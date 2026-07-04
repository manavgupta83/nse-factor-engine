# NSE Factor Engine — Methodology

This document records the formulas, design decisions, and rationale behind every signal in the pipeline. It is the source of truth for "why does this column exist and what does it actually mean" — read the relevant section before using any column downstream.

---

## Stage 1 — Universe & Liquidity

**Script:** `universe/run_universe.py`

Builds the investable universe and fetches/maintains price history.

- **Symbol list:** loaded from `data/raw/nifty500_symbols.csv`
- **Price fetch:** via yfinance, `.NS` suffix, incremental (only fetches from the day after each symbol's last known date; full 15-month history on first run)
- **Market Cap Floor:** ≥ Rs 500 Cr (`MKTCAP_FLOOR`)
- **ADTV Floor:** ≥ Rs 10 Cr, computed as a 63-trading-day rolling mean of `close * volume` (`ADTV_FLOOR`, `ADTV_WINDOW`)
- **Outputs:**
  - `data/prices.parquet` — full OHLCV history, all symbols, deduplicated on `(symbol, date)`
  - `data/universe_metadata.parquet` — `symbol, company_name, industry, market_cap_cr`
  - `data/adtv.parquet` — full ADTV time series
  - `universe/universe_{DDMMYYYY}.parquet` — `symbol, company_name, industry, market_cap_cr, adtv_63_cr, passes_mktcap, passes_adtv, in_universe` (boolean AND of the two floors)
  - `data/last_run_date.txt` — idempotency guard; script exits immediately if already run today (IST)

**KNOWN GAP:** `in_universe` is computed here but **not consumed by any downstream stage** (Stages 2-4 operate on the full ~500-symbol list regardless of investability). Deferred to Stage 5 — see Stage 5 handover doc.

**Data quirk:** `prices.parquet` contains a rogue row (TENNIND, 2026-06-26, all OHLC = NaN) — this is why T is never resolved as `prices['date'].max()` (see T Resolution below).

---

## T Resolution (used identically by every stage from Stage 2 onward)

```python
date_counts = prices.groupby('date')['symbol'].count()
T = date_counts[date_counts >= 490].index.max()
all_dates = sorted(prices[prices['date'] <= T]['date'].unique())
T_21  = all_dates[-22]
T_252 = all_dates[-253]
```

Every stage resolves T independently from `prices.parquet` content — never from `date.today()` or any passed-in parameter. This makes each stage fully self-contained and re-runnable in isolation. The window convention throughout the pipeline is `(T-N, T]` — N excluded, T included — giving exactly N trading days.

---

## Stage 2 — Momentum Signal (Core)

**Script:** `signals/stage2/stage2_step5_assemble.py`

Self-contained: reads `data/prices.parquet` directly, resolves T itself, computes everything end-to-end. (Earlier step0-step4 scripts in the same folder were exploratory/development scripts, not part of the runtime path — `stage2_step5_assemble.py` is the only entry point.)

| Column | Formula | Window |
|---|---|---|
| `ret_12m1m` | (close[T-21] − close[T-252]) / close[T-252] | T-252 → T-21 |
| `ret_6m1m` | (close[T-21] − close[T-126]) / close[T-126] | T-126 → T-21 |
| `ret_3m1m` | (close[T-21] − close[T-63]) / close[T-63] | T-63 → T-21 |
| `vol_252` | std(daily log returns, T-252→T) × √252 | T-252 → T |
| `downside_vol_252` | std(negative daily log returns, T-252→T) × √252 | T-252 → T |
| `vol_231` | std(daily log returns, T-252→T-21) × √252 | T-252 → T-21 |
| `downside_vol_231` | std(negative daily log returns, T-252→T-21) × √252 | T-252 → T-21 |
| `simple_vol_adj_momentum` | ret_12m1m / vol_231 | — |
| `sharpe_style_momentum` | (ret_12m1m − RF) / vol_231 | — |
| `sortino_style_momentum` | (ret_12m1m − RF) / downside_vol_231 | — (extension beyond base spec: per-stock Sortino) |
| `data_quality_flag` | string, hardcoded lookup (currently: VEDL → "KI-001...") | — |

**`RF = 0.07`** is hardcoded as a placeholder Indian 10Y G-Sec rate, flagged in-code as pending a future Stage 6 G-Sec time series. Any Sharpe/Sortino-based ranking downstream is implicitly built on this static assumption.

**Output:** `signals/stage2/momentum_core_signals_{T:%d%m%Y}.parquet` (intermediate artifact, not the final merged file).

**Note:** code comment in this script reads `"in_universe merge deferred to Stage 4"` — never acted on; see Stage 1 Known Gap above.

---

## Stage 3 — Momentum Quality (Path) + Industry/RS Extension

**Script:** `signals/stage3/stage3_assemble.py`

Self-contained: reads `data/prices.parquet`, `data/universe_metadata.parquet`, and Stage 2's output file (path built from T). Imports seven metric modules from `signals/stage3/metrics/`.

**Base spec columns** (per factor master list, Momentum Quality — Path):

| Column | Description |
|---|---|
| `fip_score` | Frog-in-Pan score: sign(12M-1M return) × (% negative days − % positive days). Lower = smoother path = higher quality |
| `pct_pos_days` | % of days with positive return in formation period |
| `pct_neg_days` | % of days with negative return in formation period |
| `smoothness` | Fraction of positive weeks over the lookback window |
| `proximity_52w_high` | Current price / 52-week high |
| `residual_momentum` | Idiosyncratic return after stripping market beta and sector return |
| `rm_r2`, `rm_n_obs` | Diagnostics for the residual momentum regression (R², observation count) |

**Extension columns** (beyond the original factor master list — confirmed deliberate addition during this project, not in the PDF spec):

| Column | Description |
|---|---|
| `industry_cum_ret` | Cumulative industry-level return over the formation window |
| `industry_rank` | Stock's industry's rank vs other industries |
| `weinstein_stage2` | Weinstein stage analysis classification (trend-following overlay) |
| `rs_excess_ret` | Relative strength excess return vs benchmark/peer set |
| `rs_rank_500` | Relative strength rank across the 500-symbol universe |

Formation window for most metrics: `T-252 → T-21` (same as Stage 2's vol_231 window).

**Outputs:**
- `signals/stage3/momentum_quality_signals_{T:%d%m%Y}.parquet` (intermediate)
- `signals/final/momentum_signals_final_{T:%d%m%Y}.parquet` — **this is the merged Stage2+Stage3 file that Stage 4 reads and extends.** Stage 3 is what first creates the "final" filename; Stage 4 does not create it from scratch.

---

## Stage 4 — Entry Quality Filters

**Source:** Factor master list, Section 04 (Capitalmind / Gray). Filters, not ranking signals — they gate entry quality, they do not score or rank.

**Scripts:** `signals/stage4/metrics/{stpb,volume_confirmation,daily_return_magnitude}.py`, assembled by `signals/stage4/stage4_assemble.py`.

**Output:** appends 12 columns in place to `signals/final/momentum_signals_final_{T:%d%m%Y}.parquet` (filename derived dynamically from resolved T via glob match against existing files — never hardcoded; an earlier draft hardcoded the date and was caught and fixed before deployment, see Cross-cutting notes below). Original pre-Stage-4 file is backed up (`_pre_stage4_backup.parquet` suffix) before being overwritten in place.

### 4.1 Short-Term Price Behaviour (Capitalmind)

"Recent price action at point of entry. Reduces probability of buying at peak before reversal." Five sub-metrics, all describing the T-21→T window (and a tighter T-7→T window) — i.e. exactly the skip-month window that Stage 2/3 deliberately excluded from the core momentum signal.

| Column | Formula | Window |
|---|---|---|
| `stpb_ret_21d` | (close[T] − close[T-21]) / close[T-21] | T-21 → T |
| `stpb_ret_7d` | (close[T] − close[T-7]) / close[T-7] | T-7 → T |
| `stpb_zscore_21d` | stpb_ret_21d / vol_231 | T-21 → T (return) ÷ T-252→T-21 (vol) |
| `stpb_zscore_7d` | stpb_ret_7d / vol_231 | T-7 → T (return) ÷ T-252→T-21 (vol) |
| `stpb_ma_distance_21d` | (close[T] − MA_21[T]) / MA_21[T] | T-21 → T, strictly 21 trading days (T-21 excluded, T included) |

**Design decision — `stpb_zscore_*` denominator:** `vol_231` (Stage 2's matched-window realised volatility, T-252→T-21) was deliberately chosen over a fresh same-window volatility calculation. Rationale: a true z-score using the *current* window's own volatility as the denominator would self-dampen exactly the spike the metric is meant to detect — a sharp recent move inflates its own measuring stick. Using the prior, non-overlapping `vol_231` baseline avoids this and answers "how big is this move relative to the stock's *normal* behaviour," which is the economically meaningful question for peak-buying risk.

**IMPORTANT — these are NOT statistical z-scores.** Numerator window (T-21→T or T-7→T) and denominator window (vol_231, T-252→T-21) do not overlap. Do not assume ~N(0,1) distribution or apply z-score conventions (e.g. ±3 clipping) downstream. Treat as a normalized ratio, named for convenience.

**Null propagation:** `stpb_zscore_21d`/`stpb_zscore_7d` carry forward the same nulls present in Stage 2's `vol_231` (short-history symbols — 22 nulls observed at T=2026-06-25). `stpb_ret_21d`, `stpb_ret_7d`, `stpb_ma_distance_21d` have zero nulls (pure price-based, no Stage 2 dependency).

### 4.2 Volume Confirmation (Capitalmind)

"Recent volume relative to longer-term average. Rising price on rising volume = valid momentum."

| Column | Formula | Window |
|---|---|---|
| `vol_ratio_21_252` | avg_volume(T-21→T) / avg_volume(T-252→T) | 21d recent ÷ 252d longer-term |
| `volume_price_pos_move_confirmed` | boolean: (stpb_ret_21d > 0) AND (vol_ratio_21_252 > 1.2) | — |

**Design decision — price leg:** `stpb_ret_21d` (Section 4.1) reused as the price leg, rather than `ret_3m1m` from Stage 2, to keep the price and volume legs measuring the same 21-trading-day window. Pairing a 21d volume ratio against a ~2-month price return would introduce a window mismatch.

**Design decision — threshold:** vol_ratio > 1.2 (not > 1.0) chosen to require a meaningful volume increase, filtering day-to-day noise around the 1.0 baseline.

**IMPORTANT — one-sided flag.** `volume_price_pos_move_confirmed` is a positive-confirmation flag only (bullish entries). `False` does not imply bearish — it covers both "rising price, flat/falling volume" and "falling price" cases indiscriminately. A high `vol_ratio_21_252` with a negative `stpb_ret_21d` (e.g. AARTIIND at T=2026-06-25: ratio 1.47, return −5.1%, flag False) is "high volume on a decline," a real and distinct pattern not separately characterized by this column. Stage 5 should not infer bearishness from `False`.

**Known issue — KI-002 (VEDL):** see `docs/KNOWN_ISSUES.md`. VEDL's 252d volume baseline spans the 2026-04-30 stock split, producing a structural ~3x volume-regime shift mid-window (unadjusted split, same root cause as KI-001). `vol_ratio_21_252` for VEDL (1.71 at T=2026-06-25) is not reliably interpretable until the pre-split tail rolls out of the 252d window or the price data is repatched.

### 4.3 Absolute Daily Return Magnitude — Lottery Classifier (Gray)

"High average daily move = lottery characteristic. Penalise erratic movers." Implemented as a bucketed day-count classifier over a 63-trading-day window (T-63→T), using simple daily returns (close[t]/close[t-1] − 1, not log returns).

**Buckets** (count of days in window where |daily return| falls in range):

| Column | Range |
|---|---|
| `days_bw_15_20perc` | \|ret\| ≥ 15% (no upper cap — 20%+ moves also counted here) |
| `days_bw_10_15perc` | 10% ≤ \|ret\| < 15% |
| `days_bw_5_10perc` | 5% ≤ \|ret\| < 10% |
| `days_bw_2_5perc` | 2% ≤ \|ret\| < 5% |

**Classification** (`lottery_class`, cascade evaluated top to bottom, first match wins):

| Condition | Label |
|---|---|
| days_bw_15_20perc > 2 | EXTREME LOTTERY |
| days_bw_15_20perc > 0 | LOTTERY |
| days_bw_10_15perc > 0 | BORDER_LOTTERY |
| days_bw_5_10perc > 0 | CAUTIOUS |
| days_bw_2_5perc > 0 | ALRIGHT |
| (none of the above) | BORING |

**IMPORTANT — asymmetric threshold, confirmed intentional.** Only the top tier (EXTREME LOTTERY) requires more than 2 qualifying days; every other tier triggers on a single occurrence. One isolated extreme day is enough to classify a stock as LOTTERY, but it takes 3+ such days to escalate to EXTREME LOTTERY.

**Window construction note:** the 63-day return series requires 64 trading days of close prices (T-64→T); the anchor day (T-64) itself produces no return observation and is excluded from bucket counts, leaving exactly 63 return-days per symbol.

**Known limitation — VEDL, not logged as a known issue (explicit decision).** VEDL's `lottery_class` = LOTTERY (at T=2026-06-25) is driven in part by a single contaminated day: the 2026-04-30 split shows as a ~65% single-day "return" (close ₹773.60 → ₹271.55), registering in `days_bw_15_20perc`. Same root cause as KI-001/KI-002 but explicitly decided NOT to be patched or separately logged — VEDL's lottery classification should be read with this in mind, output left as computed.

**Empirical result at T=2026-06-25:** 0 symbols in EXTREME LOTTERY, 36 in LOTTERY, 66 in BORDER_LOTTERY, 340 in CAUTIOUS, 58 in ALRIGHT, 0 in BORING. (Distribution will shift run to run as T advances; this is a point-in-time snapshot, not a fixed expectation.)

### Stage 4 Cross-cutting notes

- All Stage 4 windows use the convention `(T-N, T]` — verified explicitly for the 21d and 252d windows during implementation. An earlier draft of the MA_21 window incorrectly included T-21, inflating the window to 22 days; caught and fixed before deployment.
- `data_quality_flag` (Stage 2 column) is reused as-is for VEDL; no new Stage-4-specific flag column was added.
- The Stage 4 assembler derives both T and the input/output signals filename dynamically from `prices.parquet` and a glob match — no hardcoded dates. An earlier draft hardcoded the signals filename (`momentum_signals_final_25062026.parquet`); this was identified as a real bug (would silently fail or misbehave on any future T) and fixed before deployment — see "Master Pipeline Orchestration" section for the broader context this was caught in.

---

## Stage 5 — Ranking & Selection

**Scripts:** `signals/stage5/metrics/in_universe.py`, `signals/stage5/metrics/cross_sectional_rank.py`, `signals/stage5/metrics/fip_rerank.py`, `signals/stage5/stage5_assemble.py`

First stage to close the `in_universe` gap flagged since Stage 1. Closes it, then ranks the investable universe and re-ranks by FIP quality. Output is **additive only** — all 500 original rows and all 38 pre-existing columns are preserved unchanged; Stage 5 adds 11 new columns and writes nothing else away.

### 5.1 in_universe merge (prerequisite gate)

Loads `universe/universe_{run_date}.parquet` and merges `in_universe`, `passes_mktcap`, `passes_adtv` onto the signals file.

**Filename vs data-date convention (locked 2026-06-30) — applies pipeline-wide, not just Stage 5:** every dated output file (`universe_*.parquet`, `momentum_signals_final_*.parquet`, etc.) is named using **RUN_DATE** — the IST calendar date the pipeline actually executed — **not T**. T is recorded separately as the `as_of_date` column inside each file and can lag RUN_DATE (e.g. a run on June 30 can produce `as_of_date = 2026-06-29` if June 30's full data wasn't yet available at fetch time). Filename tells you *when this ran*; `as_of_date` tells you *what trading day the data represents*. The two are never assumed to match.

**Matching rule:** `in_universe.py` requires an **exact** RUN_DATE match between the universe file and the signals file (both taken from filename, not T). No "nearest" or "most recent before" fallback — if no exact match exists, the script asserts and stops rather than guessing. In the normal orchestrated path (`run_pipeline.py`), Stage 1 and Stages 2-5 run back-to-back under one invocation and stamp the same RUN_DATE by construction, so this is automatic. Running Stage 5 standalone on a day Stage 1 was skipped is the one scenario where this could fail — by design.

**Row retention — non-investable symbols kept, not dropped (locked 2026-06-30):** earlier design intent was to filter the final file down to `in_universe == True` only. Revised: all 500 original symbols are retained in the final output for auditability. Symbols where `in_universe == False` get `in_universe`/`passes_mktcap`/`passes_adtv` populated as normal, but every Stage 5 rank/FIP column is `NaN` for them — they're never fed into the ranking computation, just carried through unranked.

**Verified at T=2026-06-30 (run_date=30062026):** 500 signal symbols, 500 universe symbols, full match, 0 unmatched. 496 `in_universe=True` (all 500 clear the market cap floor; 4 fail the ADTV floor).

### 5.2 Cross-Sectional Rank (Gray)

"Rank all stocks in universe... select top decile" — implemented as 4 **independent, non-combined** rank columns, computed only on the `in_universe == True` subset (ranking against non-investable symbols would distort percentile cutoffs):

| Column | Ranks on |
|---|---|
| `rank_ret_12m1m` | `ret_12m1m` |
| `rank_simple_vol_adj_momentum` | `simple_vol_adj_momentum` |
| `rank_sharpe_style_momentum` | `sharpe_style_momentum` (covers PDF's "Sharpe Ratio Rank") |
| `rank_sortino_style_momentum` | `sortino_style_momentum` |

Convention: rank 1 = best (highest value, descending), ties via `method='min'`, computed independently per metric — no averaging or combining across the 4.

**Decile sizing — design conflict identified and resolved (locked 2026-06-30):** the PDF's literal spec ("top decile... 90th percentile") and its literal "top 100 → final 50" numbers are mutually inconsistent once sized against the actual investable universe (~496, not 500) — a true 10% decile is ~45-49 names, smaller than both 100 and 50. **Resolved by dropping the percentile/decile framing entirely** in favor of the PDF's explicit fixed numbers: rank by return, take a fixed top 100 (not scaled to universe size), then FIP re-rank narrows to (up to) 50. The word "decile" in the PDF is treated as superseded by the literal 100/50 figures it also specifies.

**Verified at T=2026-06-30:** 496 in-universe symbols ranked on all 4 metrics. 21 symbols (same 21 across all 4 metrics — all `ret_12m1m`-dependent) have null inputs and rank as NaN; likely recent listings without full 252-day history.

### 5.3 FIP Re-Rank (Gray)

"Within top decile by return, sort by FIP score" — implemented as 4 **parallel, independent** FIP-rerank tracks, one per Cross-Sectional Rank metric (no cross-metric combination):

For each of the 4 metrics: take that metric's own top 100 (`rank_<metric> <= 100`), then rank `fip_score` ascending within that pool of 100 only (lower/more-negative FIP = better = rank 1, per the Stage 3 FIP convention). Output columns: `rank_fip_ret_12m1m`, `rank_fip_simple_vol_adj_momentum`, `rank_fip_sharpe_style_momentum`, `rank_fip_sortino_style_momentum`. Each is `1..100` for symbols inside that metric's top-100 pool, `NaN` for everyone else (including in-universe symbols that simply didn't make that metric's top 100).

No combined "final 50" column exists in production output — per explicit decision, the PDF's "final 50 from top 100" step is left as a downstream/manual selection step, not materialized as a flag in the assembled file.

**Verified at T=2026-06-30:** all 4 pools filled at exactly 100/100 (no shortfall), FIP-ranked 1-100. Sanity check passed: `rank_fip_ret_12m1m == 1` matched the actual minimum `fip_score` within that 100-name pool.

### 5.4 Seasonality Filter — explicitly out of scope

PDF spec: "Avoid January rebalance. Momentum weakest in January due to tax-loss selling reversal." **Not implemented.** Explicitly descoped per user decision (2026-06-30) — manual judgment will be applied in January rather than an automated flag or skip. No `seasonality_warning` column or equivalent exists in the Stage 5 output. If revisited later, prior design discussion (not implemented) had converged on: always compute and write a selection regardless of month (never hard-skip), with January's IST run-month (not T's month) driving any flag.

### Stage 5 Cross-cutting notes

- **Assembler row-count invariant:** `stage5_assemble.py` asserts the output row count equals the input row count exactly, and that the symbol set is unchanged — guards against the earlier (corrected) design where non-investable symbols were silently dropped.
- **Determinism verified:** re-running `run_pipeline.py` end-to-end against the same T produced byte-identical Stage 5 output (all 11 new columns, all 500 symbols) on a second invocation the same day.
- **`testing_shortlist.py`** (`signals/stage5/testing_shortlist.py`) — **experimental, not a production pipeline component, not part of `stage5_assemble.py` or `run_pipeline.py`.** A standalone gate-then-composite-score prototype combining `in_universe`, `weinstein_stage2`, `stpb_ret_21d`, `stpb_ma_distance_21d`, `lottery_class`, `proximity_52w_high` as hard gates, then a weighted composite (30% momentum rank avg / 20% FIP / 20% RS / 15% industry / 15% proximity) to produce a top-20 shortlist. Built to explore the "rank-1-on-everything but structurally broken" failure mode surfaced by NATIONALUM (elite `ret_12m1m`/`rs_rank_500`/FIP ranks, but `weinstein_stage2=False` and `stpb_ret_21d=-19.8%` — a rolled-over former leader). **Explicitly flagged as needing backtest validation before any thresholds, weights, or the gate/score split itself are trusted** — see Stage 6 handover for what to test.

---

## Master Pipeline Orchestration

**Script:** `run_pipeline.py` (repo root)

Sequences Stage 1 → 2 → 3 → 4 → 5 as subprocesses, in order, calling each stage's existing entry-point script unmodified (except `universe/run_universe.py`'s output filename format, see below). Does not reimplement any stage's internal logic.

**Entry points invoked, in order:**
1. `universe/run_universe.py` (Stage 1)
2. `signals/stage2/stage2_step5_assemble.py` (Stage 2)
3. `signals/stage3/stage3_assemble.py` (Stage 3)
4. `signals/stage4/stage4_assemble.py` (Stage 4)
5. `signals/stage5/stage5_assemble.py` (Stage 5, added 2026-06-30)

**Design decisions:**

- **Timezone handling:** Stage 1's `run_universe.py` uses `END_DATE = date.today()` internally, which resolves to the EC2 server's local timezone — confirmed UTC, not IST. Rather than modify `run_universe.py`, the master script sets `TZ=Asia/Kolkata` only on Stage 1's subprocess environment, so `date.today()` resolves correctly to the IST calendar date without touching Stage 1's code. No other stage needed this — Stages 2-4 all derive their working date from `prices.parquet` content via T-resolution, not `date.today()`.

- **`universe/run_universe.py` filename format changed:** `universe_{YYYYMMDD}.parquet` → `universe_{DDMMYYYY}.parquet`, for consistency with every other dated output in the pipeline. This is the one direct edit made to a pre-existing Stage 1-4 script. `data/failed_symbols_{YYYYMMDD}.csv` deliberately left as YYYYMMDD — internal artifact, only read programmatically.

- **Stage 1 failure threshold:** if Stage 1 finishes with ≥5 symbols still failing after its own internal retries, the pipeline halts before Stage 2 rather than proceeding on a meaningfully incomplete universe. Below threshold, proceeds with a logged warning. `FAILED_SYMBOL_HALT_THRESHOLD = 5` is a judgment call, not derived from formal analysis.

- **No T is computed or passed by the master script.** Each stage independently resolves its own T from `data/prices.parquet`. Deliberate choice to keep each stage fully self-contained and re-runnable in isolation.

- **Live output streaming:** stage subprocesses run via `subprocess.Popen` with unbuffered (`-u`), line-buffered output, streamed live — not `subprocess.run(capture_output=True)`, which would buffer everything until each stage's completion. Matters most for Stage 1 (15+ minute runtime with per-symbol progress prints).

- **Logging:** every run writes a timestamped log to `logs/master_run_{YYYYMMDD_HHMMSS}.log`, capturing all stdout/stderr from every stage plus the master script's own status messages.

- **Halt-on-failure:** any stage returning non-zero exit code immediately halts the pipeline, with the failing stage and exit code logged.

**Resolved gap (was open at time of original writing):** `in_universe` (Stage 1) is now applied in Stage 5 — see Stage 5 section above. Stages 2-4 still operate on the full ~500-symbol universe by design; Stage 5 is where filtering scope first applies.

**Verified:** end-to-end run on 2026-06-30 (IST), Stage 1 through Stage 5, produced `signals/final/momentum_signals_final_30062026.parquet` — 500 rows, 49 columns (38 pre-Stage-5 + 11 new), `as_of_date` = 2026-06-30. Re-run same day reproduced byte-identical output.

---

## Repository & Infrastructure Notes

- **Repo:** `manavgupta83/nse-factor-engine` on GitHub (private)
- **`.gitignore`** excludes all `.parquet`, raw `.csv` data, and `.log` files — only code, docs, and small state files (`data/last_run_date.txt`) are version-controlled
- **Auth:** PAT (classic token) used for push, entered at prompt only — never persisted to disk via `credential.helper store`. Token set with a 7-day expiry during this session; will need regeneration after expiry
- **EC2 server timezone is UTC** — relevant any time new date-sensitive logic is added; Stage 1 already handles this via the master script's `TZ=Asia/Kolkata` override, but any new logic should not assume server-local `date.today()` reflects IST
- **Git was not pre-installed** on the EC2 box; installed via `sudo dnf install -y git` (Amazon Linux 2023)

---

## Open Items / Known Gaps (not yet resolved, carried forward)

1. ~~`in_universe` filtering~~ — **RESOLVED in Stage 5** (2026-06-30). See Stage 5 section above.
2. **Old dated final files accumulate indefinitely** in `signals/final/` — no archiving/cleanup policy decided yet. Stage 5 backups (`_pre_stage5_backup.parquet`) add to this; same open question.
3. **`RF = 0.07`** (Stage 2) is a hardcoded placeholder pending a future Stage 6 G-Sec time series. Stage 5's `rank_sharpe_style_momentum` / `rank_sortino_style_momentum` inherit this assumption.
4. **VEDL lottery-classifier contamination** (Section 4.3) — explicitly left unpatched and undocumented as a formal known issue, per user decision.
5. **No "final 50" selection materialized.** Stage 5 produces 4 parallel top-100-then-FIP-ranked tracks but no single combined/intersected "these are the 50 stocks to buy" output. This is intentional per current scope, but means Stage 5's output is still an intermediate ranking artifact, not a portfolio.
6. **`testing_shortlist.py` thresholds/weights are unvalidated.** Gate thresholds (`stpb_ret_21d > -5%`, `proximity_52w_high > 0.80`, etc.) and composite weights (30/20/20/15/15) were chosen by judgment during this session, explicitly pending backtest validation. See Stage 6 handover for the validation plan.
7. **No exit strategy exists yet.** Per PDF Section 07 (Portfolio Construction), exit logic ("Weekly Review with Exit Rules," drawdown/regime triggers) is scoped for a future stage, not Stage 5. Needs "current holdings" as an input Stage 5 doesn't have — structurally a different computation (point-in-time retention check vs cross-sectional ranking), not a Stage 5 extension.
8. **Methodology doc previously went stale relative to code** (discovered and corrected during Stage 5 build, 2026-06-30) — an earlier version of this file incorrectly described Stage 3's `residual_momentum` and 4 extension metrics as un-built TODOs, when the actual `stage3_assemble.py` on GitHub had them fully implemented. Verify against actual code (not just this doc) before trusting column lists, especially after long gaps between sessions.

---

## Stage 6 — Backtesting

**Design session:** 2026-07-01. All decisions below were locked in the design chat before any code was written. The coding chat should treat every decision here as final unless explicitly reopened with the user.

---

### 6.1 Design Philosophy

- Backtest is **self-contained** under `backtest/` — zero dependency on production `data/prices.parquet`, `universe/`, or `signals/`
- **Survivorship bias is accepted** and documented as a known caveat. Getting 10yr survivorship-bias-free NSE data is impractical. Since all 25 strategy cells share the same bias, **relative rankings between cells are valid**; absolute return figures are not. This must be stated clearly in any output or report.
- **RF = 0.07 static** across the full 10-year backtest period — consistent with Stage 2, justified because all 25 cells share the same constant (comparative validity preserved).

---

### 6.2 Strategy Grid

Full factorial cross of 5 gate variants × 5 score variants = **25 strategy cells**. Every combination is run (no pruning). Each cell is identified by `cell_id` = `{gate_variant}_{score_variant}` (e.g. `G2_C4`).

**Fixed parameters across all cells:**
- Portfolio size N = 25
- Tiebreaker at N cutoff = `proximity_52w_high` descending (config parameter — swappable without redesign)
- Precondition (applies under every cell, not a variant): `in_universe = True`

---

### 6.3 Gate Variants (G-axis)

| ID | Gates Applied | Hypothesis Being Tested |
|---|---|---|
| G1 | `in_universe` only | No additional gating adds value |
| G2 | + `weinstein_stage2 = True` | Trend-stage confirmation is the dominant disqualifier |
| G3 | + `stpb_ret_21d > -5%` AND `proximity_52w_high > 0.80` | Recent reversal + distance-from-high is the dominant disqualifier |
| G4 | + `lottery_class not in {LOTTERY, BORDER_LOTTERY, EXTREME LOTTERY}` | Erratic-mover exclusion is the dominant disqualifier |
| G5 | All of the above combined | Combined gating beats any single-axis gate |

G5 is the full production gate set from `testing_shortlist.py`.

---

### 6.4 Score Variants (C-axis)

| ID | Scoring Formula | Hypothesis Being Tested |
|---|---|---|
| C1 | `rank_ret_12m1m` alone | Raw 12M-1M return rank is sufficient; everything else is noise |
| C2 | `rank_fip_ret_12m1m` alone | Path quality (FIP re-rank) beats raw magnitude as primary driver |
| C3 | Average of `rank_sharpe_style_momentum` + `rank_sortino_style_momentum` | Risk-adjusting the signal (not gating, not FIP) is what matters |
| C4 | Equal-weight composite: 20% each of (avg 3-momentum-ranks, FIP rank, RS rank, industry rank, proximity rank) | The specific 30/20/20/15/15 weights in C5 don't matter |
| C5 | Weighted composite: 30% avg(3 momentum ranks) / 20% FIP / 20% RS / 15% industry / 15% proximity | Current production weights (testing_shortlist.py) |

C5 is the production scoring formula from `testing_shortlist.py`.

**Note on C2:** `rank_fip_ret_12m1m` is NaN for symbols outside the top-100 pool of `rank_ret_12m1m`. When C2 is used as the score, only symbols with a non-NaN FIP rank are eligible for selection — this pool is at most 100 in-universe symbols. This is correct and expected behaviour, not a bug.

---

### 6.5 Data Layer

| Item | Detail |
|---|---|
| Source | Yahoo Finance (`.NS` suffix for NSE symbols) |
| Symbols | ~1000 (list provided by user before coding begins) |
| File | `backtest/data/prices_backtest.parquet` — OHLCV, all symbols, full 10yr history |
| Benchmark | Nifty 500 price return index (`^CNTX` on Yahoo Finance), stored in `backtest/data/benchmark/nifty500_weekly.parquet` |
| Period | 2015–2025 (10 years) |
| Effective rebalance points | ~470 (first ~252 trading days consumed as warmup for signal computation) |
| Decoupling | `prices_backtest.parquet` is fetched once in bulk — Stage 1 (incremental daily fetch) is NOT cloned into backtest |

---

### 6.6 Pipeline Clone

Stages 2–5 signal logic is cloned into `backtest/pipeline/` and adapted for **historical T parametrisation** (each run takes a specific Friday date as T rather than resolving T from latest available data). The clone runs once per Friday in the backtest period (~470 times), producing one signals file per date in `backtest/signals/historical/signals_{DDMMYYYY}.parquet`.

Each historical signals file has the same 49-column schema as production `momentum_signals_final_{DDMMYYYY}.parquet`.

`backtest/pipeline/run_historical_pipeline.py` orchestrates the loop over all ~470 Fridays.

---

### 6.7 Weekly Rebalance Mechanics

Rebalance fires every **Friday at close price**. If Friday is a market holiday, use the last available trading day that week.

#### Step 1 — Pre-Rebalance State (Friday close, before trades)
- `holdings` = {symbol: shares} carried from prior week
- `cash_pool_carryover` = idle cash from prior weeks
- `market_value_holdings` = Σ(shares × Friday close price) for all held symbols
- `portfolio_value_pre` = `market_value_holdings + cash_pool_carryover`

#### Step 2 — Signal Computation
- Load Friday's signals file from `backtest/signals/historical/`
- Apply gate variant → surviving pool
- Apply score variant → ranked pool
- Select top-25 by score (tiebreak: `proximity_52w_high` descending)
- Derive: `exits` = in holdings but not in new top-25; `entries` = in new top-25 but not in holdings; `held` = in both

#### Step 3 — Sell
- Sell all exit symbols at Friday close
- `sell_proceeds` = Σ(shares × Friday close price) for exit symbols
- `available_cash` = `cash_pool_carryover + sell_proceeds`

#### Step 4 — Allocation Cap Computation
- `avg_held_value` = Σ(market value of held symbols) / count(held)
- `cap_per_entry` = `min(available_cash / num_entries, avg_held_value)`
- **Special case — week 1 or full turnover (no held positions):** `cap_per_entry = available_cash / num_entries` (no cap applied; full cash deployment)

#### Step 5 — Buy
- Buy each entry symbol at Friday close, spending exactly `cap_per_entry` per symbol
- `cash_deployed` = `cap_per_entry × num_entries`

#### Step 6 — Post-Rebalance State
- `cash_pool_after` = `available_cash - cash_deployed`
- `portfolio_value_post` = `market_value_of_held_positions + cash_deployed + cash_pool_after`
- **Sanity check:** `portfolio_value_post` = `portfolio_value_pre` on rebalance day (no value created/destroyed by the rebalance itself — price moves occur between Fridays, not during execution) ✓

#### Key design decisions
- **Incremental rebalance** — only trade exits and entries; held positions untouched between rebalances
- **Cash drag is modelled** — undeployed cash tracked in `cash_pool_after`, earns no return
- **No borrowing** — `cash_deployed` can never exceed `available_cash`; allocation capped as above
- **Equal-weight target** — new entrants receive equal allocation (capped), but held positions drift freely with price between rebalances; portfolio is not forced back to equal-weight on held names

---

### 6.8 Performance Metrics

Computed per strategy cell (25 cells) and for the Nifty 500 benchmark in parallel.

**Weekly return series** is the base input for all metrics:
`weekly_return[t]` = `(portfolio_value_post[t] - portfolio_value_post[t-1]) / portfolio_value_post[t-1]`

| Metric | Formula | Notes |
|---|---|---|
| CAGR | `(final_portfolio_value / initial_capital)^(1/10) - 1` | 10-year annualisation |
| Sharpe | `(mean_weekly_return × 52 - RF) / (std_weekly_return × √52)` | RF = 0.07 static |
| Sortino | `(mean_weekly_return × 52 - RF) / (downside_std_weekly × √52)` | downside_std uses negative weeks only |
| Max DD | Largest peak-to-trough drop in cumulative portfolio value series | — |
| DD Recovery | Weeks from trough to recovery of prior peak value | — |
| Deflated Sharpe | Harvey & Liu (2015) multiple-testing adjustment for 25 strategies | **Flag only** — `sharpe_significant: True/False`. Does not exclude or penalise cells. |
| Weeks ≥ 0% | Count of weeks with non-negative return | — |
| Weeks -5% to 0% | Count of weeks with return in (-5%, 0%) | — |
| Weeks -10% to -5% | Count of weeks with return in (-10%, -5%) | — |
| Weeks -20% to -10% | Count of weeks with return in (-20%, -10%) | — |
| Weeks < -20% | Count of weeks with return < -20% | — |
| Alpha | `portfolio CAGR - benchmark CAGR` | Simple excess return, not regression-based |

**Annualisation convention:** `×52` / `×√52` throughout (weekly data). Not `×252` — we track portfolio weekly, not daily. This means Sharpe/Sortino figures are **not directly comparable to mutual fund fact sheets** (which use daily NAV × √252). Acceptable given the purpose is inter-strategy comparison, not external benchmarking.

---

### 6.9 Benchmark Mechanics

| Item | Decision |
|---|---|
| Index | Nifty 500 price return (`^CNTX` on Yahoo Finance) |
| Return type | Price return only (no dividend reinvestment) |
| Valuation point | Same Friday close as portfolio rebalance |
| Outperformance metric | `Alpha = portfolio CAGR - Nifty 500 CAGR` (simple, not Jensen's Alpha) |
| Same metrics computed | CAGR, Sharpe, Sortino, Max DD, DD Recovery, all DD weekly buckets |

---

### 6.10 Folder Structure

```
backtest/
│
├── data/
│   ├── prices_backtest.parquet          # 10yr OHLCV, ~1000 symbols (Yahoo Finance)
│   ├── universe_backtest.parquet        # symbol metadata (static snapshot)
│   └── benchmark/
│       └── nifty500_weekly.parquet      # Nifty 500 Friday closes, 2015-2025
│
├── pipeline/                            # cloned Stage 2-5 logic, parametrised for historical T
│   ├── stage2_momentum.py
│   ├── stage3_quality.py
│   ├── stage4_filters.py
│   ├── stage5_rank.py
│   └── run_historical_pipeline.py       # loops over all ~470 Fridays
│
├── signals/
│   └── historical/                      # one parquet per Friday (same 49-col schema as production)
│       └── signals_{DDMMYYYY}.parquet
│
├── strategies/
│   ├── config.py                        # G1-G5, C1-C5 definitions; N=25; tiebreaker; RF=0.07
│   ├── gates.py                         # gate variant functions
│   ├── scores.py                        # score variant functions
│   └── engine.py                        # (gate_variant, score_variant) → top-25 for a signals file
│
├── simulation/
│   ├── portfolio.py                     # portfolio state + rebalance mechanics (Step 1-6 above)
│   └── run_simulation.py               # loops ~470 Fridays × 25 cells → weekly return series
│
├── metrics/
│   └── compute_metrics.py              # all metrics in 6.8; benchmark parallel computation
│
├── results/
│   ├── backtest_results_{DDMMYYYY}.parquet         # 25 rows × all summary metrics
│   ├── backtest_weekly_returns_{DDMMYYYY}.parquet  # ~470 rows × 27 cols (25 cells + benchmark + date)
│   └── backtest_portfolio_activity_{DDMMYYYY}.parquet  # ~290k rows: position-level audit trail
│
└── run_backtest.py                      # master orchestrator: pipeline → simulate → metrics → results
```

---

### 6.11 Output File Schemas

All three files dated with the backtest **run date** (IST), same convention as production pipeline. Produced once per full backtest run, not per rebalance week.

**`backtest_results_{DDMMYYYY}.parquet`** — 25 rows

| Column | Type | Description |
|---|---|---|
| `cell_id` | str | e.g. `G2_C4` |
| `gate_variant` | str | G1–G5 |
| `score_variant` | str | C1–C5 |
| `cagr` | float | Annualised return |
| `sharpe` | float | Annualised Sharpe |
| `sortino` | float | Annualised Sortino |
| `max_dd` | float | Max peak-to-trough (negative) |
| `dd_recovery_weeks` | int | Weeks to recover from max DD |
| `deflated_sharpe` | float | Harvey-Liu adjusted Sharpe |
| `sharpe_significant` | bool | True if deflated Sharpe passes threshold |
| `alpha` | float | Portfolio CAGR - Benchmark CAGR |
| `weeks_positive` | int | Weeks with return ≥ 0% |
| `weeks_dd_0_5` | int | Weeks with return in (-5%, 0%) |
| `weeks_dd_5_10` | int | Weeks with return in (-10%, -5%) |
| `weeks_dd_10_20` | int | Weeks with return in (-20%, -10%) |
| `weeks_dd_gt20` | int | Weeks with return < -20% |
| `benchmark_cagr` | float | Nifty 500 CAGR same period |
| `total_weeks` | int | Effective rebalance weeks |
| `initial_capital` | float | Starting capital |
| `rf_rate` | float | 0.07 (static) |

**`backtest_weekly_returns_{DDMMYYYY}.parquet`** — ~470 rows

| Column | Type | Description |
|---|---|---|
| `friday_date` | date | Rebalance date |
| `G1_C1` ... `G5_C5` | float | Weekly return per cell (25 columns) |
| `benchmark` | float | Nifty 500 weekly return |

**`backtest_portfolio_activity_{DDMMYYYY}.parquet`** — ~290,000 rows

| Column | Type | Description |
|---|---|---|
| `friday_date` | date | Rebalance date |
| `cell_id` | str | e.g. `G2_C4` |
| `symbol` | str | Stock symbol |
| `action` | str | `BUY` / `SELL` / `HOLD` |
| `shares` | float | Shares transacted or held |
| `price` | float | Friday close price |
| `value` | float | `shares × price` |
| `portfolio_value` | float | Total portfolio value post-rebalance |
| `cash_pool` | float | Cash pool post-rebalance |

---

### 6.12 Known Caveats (Stage 6 specific)

1. **Survivorship bias:** `prices_backtest.parquet` contains only symbols available today. Delisted, merged, or de-indexed stocks from 2015–2025 are absent. Absolute return figures are overstated. Relative rankings between cells remain valid (all cells share identical bias).
2. **RF = 0.07 static:** actual Indian risk-free rate varied 4–8% across 2015–2025. Sharpe/Sortino figures are not comparable to any external source using a time-varying RF.
3. **No transaction costs:** brokerage, STT, and impact cost are explicitly excluded from this round. Returns are gross of all friction. Transaction cost modelling deferred to a later stage.
4. **No slippage model:** execution at Friday close price exactly. Real execution would be slightly above/below close.
5. **Weekly portfolio valuation:** Sharpe/Sortino use ×52/×√52 annualisation, not ×252. Not comparable to mutual fund fact sheet figures (which use daily NAV).


---

## Stage 6 — As-Built Notes (append to existing Stage 6 section)

**Completed:** 2026-07-02. All 6 phases production-verified.

---

### 6.A Deviations from Original Spec

#### Benchmark Ticker
- **Spec:** `^CNTX` (Nifty 500)
- **As built:** `^CRSLDX` — `^CNTX` returned HTTP 404 on Yahoo Finance. `^CRSLDX` confirmed as correct Nifty 500 ticker.

#### Backtest Period
- **Spec:** 2015–2025, ~470 Fridays
- **As built:** 2016-01-15 → 2026-06-19, **511 valid Fridays**
- Price data extended to 2026-06-24 to include current year.
- First valid T = 2016-01-15 (252 trading days of warmup consumed from 2015-01-01 start).

#### Universe
- **Spec:** ~1000 symbols
- **As built:** 991 symbols successfully fetched. 8 failed (LTIM, GSPL, AKZOINDIA, SEQUENT, INFIBEAM, CIGNITITEC, SGLTL, SABTNL) — all delisted or renamed. Accepted per survivorship bias caveat.

#### in_universe Gate
- **Spec:** passes_mktcap AND passes_adtv
- **As built:** passes_adtv only. All 991 symbols exceed 500cr market cap floor (confirmed — minimum market cap in NIFTY_1000.csv = 2,200cr). passes_mktcap check skipped as redundant.
- **ADTV:** computed point-in-time from `prices_backtest.parquet` at each Friday T. 63-day rolling mean of close × volume / 1e7. Threshold: ≥ 10cr.

#### Industry Classification
- **Spec:** NSE industry classification (from universe_metadata.parquet)
- **As built:** yfinance `sector` field. 11 categories (vs finer NSE classification). Stored in `universe_metadata_backtest.parquet`. Coarser groupings affect residual_momentum and leading_industry signals — documented caveat, accepted for backtest.

#### Signal Columns — New Additions vs Production
Two new RS columns added in backtest pipeline (not in production as of Stage 6):
- `rs_excess_ret_mkt` = stock_cum_ret - equal_weighted_market_cum_ret (renamed from `rs_excess_ret`)
- `rs_excess_ret_industry` = stock_cum_ret - industry_cum_ret (new)
- Production pipeline update deferred to Stage 8.

#### Memory Optimisation
- Full price history (2M rows) caused OOM on t2.micro EC2 (916MB RAM).
- **Fix:** pre-slice prices to 300 trading days ending at T before passing to `compute_signals()`. 300 > 252 (longest lookback), so no signal is affected.

---

### 6.B Pipeline Architecture (as built)

Single function `compute_signals(px_window, meta, T)` in `backtest/pipeline/compute_signals.py` replicates all Stage 2–5 logic in-memory for arbitrary historical T. Called 511 times by `run_historical_pipeline.py`.

Key design: no file I/O per iteration — prices and metadata loaded once, passed as DataFrames.

---

### 6.C Signal File Schema (49 columns)

One parquet per Friday in `backtest/signals/historical/signals_{DDMMYYYY}.parquet`.

| Group | Columns |
|---|---|
| Stage 2 — Momentum Core | symbol, as_of_date, ret_12m1m, ret_6m1m, ret_3m1m, vol_252, vol_231, downside_vol_252, downside_vol_231, simple_vol_adj_momentum, sharpe_style_momentum, sortino_style_momentum |
| Stage 3 — Quality | fip_score, pct_pos_days, pct_neg_days, smoothness, proximity_52w_high, residual_momentum, rm_r2, rm_n_obs, industry_cum_ret, industry_rank, weinstein_stage2, rs_excess_ret_mkt, rs_excess_ret_industry, rs_rank_500 |
| Stage 4 — Entry Filters | stpb_ret_21d, stpb_ret_7d, stpb_zscore_21d, stpb_zscore_7d, stpb_ma_distance_21d, vol_ratio_21_252, volume_price_pos_move_confirmed, days_bw_15_20perc, days_bw_10_15perc, days_bw_5_10perc, days_bw_2_5perc, lottery_class |
| Stage 5 — Universe + Ranks | adtv_63_cr, passes_adtv, in_universe, rank_ret_12m1m, rank_simple_vol_adj_momentum, rank_sharpe_style_momentum, rank_sortino_style_momentum, rank_fip_ret_12m1m, rank_fip_simple_vol_adj_momentum, rank_fip_sharpe_style_momentum, rank_fip_sortino_style_momentum |

---

### 6.D Strategy Engine (as built)

**`backtest/strategies/config.py`** — single source of truth for all locked decisions.
**`backtest/strategies/gates.py`** — `apply_gate(gate_id, signals_df)` → filtered DataFrame.
**`backtest/strategies/scores.py`** — `apply_score(score_id, survivors_df)` → top-N DataFrame.
**`backtest/strategies/engine.py`** — `get_portfolio()` + `run_all_cells()` → long-format 625-row DataFrame per Friday.

Scoring approach for C4/C5: all inputs re-ranked within survivor pool (post-gate) before weighting. Consistent with production `testing_shortlist.py`.

---

### 6.E Rebalance Mechanics (as built)

Per §6.7 exactly. One `PortfolioState` object per cell, persists across all 511 Fridays.

Edge cases handled:
- `num_entries = 0` → Steps 4/5 skipped, cash stays in pool
- Week 1 or full turnover (`count(held) = 0`) → `cap_per_entry = available_cash / num_entries` (no cap)
- Symbol with no price at T → excluded from portfolio that week
- `cash_deployed` ≤ `available_cash` always (no borrowing)

Sanity check: `portfolio_value_post = portfolio_value_pre` on rebalance day (verified at runtime).

---

### 6.F Performance Metrics (as built)

Per §6.8. CAGR uses fixed 10-year denominator per spec (actual period = 9.83 years — negligible difference).

**Deflated Sharpe implementation note:** Bailey & López de Prado (2012) approximation used, not full Harvey & Liu (2015). Threshold = `sqrt(log(25) / 2) = 1.269`. Our 25 cells are highly correlated (same universe), so true H&L would give a lower threshold. Current implementation is conservative. Full H&L with correlation adjustment deferred to Stage 8.

---

### 6.G Backtest Results (run date 02072026)

**Top 5 cells by Sharpe:**

| Cell | CAGR | Sharpe | Sortino | MaxDD | DD Recovery | Alpha |
|---|---|---|---|---|---|---|
| G5_C1 | 36.73% | 1.24 | 1.72 | -30.40% | 61 weeks | 22.77% |
| G3_C1 | 38.17% | 1.17 | 1.57 | -34.78% | 64 weeks | 24.21% |
| G4_C1 | 37.61% | 1.16 | 1.56 | -34.73% | 64 weeks | 23.65% |
| G5_C3 | 32.61% | 1.15 | 1.62 | -29.82% | 64 weeks | 18.65% |
| G3_C3 | 35.63% | 1.14 | 1.54 | -34.01% | 23 weeks | 21.67% |

**Benchmark (^CRSLDX / Nifty 500):**

| CAGR | Sharpe | Sortino | MaxDD | DD Recovery |
|---|---|---|---|---|
| 13.96% | 0.47 | 0.64 | -34.39% | 29 weeks |

**Key findings:**
- All 25 cells outperform benchmark on CAGR and Sharpe
- C1 (raw 12M-1M momentum rank) dominates — simple beats complex across all gate variants
- G5 (strictest gate) consistently delivers lowest MaxDD
- G3_C2 = worst cell (CAGR 4.33%, Sharpe -0.16) — G3 and C2 are contradictory filters
- No cell clears deflated Sharpe threshold of 1.269 (G5_C1 at 1.24 is closest)
- `sharpe_significant = False` for all 25 — expected given conservative threshold

---

### 6.H Data Files (not in GitHub — EC2 only)

| File | Location | Size |
|---|---|---|
| prices_backtest.parquet | backtest/data/ | 45.2 MB |
| universe_metadata_backtest.parquet | backtest/data/ | small |
| nifty500_weekly.parquet | backtest/data/benchmark/ | small |
| signals_{DDMMYYYY}.parquet × 511 | backtest/signals/historical/ | ~164KB each, ~82MB total |
| backtest_results_02072026.parquet | backtest/results/ | <1 MB |
| backtest_weekly_returns_02072026.parquet | backtest/results/ | 135 KB |
| backtest_portfolio_activity_02072026.parquet | backtest/results/ | 5.8 MB |

Parquets excluded from GitHub per `.gitignore`. All code committed at commit `74ae1f2`.
---

## Stage 6 — Deep Dive Analysis & Cell Selection (2026-07-04)

**Input files:** `backtest_results_03072026.parquet`, `backtest_weekly_returns_03072026.parquet`, `backtest_portfolio_activity_03072026.parquet`

**Cell grid tested (9 cells + benchmark):**
`G2_C3`, `G2_C6`, `G4_C3`, `G4_C6`, `G4_C7`, `G5_C1`, `G6_C1`, `G6_C6`, `G6_C7`

### Cell Selection Decision

**Selected cell: G6_C6**

Rationale summarised from full deep dive analysis:

- G6 gate (Weinstein + no lottery + RS > 0) provides the cleanest quality filter — stocks must be in confirmed uptrend, non-erratic, AND beating the market
- C6 score (rank_ret_12m1m + rank_rs_excess_ret_mkt with 1.2× incumbent boost) fills the identified RS signal gap while controlling churn via stickiness
- RS signal (rs_excess_ret_mkt) was only ever tested buried in C4/C5 composites — C6 gives it a clean standalone test paired with raw momentum
- Incumbent 1.2× multiplier reduces unnecessary turnover without sacrificing signal quality

### Gates and Scores Tested

**Gates:**
- G2: `weinstein_stage2 = True`
- G4: `lottery_class not in {LOTTERY, BORDER_LOTTERY, EXTREME_LOTTERY}`
- G5: G2 + G3 + G4 (G3 = stpb_ret_21d > -5% AND proximity_52w_high ≥ 0.80)
- G6 (NEW): `weinstein_stage2 = True` AND `lottery_class not in {LOTTERY, BORDER_LOTTERY, EXTREME_LOTTERY}` AND `rs_excess_ret_mkt > 0`

**Scores:**
- C1: `rank_ret_12m1m` only
- C3: `avg(rank_sharpe_style_momentum, rank_sortino_style_momentum)`
- C6 (NEW): `rank_ret_12m1m + rank_rs_excess_ret_mkt` with 1.2× multiplier on incumbents before top-N selection
- C7 (NEW): `0.5 × rank_ret_12m1m + 0.5 × rank_rs_excess_ret_mkt` (no hysteresis)

**G3 gate deprecated:** G3's proximity + stpb combination was shown to backfire in bear markets (ranked dead last in 2018-2020 bear). Not included in Stage 7 onwards.

### Weinstein Stage 2 — Updated Definition (locked)

Previous implementation (2 conditions) upgraded to 4 conditions:

| # | Condition | Timeframe | Status |
|---|---|---|---|
| 1 | Weekly close > 30-week MA | Weekly | ✅ Existing |
| 2 | 30-week MA slope positive (this week > last week) | Weekly | ✅ Existing |
| 3 | 150-day SMA > 200-day SMA (MA fan-out) | Daily | ❌ NEW — add in Stage 7 |
| 4 | Price > 50-day SMA | Daily | Explicitly excluded (see rationale) |

**Rationale for excluding 50D SMA:** Entry gate only (not exit). Makes pool more restrictive on short-term momentum with no clear regime benefit. Decision locked.

**Rationale for excluding 52w high proximity:** Previously in G3 gate. G3 deprecated. Not added to Weinstein — proximity backfires in bear markets (G3_C1 ranked last in 2018-2020 bear).

---

## Stage 7 — Portfolio Selection (G6_C6 Production Integration)

**Script (to be built):** `signals/stage6/stage6_assemble.py`
**Entry point added to:** `run_pipeline.py` after Stage 5

Stage 7 integrates the selected G6_C6 cell into the production pipeline as a live portfolio selection stage. Starts from Stage 5 output.

---

### 7.1 Design Philosophy

- Production portfolio selection = one cell only: **G6_C6**
- No strategy grid — single path, deterministic output
- Incumbent stickiness via C6's 1.2× multiplier — reduces churn without changing signal
- Portfolio state is persisted between runs — required for incumbent identification
- Output is actionable: BUY / HOLD / SELL per symbol

---

### 7.2 Gate: G6

```
G6 = in_universe = True
     AND weinstein_stage2 = True
     AND lottery_class not in {LOTTERY, BORDER_LOTTERY, EXTREME LOTTERY}
     AND rs_excess_ret_mkt > 0
```

All conditions must be True simultaneously. `in_universe` is the prerequisite (applied first). Then Weinstein, then lottery exclusion, then RS positivity.

If pool size < 25 after G6: deploy all available symbols (no partial fill with non-qualifying stocks). Note pool size in output. Stage 8 backlog item: Nifty 500 Weinstein overlay will address systematic sub-25 handling.

---

### 7.3 Score: C6

```
c6_raw    = rank_ret_12m1m + rank_rs_excess_ret_mkt
c6_score  = c6_raw / 1.2   if symbol in current_portfolio
            c6_raw          otherwise
```

Lower c6_score = better (both rank components are ascending toward rank 1 = best).

Dividing incumbents' score by 1.2 lowers their numeric score, making them harder to displace. A new candidate must have a c6_raw at least 16.7% better than the incumbent's c6_raw to displace them.

Tiebreaker: `proximity_52w_high` descending.

**NaN handling:** symbols with NaN in `rank_ret_12m1m` or `rank_rs_excess_ret_mkt` are excluded from selection. These are typically recent listings without full 252-day history.

---

### 7.4 Portfolio State

**File:** `portfolio/portfolio_state.parquet`

| Column | Type | Description |
|---|---|---|
| `symbol` | str | Stock symbol |
| `shares` | float | Shares currently held |
| `entry_date` | date | Date first entered portfolio |
| `entry_price` | float | Price at entry |
| `last_rebalance_date` | date | Last Friday position was confirmed |

**First run:** If file does not exist, `current_holdings = {}`. No incumbent boost. File created after first run.

---

### 7.5 Output

**File:** `signals/stage6/portfolio_recommendations_{DDMMYYYY}.parquet`

| Column | Type | Description |
|---|---|---|
| `symbol` | str | Stock symbol |
| `action` | str | BUY / HOLD / SELL |
| `c6_score` | float | Final C6 score (post incumbent adjustment) |
| `c6_raw` | float | Pre-adjustment score |
| `rank_ret_12m1m` | Int64 | Momentum rank |
| `rank_rs_excess_ret_mkt` | Int64 | RS rank |
| `rs_excess_ret_mkt` | float | Raw RS vs market |
| `ret_12m1m` | float | Raw 12M-1M return |
| `proximity_52w_high` | float | Tiebreaker value |
| `weinstein_stage2` | bool | Gate condition 1 |
| `lottery_class` | str | Gate condition 2 |
| `incumbent_boost_applied` | bool | Whether 1.2× applied |
| `as_of_date` | date | T from signals file |
| `run_date` | date | IST date pipeline ran |
| `pool_size_post_gate` | int | Number of symbols that passed G6 (before top-N cut) |

---

### 7.6 Folder Structure (Stage 7 additions)

```
signals/
└── stage6/
    ├── stage6_assemble.py          # master entry point
    └── metrics/
        ├── g6_gate.py              # apply_g6_gate(signals_df) → filtered_df
        └── c6_score.py             # apply_c6_score(filtered_df, current_holdings) → ranked_df

portfolio/
├── portfolio_state.parquet         # current holdings (persisted between runs)
└── portfolio_history/
    └── portfolio_{DDMMYYYY}.parquet  # point-in-time snapshot per run (audit trail)
```

---

### 7.7 Integration into run_pipeline.py

New stage added after Stage 5:

```python
run_stage(
    "STAGE 6 — Portfolio Selection (G6_C6)",
    BASE / "signals" / "stage6" / "stage6_assemble.py",
)
```

---

### 7.8 Pre-Stage 7 Checklist

In this order before writing stage6_assemble.py:

1. Update `signals/stage3/metrics/weinstein.py` — add 150D SMA > 200D SMA condition
2. Verify `rank_rs_excess_ret_mkt` column present in latest Stage 5 output
3. Confirm NaN handling in signal columns
4. Decide sub-25 pool behaviour (current decision: deploy all available)

---

### 7.9 Known Caveats (Stage 7 specific)

1. **No transaction costs modelled in backtest** — real-world friction will reduce returns. G6_C6's incumbent stickiness partially mitigates this via lower churn.
2. **Sub-25 pool in stress periods** — G6's strict gates (especially RS > 0) will thin the pool during market crashes. Seen historically in Feb-Apr 2016, Oct 2018, Mar-Jun 2020. Stage 8 Nifty 500 regime overlay will address this.
3. **Incumbent boost is untested in live deployment** — the 1.2× multiplier was part of the C6 backtest design but its exact real-world churn impact depends on portfolio state persistence working correctly.
4. **Weinstein update (150D > 200D)** changes the production signal from the backtest signal — backtest used the older 2-condition Weinstein. First few weeks of live deployment may show slightly different pools than backtest history suggested.

