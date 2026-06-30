# NSE Factor Engine — Methodology Reference
*Last updated: 2026-06-29 · Stages complete: 1, 2, 3*

---

# Stage 1 — Universe & Liquidity

## Purpose
Returns the current investable NSE universe (Nifty 500 base) after market-cap and liquidity screens, with 15M trailing OHLCV stored for downstream signal computation.

## Inputs
| File | Key columns |
|---|---|
| `data/raw/nifty500_symbols.csv` | `symbol` (500 rows) |
| yfinance API (`.NS` suffix) | OHLCV + `marketCap` / `nonDilutedMarketCap` |

## Filters
| Filter | Threshold | Window | Source |
|---|---|---|---|
| Market Cap | ≥ ₹500 Cr | point-in-time (today) | Gray |
| ADTV | ≥ ₹10 Cr/day | rolling 63 trading days | Gray |
| Zero-volume rows | dropped | per row | — |

**Logic:** `in_universe = passes_mktcap AND passes_adtv`
- `market_cap` uses `marketCap`, falls back to `nonDilutedMarketCap` (diff < 0.1% in testing)
- ADTV = mean(close × volume) over trailing 63 trading days, in ₹ Cr
- Price fetched only if market cap ≥ ₹500 Cr (cap check gates the download)

## Output
**`universe/universe_{YYYYMMDD}.parquet`** — one row per symbol, fresh dated file per run (history retained, no deletion)

Columns: `symbol, company_name, industry, market_cap_cr, adtv_63_cr, passes_mktcap, passes_adtv, in_universe`

**Counts (run 2026-06-28):**
| Stage | Count |
|---|---|
| Total symbols | 500 |
| Passes mktcap (≥ ₹500 Cr) | 499 |
| Passes ADTV (≥ ₹10 Cr) | 494 |
| **in_universe** | **494** |

Supporting outputs:
| File | Schema |
|---|---|
| `data/prices.parquet` | `symbol, date, open, high, low, close, volume` (149,462 rows, 499 symbols, 2025-03-28 → 2026-06-25) |
| `data/universe_metadata.parquet` | `symbol, company_name, industry, market_cap_cr` (all 500, unfiltered) |
| `data/adtv.parquet` | `symbol, date, adtv_63_cr` (rolling, full history) |
| `data/last_run_date.txt` | last successful run date (YYYY-MM-DD) |
| `data/failed_symbols_{YYYYMMDD}.csv` | `symbol, failure_type, error_message, attempts` (only if failures persist) |

## Locked Decisions
1. **Data source** — yfinance (`.NS`), one symbol at a time, 2s sleep (avoid Yahoo throttling)
2. **History depth** — 15 calendar months on first fetch
3. **Market cap** — point-in-time only; yfinance has no historical mktcap. `marketCap` → `nonDilutedMarketCap` fallback
4. **Metadata** — full refresh every run, ALL 500 symbols regardless of filters (for later checks)
5. **Prices** — only symbols with mktcap ≥ ₹500 Cr stored
6. **Storage** — parquet, long format (symbol × date rows); universe + metadata one-row-per-symbol
7. **Append/dedup** — incremental append, dedup on `symbol+date`, `keep=last`
8. **Incremental window** — `fetch_start = last_date + 1`; recompute is per-symbol gap-fill
9. **ADTV** — rolling 63-day, stored per-symbol-per-date in `adtv.parquet`; universe uses latest value
10. **No NSE 500 index dependency** — ₹500 Cr floor defines investability; Nifty 500 only the starting symbol pool
11. **Symbol list** — external CSV (`data/raw/nifty500_symbols.csv`), refreshed manually on constituent change

## Known Issues / Edge Cases
- **company_name missing** — 47 recently-listed symbols (yfinance `longName` empty). Descriptive only, not used in computation. Left null.
- **TENNIND** — listed ~Apr 2026; `marketCap` key absent (only `nonDilutedMarketCap` = ₹23,360 Cr). Pre-fallback runs excluded it; fixed.
- **Zero-volume rows** — 9 NSE holiday dates returned by yfinance with vol=0 (price carried forward). Dropped at fetch and on every future run.
- **Empty-dataframe handling** — `pandas-market-calendars` NSE calendar found UNRELIABLE (listed 2026-06-26 as trading day; it was a holiday). NOT used. Replaced with rule: empty df within ≤7-day gap = UP TO DATE (not failure); >7-day gap = NO_DATA (retry).
- **Pre-close runs** — if run before 15:30 IST, today's bar not yet available; handled via `Asia/Kolkata` timezone check (verified working).
- **5 ADTV near-misses** (run 2026-06-28): BLUEDART (8.21), DCMSHRIRAM (8.23), BBTC (9.41), JSWDULUX (9.78), TRAVELFOOD (9.98) — legitimate exclusions, just under ₹10 Cr.

## Scripts
| File | Description |
|---|---|
| `universe/run_universe.py` | Main daily pipeline: load symbols → mktcap gate → incremental price fetch (2-retry on fail) → ADTV → dated universe snapshot → update last_run_date |
| `universe/build_universe_snapshot.py` | One-off: rebuild ADTV + universe from existing prices.parquet, no price fetch |
| `universe/data_quality.py` | QC report: dupes, nulls, zero/negative values, price sanity (high<low etc.), vol outliers, stale/short history, metadata completeness, symbol alignment → `logs/data_quality.log` |

## Run Control Flow
- **Scenario 1** — `last_run_date == today` → exit immediately (Option A, no rebuild)
- **Scenario 2** — before 15:30 IST → print pre-close note, fetch up to last available trading day
- **Success** = zero failed symbols after 2 retries → update `last_run_date.txt`, delete failed CSV
- **Failure** = symbols still failing → `last_run_date.txt` NOT updated → next run retries all

---

# Stage 2 — Momentum Signal (Core)

## Purpose
Computes per-stock momentum signals and volatility metrics for all 500 symbols as of a single as-of date. Produces a flat signal file consumed by Stage 3 onwards. No universe filtering here — that is Stage 4's job.

## Inputs
| File | Key columns |
|---|---|
| `data/prices.parquet` | `symbol, date, open, high, low, close, volume` |
| `universe/universe_{YYYYMMDD}.parquet` | `symbol, in_universe` (merged in Stage 4, not here) |

## Trading Day Windows
All offsets are **trading days**, not calendar days. T = latest date in `prices.parquet`.

| Label | Offset from T | Purpose |
|---|---|---|
| T-21 | skip-month anchor | near end of all return windows |
| T-63 | 3M lookback start | 3M-1M return |
| T-126 | 6M lookback start | 6M-1M return |
| T-252 | 12M lookback start | 12M-1M return, vol window start |

Skip-month logic: all returns end at T-21 (not T) to avoid short-term reversal.

## Signals Computed

### Returns
| Column | Formula | Window |
|---|---|---|
| `ret_12m1m` | (close[T-21] − close[T-252]) / close[T-252] | T-252 → T-21 |
| `ret_6m1m` | (close[T-21] − close[T-126]) / close[T-126] | T-126 → T-21 |
| `ret_3m1m` | (close[T-21] − close[T-63]) / close[T-63] | T-63 → T-21 |

### Volatility
| Column | Formula | Window | Returns used |
|---|---|---|---|
| `vol_252` | std(log rets, ddof=1) × √252 | T-252 → T | all |
| `vol_231` | std(log rets, ddof=1) × √252 | T-252 → T-21 | all |
| `downside_vol_252` | std(neg log rets, ddof=1) × √252 | T-252 → T | negative only |
| `downside_vol_231` | std(neg log rets, ddof=1) × √252 | T-252 → T-21 | negative only |

`vol_231` and `downside_vol_231` use the matched skip-month window (T-252→T-21) so numerator and denominator in the composites describe the same period. `vol_252` and `downside_vol_252` are retained as standalone metrics for correlation analysis in Stage 6.

### Composite Momentum Scores
All three use `vol_231` or `downside_vol_231` as denominator (matched window). `ret_12m1m` = skip-month 12M return throughout.

| Column | Formula | Source |
|---|---|---|
| `simple_vol_adj_momentum` | ret_12m1m / vol_231 | Capitalmind (directional, no RF) |
| `sharpe_style_momentum` | (ret_12m1m − RF) / vol_231 | PDF / Factor |
| `sortino_style_momentum` | (ret_12m1m − RF) / downside_vol_231 | Extension beyond PDF spec |

**RF = 0.07** (Indian 10Y G-Sec, annualised constant). Placeholder until a point-in-time G-Sec time series is introduced in Stage 6. RF does not affect within-date cross-sectional ranking (uniform shift across all symbols).

> **Note on Capitalmind:** `simple_vol_adj_momentum` implements the directional intuition CM has published (volatility-adjust the return). It is not CM's actual proprietary formula.

## Output
**`signals/stage2/momentum_core_signals_{DDMMYYYY}.parquet`** — one row per symbol, all 500 symbols, no universe filter applied.

Columns: `symbol, as_of_date, ret_12m1m, ret_6m1m, ret_3m1m, vol_252, vol_231, downside_vol_252, downside_vol_231, simple_vol_adj_momentum, sharpe_style_momentum, sortino_style_momentum, data_quality_flag`

**Counts (run 2026-06-25):**
| Metric | Count |
|---|---|
| Total symbols | 500 |
| Valid 12M-1M return | 478 |
| Valid 6M-1M return | 499 |
| Valid 3M-1M return | 500 |
| Valid vol / composites | 478 |
| Data quality flagged | 1 (VEDL) |

## Locked Decisions
1. **Return type** — simple % return for momentum; log returns for volatility only
2. **Skip-month** — all returns end at T-21 (not T); "12M return" everywhere = T-252→T-21
3. **Vol windows** — two cols: `vol_252` (full, standalone) + `vol_231` (matched, composite denominator)
4. **ddof** — sample std (ddof=1) for all vol calculations
5. **NaN propagation** — 22 symbols with <253 rows get NaN for 12M signals; kept in output, not dropped
6. **Null close** — forward-filled within symbol (TENNIND, 1 row affected)
7. **Risk-free rate** — constant 0.07, named param `RF`, placeholder until Stage 6
8. **Downside vol edge case** — symbols with <2 negative return days in window → NaN (not zero-divide)
9. **All 500 in output** — `in_universe` merge deferred to Stage 4
10. **Sortino** — added as per-stock signal (extension beyond PDF Stage 2 spec)
11. **T resolution** — see Global Locked Decision below; Stage 2 scripts must use the robust T resolution method

## Known Issues
| ID | Symbol | Issue | Impact |
|---|---|---|---|
| KI-001 | VEDL | Stock split 2026-04-30 not adjusted in yfinance | vol_252 inflated to 1.10; composites deflated; flagged via `data_quality_flag` |

TMPV, TRENT, IEX show `downside_vol > total_vol` — confirmed legitimate (negative days few but widely dispersed, asymmetric return distribution). Not a data error.

## Scripts
| File | Description |
|---|---|
| `signals/stage2/stage2_step0_inspect.py` | Data quality inspection — shapes, dtypes, date range, null check, duplicate check, symbol counts |
| `signals/stage2/stage2_step1_windows.py` | Resolves T-252/231/126/63/21 offsets to actual trading dates per symbol; flags symbols with insufficient history |
| `signals/stage2/stage2_step2_returns.py` | Computes ret_12m1m, ret_6m1m, ret_3m1m |
| `signals/stage2/stage2_step3_vol.py` | Computes vol_252, vol_231, downside_vol_252, downside_vol_231 |
| `signals/stage2/stage2_step4_composites.py` | Computes simple_vol_adj_momentum, sharpe_style_momentum, sortino_style_momentum |
| `signals/stage2/stage2_step5_assemble.py` | Consolidates all signals, writes momentum_core_signals_{DDMMYYYY}.parquet |
| `signals/stage2/stage2_investigate_violations.py` | One-off diagnostic for downside_vol > total_vol violations |

---

# Stage 3 — Momentum Quality (Path)

## Purpose
Refines the raw momentum score for path quality — how a stock earned its return matters as much as the return itself. Produces a flat signal file with 4 path-quality metrics per symbol. No universe filtering — that is Stage 4's job.

## Inputs
| File | Key columns |
|---|---|
| `data/prices.parquet` | `symbol, date, open, high, low, close, volume` |
| `signals/stage2/momentum_core_signals_{DDMMYYYY}.parquet` | `symbol, ret_12m1m` (sign used for FIP) |

## Trading Day Windows
All offsets are **trading days**, not calendar days. T resolved via robust method (see Global Locked Decisions).

| Label | Offset from T | Purpose |
|---|---|---|
| T-21 | skip-month anchor | end of formation window for FIP, pct_pos_days, smoothness |
| T-252 | 12M lookback start | start of formation window for all metrics |

- FIP, % Positive Days, Smoothness: window is T-252 → T-21 (skip-month, matches Stage 2)
- 52-Week High Proximity: window is T-252 → T (no skip-month — George & Hwang 2004)

## Signals Computed

| Column | Formula | Window | Source |
|---|---|---|---|
| `fip_score` | sign(ret_12m1m) × (% negative days − % positive days) | T-252 → T-21 | Gray |
| `pct_pos_days` | days with positive log return / total days | T-252 → T-21 | Gray |
| `pct_neg_days` | days with negative log return / total days | T-252 → T-21 | Gray |
| `smoothness` | positive 5-day blocks / total complete 5-day blocks | T-252 → T-21 | Factor |
| `proximity_52w_high` | close[T] / max(high, T-252→T) | T-252 → T | Factor |

### FIP Score Detail
- Daily log returns computed per symbol over T-252 → T-21
- A day is positive if log_ret > 0; negative if log_ret < 0; zero days excluded from both counts
- Lower FIP = smoother upward path = higher quality momentum
- Sign from `ret_12m1m` in Stage 2 output — NaN propagates for 22 short-history symbols

### Momentum Smoothness Detail
- Formation window sliced into non-overlapping 5-trading-day blocks
- Week Open = open price of day 1 of block; Week Close = close price of day 5 of block
- Week is positive if Week Close > Week Open
- Incomplete trailing block (< 5 days) is dropped
- All 500 symbols have at least 1 complete block — no NaNs from smoothness itself

### 52-Week High Proximity Detail
- Numerator: close price at T (latest trading day, no skip-month)
- Denominator: max of daily high over full T-252 → T window
- Independent predictive power per George & Hwang (2004) — not a path quality metric per se, but grouped here per PDF spec

## Output
**`signals/stage3/momentum_quality_signals_{DDMMYYYY}.parquet`** — one row per symbol, all 500 symbols, no universe filter applied.

Columns: `symbol, as_of_date, fip_score, pct_pos_days, pct_neg_days, smoothness, proximity_52w_high`

**Counts (run 2026-06-25):**
| Metric | Count |
|---|---|
| Total symbols | 500 |
| Valid fip_score | 478 |
| Valid pct_pos_days | 500 |
| Valid pct_neg_days | 500 |
| Valid smoothness | 500 |
| Valid proximity_52w_high | 500 |

## Locked Decisions
1. **Formation window** — T-252 → T-21 for FIP, pct_pos_days, pct_neg_days, smoothness; T-252 → T for proximity_52w_high
2. **Log returns for day classification** — FIP and pct_pos_days use log returns (not simple returns) for day-level positive/negative classification
3. **Zero return days** — excluded from both positive and negative day counts (neither positive nor negative)
4. **5-day block definition** — non-overlapping sequential blocks; Week Open = open of day 1, Week Close = close of day 5; incomplete trailing block dropped
5. **NaN propagation** — 22 symbols with <253 rows get NaN for fip_score (sign requires ret_12m1m which is NaN for these); pct_pos_days, smoothness, proximity_52w_high compute for all 500
6. **No universe filter** — `in_universe` stays out of Stage 3 output; deferred to Stage 4
7. **ret_12m1m sourced from Stage 2** — not recomputed in Stage 3; sign taken directly from Stage 2 output
8. **Residual Momentum deferred** — to be built as a separate script (`stage3_step5_residual_momentum.py`) and added to assembler after completion; see deferred decisions below

## Deferred Decisions
| Item | Detail |
|---|---|
| **Residual Momentum** | Single multivariate OLS per stock: `stock_daily_log_ret = α + β₁(market) + β₂(sector) + ε`. Market = equal-weighted avg daily log ret of all 500 symbols. Sector = equal-weighted avg daily log ret of same `industry` group (self-excluded). Residual momentum = sum(ε) over T-252→T-21. Output column: `residual_momentum`. Script: `signals/stage3/stage3_step5_residual_momentum.py`. To be added to assembler once validated. |

## Scripts
| File | Description |
|---|---|
| `signals/stage3/stage3_step0_inspect.py` | Inspect prices.parquet and stage2 signals — shapes, dtypes, date range, null counts, symbol alignment, formation window availability |
| `signals/stage3/stage3_step1_fip.py` | Computes fip_score, pct_pos_days, pct_neg_days |
| `signals/stage3/stage3_step2_pct_pos_days.py` | Extracts pct_pos_days as standalone output from step 1 |
| `signals/stage3/stage3_step3_smoothness.py` | Computes momentum smoothness via 5-day blocks |
| `signals/stage3/stage3_step4_52wk_high.py` | Computes 52-week high proximity |
| `signals/stage3/stage3_assemble.py` | Self-contained production assembler — recomputes all metrics from scratch, writes momentum_quality_signals_{DDMMYYYY}.parquet |

---

# Global Locked Decisions

## T Resolution — Robust Method (All Stages)
`prices.parquet` may contain stray rows for recently-listed symbols with partial or null OHLC data at dates beyond the last full trading day. Using `prices['date'].max()` or `sorted(dates)[-1]` as T is unreliable — it picks up these stray dates.

**Always resolve T as the latest date with data for at least 490 symbols:**

```python
date_counts = prices.groupby('date')['symbol'].count()
T = date_counts[date_counts >= 490].index.max()
all_dates = sorted(prices[prices['date'] <= T]['date'].unique())
```

**Known instance:** TENNIND has a row on `2026-06-26` with all OHLC = NaN. Correct T = `2026-06-25` (500 symbols). Using `prices['date'].max()` returned `2026-06-26` and caused point-in-time filters (`prices[prices['date'] == T]`) to return only 1 row, breaking Stage 3 Step 4.

**Threshold of 490** — set below 500 to tolerate legitimate single-day gaps for a small number of symbols without being fooled by stray partial rows.

This method must be applied in all Stage 2, 3, 4, 5, 6, 7 scripts wherever T is resolved from `prices.parquet`.

## Stage 4 — Entry Quality Filters

**Source:** Factor master list, Section 04 (Capitalmind / Gray). Filters, not ranking signals — they gate entry quality, they do not score or rank.

**Scripts:** `signals/stage4/metrics/{stpb,volume_confirmation,daily_return_magnitude}.py`, assembled by `signals/stage4/stage4_assemble.py`.

**Output:** appends 12 columns in place to `signals/final/momentum_signals_final_{DDMMYYYY}.parquet` (filename/date derived dynamically from T, not hardcoded — script is safe to re-run on any future T).

---

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

**Null propagation:** `stpb_zscore_21d`/`stpb_zscore_7d` carry forward the same 22 nulls present in Stage 2's `vol_231` (short-history symbols). `stpb_ret_21d`, `stpb_ret_7d`, `stpb_ma_distance_21d` have zero nulls (pure price-based, no Stage 2 dependency).

---

### 4.2 Volume Confirmation (Capitalmind)

"Recent volume relative to longer-term average. Rising price on rising volume = valid momentum."

| Column | Formula | Window |
|---|---|---|
| `vol_ratio_21_252` | avg_volume(T-21→T) / avg_volume(T-252→T) | 21d recent ÷ 252d longer-term |
| `volume_price_pos_move_confirmed` | boolean: (stpb_ret_21d > 0) AND (vol_ratio_21_252 > 1.2) | — |

**Design decision — price leg:** `stpb_ret_21d` (Section 4.1) reused as the price leg, rather than `ret_3m1m` from Stage 2, to keep the price and volume legs measuring the same 21-trading-day window. Pairing a 21d volume ratio against a ~2-month price return would introduce a window mismatch.

**Design decision — threshold:** vol_ratio > 1.2 (not > 1.0) chosen to require a meaningful volume increase, filtering day-to-day noise around the 1.0 baseline.

**IMPORTANT — one-sided flag.** `volume_price_pos_move_confirmed` is a positive-confirmation flag only (bullish entries). `False` does not imply bearish — it covers both "rising price, flat/falling volume" and "falling price" cases indiscriminately. A high `vol_ratio_21_252` with a negative `stpb_ret_21d` (e.g. AARTIIND: ratio 1.47, return −5.1%, flag False) is "high volume on a decline," a real and distinct pattern not separately characterized by this column. Stage 5 should not infer bearishness from `False`.

**Known issue — KI-002 (VEDL):** see `docs/KNOWN_ISSUES.md`. VEDL's 252d volume baseline spans the 2026-04-30 stock split, producing a structural ~3x volume-regime shift mid-window (unadjusted split, same root cause as KI-001). `vol_ratio_21_252` for VEDL (currently 1.71) is not reliably interpretable until the pre-split tail rolls out of the 252d window (~213 trading days from T as of this writing) or the price data is repatched.

---

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

**IMPORTANT — asymmetric threshold, confirmed intentional.** Only the top tier (EXTREME LOTTERY) requires more than 2 qualifying days; every other tier triggers on a single occurrence. This means one isolated extreme day is enough to classify a stock as LOTTERY, but it takes 3+ such days to escalate to EXTREME LOTTERY.

**Window construction note:** the 63-day return series requires 64 trading days of close prices (T-64→T); the anchor day (T-64) itself produces no return observation and is excluded from bucket counts, leaving exactly 63 return-days per symbol.

**Known limitation — VEDL, not logged as a known issue (explicit decision).** VEDL's `lottery_class` = LOTTERY is driven in part by a single contaminated day: the 2026-04-30 split shows as a ~65% single-day "return" (close ₹773.60 → ₹271.55), registering in `days_bw_15_20perc`. This is the same root cause as KI-001/KI-002 but was explicitly decided NOT to be patched or separately logged — VEDL's lottery classification should be read with this in mind, but the output is left as computed.

**Empirical result at T=2026-06-25:** 0 symbols in EXTREME LOTTERY, 36 in LOTTERY, 66 in BORDER_LOTTERY, 340 in CAUTIOUS, 58 in ALRIGHT, 0 in BORING.

---

### Cross-cutting notes

- All Stage 4 windows use the convention `(T-N, T]` — the lower bound is excluded, T is included — giving exactly N trading days. Verified explicitly for the 21d and 252d windows during implementation (an earlier draft of the MA_21 window incorrectly included T-21, inflating the window to 22 days; caught and fixed before handoff).
- `data_quality_flag` (Stage 2 column) is reused as-is for VEDL; no new Stage-4-specific flag column was added.
- The Stage 4 assembler derives both T and the input/output signals filename dynamically from `prices.parquet` — no hardcoded dates — and asserts a unique filename match before reading, and path equality before writing, so the script can be re-run safely on any future T without manual edits. The original pre-Stage-4 file is backed up (`_pre_stage4_backup.parquet` suffix) before being overwritten in place.
