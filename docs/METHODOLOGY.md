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
