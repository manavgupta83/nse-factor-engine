# Stage 6 — Metrics

Portfolio selection engine for the MR_M (Momentum Ratio, Monthly) strategy.

## Files

| File | Purpose |
|---|---|
| `mr_score.py` | Scores the eligible universe using momentum ratio formula |
| `mr_reconstitute.py` | Determines final 25-stock portfolio using 4-step algorithm |
| `mr_beta.py` | Computes 12-month beta, alpha and returns for each stock and portfolio |
| `g6_gate.py` | Pre-scoring filter — removes stocks with circuit breaker hits |

---

## Methodology

### Step 1 — Scoring (`mr_score.py`)

All stocks passing the G6 gate are scored using a vol-adjusted momentum composite:

```
MR_12 = ret_12m1m / vol_252
MR_6  = ret_6m1m  / vol_252

Z_12, Z_6 = cross-sectional Z-scores of MR_12, MR_6

Weighted_Z = 0.5 * Z_12 + 0.5 * Z_6

Momentum_Score = 1 + Weighted_Z          if Weighted_Z >= 0
               = 1 / (1 - Weighted_Z)    if Weighted_Z <  0
```

Stocks are ranked 1 to N by `Momentum_Score` descending (rank 1 = highest score).

**Gate (`g6_gate.py`):** Removes stocks with `lower_circuit_hits_63d >= 3` before scoring. Weinstein is NOT applied at this stage.

---

### Step 2 — Reconstitution (`mr_reconstitute.py`)

Executed in strict order every rebalance to determine the new 25-stock portfolio.

#### Step 3 — Compulsory Exclusions
- Evaluate all stocks in `Current_Portfolio`
- **IF** `Rank > 38` → remove from portfolio (SELL)
- Unscored holdings (not in universe) → also removed (SELL)

#### Step 4 — Compulsory Inclusions
- Evaluate all stocks in `Universe` that are **NOT** in `Current_Portfolio`
- **IF** `Rank <= 12` → force into portfolio (BUY)
- Each forced-in stock **unconditionally** displaces the holding with the lowest `Momentum_Score` currently in the portfolio
- No Weinstein check — rank 1-12 non-holders always enter

#### Step 5 — Retention
- Remaining holdings with `Rank <= 38` that survived Steps 3 and 4 → retained (HOLD)

#### Step 6 — Fill Remaining Slots
- If portfolio count < 25 after Steps 3-5
- Add non-holdings in strict rank order until count = 25 (BUY)

**Final portfolio = exactly 25 stocks.**

---

### Step 3 — Beta Calculation (`mr_beta.py`)

Computed at every rebalance immediately after reconstitution, using 12 months
of daily price history (252 trading days).

#### Benchmark
- Index: Nifty500 (`^CRSLDX`)
- Source: `/home/ec2-user/nse-factor-engine/data/index_prices_DDMMYYYY.parquet`
- Latest file resolved by parsed date (not string sort)

#### Stock prices
- Source: `/home/ec2-user/nse-factor-engine/data/prices.parquet`
- Suffix stripping: `.NS` / `.BO` handled automatically

#### Individual stock beta

```
Beta = Cov(stock_daily_ret, nifty500_daily_ret) / Var(nifty500_daily_ret)
```

Computed over the 252 trading days prior to `as_of_date`.

#### Portfolio beta

Equal-weighted portfolio return series computed from constituent daily returns,
then regressed against Nifty500 daily returns over the same 252-day window.

```
Portfolio_daily_ret = mean(stock_daily_ret) across all 25 holdings
Portfolio_Beta = Cov(Portfolio_daily_ret, nifty500_daily_ret) / Var(nifty500_daily_ret)
```

#### Jensen Alpha

```
Alpha = Stock_12m_ret - Rf - Beta * (Market_12m_ret - Rf)
```

Where:
- `Rf = 7%` annualised (risk-free rate)
- `Market_12m_ret` = compounded Nifty500 return over the 252-day window
- `Stock_12m_ret` = compounded stock return over the same aligned window

#### Output fields added to portfolio parquet

| Field | Level | Description |
|---|---|---|
| `beta_12m` | Stock | 12-month beta vs Nifty500 |
| `r2` | Stock | R2 (correlation squared) — how much of stock return variation is explained by Nifty500 |
| `stock_12m_ret` | Stock | Compounded 12-month return |
| `alpha_12m` | Stock | Jensen alpha over 12 months |
| `market_12m_ret` | All rows | Nifty500 12-month return (same value) |
| `portfolio_beta` | All rows | Equal-weighted portfolio beta (same value) |

#### Parquet output

Saved to `signals/stage6/beta/beta_DDMMYYYY.parquet` at each rebalance.

| Column | Description |
|---|---|
| `as_of_date` | Signal date |
| `symbol` | Stock symbol or `PORTFOLIO` |
| `level` | `stock` or `portfolio` |
| `beta_12m` | Beta vs Nifty500 |
| `cov` | Raw covariance (stock vs bench daily returns) |
| `bench_var` | Benchmark daily return variance |
| `stock_12m_ret` | Stock 12-month compounded return |
| `market_12m_ret` | Nifty500 12-month compounded return |
| `alpha_12m` | Jensen alpha |
| `portfolio_beta` | Portfolio-level beta (PORTFOLIO row only) |
| `port_12m_ret` | Portfolio 12-month return (PORTFOLIO row only) |
| `bench_start` | Start date of beta window |
| `bench_end` | End date of beta window |
| `n_obs` | Trading days used in beta calculation |

---

## Key Constants

```python
PORTFOLIO_N  = 25    # target portfolio size
FORCED_IN_N  = 12    # compulsory inclusion threshold (rank <= 12)
BUFFER_ZONE  = 38    # retention / exclusion boundary (rank <= 38 retained)
BETA_WINDOW  = 252   # trading days for beta calculation (~12 months)
RF_ANNUAL    = 0.07  # risk-free rate for Jensen alpha
```

---

## Return Values

`apply_reconstitution(ranked_df, current_holdings)` returns:

```python
output_df         # DataFrame with action and tier columns for all relevant stocks
top25_symbols     # set of 25 symbols in final portfolio
weinstein_rejects # empty DataFrame (kept for stage6_assemble.py compatibility)
```

`compute_beta(top25_symbols, as_of_date)` returns:

```python
{
    "stocks"           : list of per-stock dicts (beta, cov, bench_var, returns, alpha)
    "portfolio_beta"   : float
    "portfolio_12m_ret": float
    "market_12m_ret"   : float
    "bench_start"      : date
    "bench_end"        : date
    "n_obs"            : int
}
```

### Action values

| Action | Meaning |
|---|---|
| `BUY` | Not in current portfolio, entering this rebalance |
| `HOLD` | In current portfolio, retained this rebalance |
| `SELL` | In current portfolio, exiting this rebalance |
| `WATCHLIST` | Not in portfolio, rank <= 38, monitor for next rebalance |

---

## Rebalance Cadence

Monthly — minimum 30 days between rebalances enforced by `stage6_assemble.py`.

---

## History

| Date | Change |
|---|---|
| Aug 2026 | Added `mr_beta.py` — 12m stock and portfolio beta, Jensen alpha, parquet output |
| Aug 2026 | Wired `mr_beta.py` into `stage6_assemble.py` — beta fields in output parquet and monitor mode |
| Aug 2026 | Replaced MR_HYB_M (Weinstein SET1/SET2 architecture) with correct MR_M logic |
| Aug 2026 | Fixed SET 1 bug — was taking top 12 non-holders from full universe instead of ranks 1-12 |

---

## Absolute Momentum Scorecard (`abs_momentum_scorecard.py`)

Evaluates each stock's absolute momentum quality across 6 structural metrics.
Outputs a score, tier, and per-metric PASS/FAIL for every stock in the input dataframe.

### Purpose

Complements the relative momentum rank (MR score) with an absolute quality check —
a stock can rank highly on relative momentum but still be in a structurally weak trend.
The scorecard flags this before it enters the portfolio.

---

### Input Signals

#### Fresh signals (recomputed from OHLCV each run)

| Signal | Used In | Min History |
|---|---|---|
| `rsi_14` | RSI_Strength | 15 bars |
| `ret_12m1m` | Acceleration, ROC Gate | 273 bars |
| `ret_6m1m` | Acceleration, ROC Gate | 147 bars |
| `ret_3m1m` | Acceleration, ROC Gate | 84 bars |
| `pct_pos_days` | RSI_Strength | 2 bars (252-window) |
| `proximity_52w_high` | Hi52W_Proximity | 2 bars (252-window) |
| `dist_ema_20` | Display / downstream only | 20 bars |
| `dist_ema_50` | MA_Position | 50 bars |
| `weinstein_stage2` | MA_Position | 151 bars |
| `bb_pct_b` | Hi52W_Proximity | 20 bars |
| `mfi_14` | Volume_Confirm | 15 bars |
| `vol_ratio_21_252` | Volume_Confirm | 22 bars |
| `volume_price_pos_move_confirmed` | Volume_Confirm | 22 bars |

#### Stale signals (carried from last rebalance parquet — not recomputed)

| Signal | Used In |
|---|---|
| `smoothness` | Acceleration + Trend_Strength (vs universe median) |
| `rm_r2` | Trend_Strength |
| `residual_momentum` | Trend_Strength |
| `stpb_zscore_21d` | Acceleration |

> `smoothness` is the only signal used in two metrics and also drives the universe-level median — most critical stale signal.

---

### Metric Definitions

#### 1. MA_Position
Weinstein Stage 2 confirmed + price above 50 EMA.

weinstein_stage2 == 1 AND dist_ema_50 > 0


#### 2. RSI_Strength
Momentum confirmed by RSI and positive day ratio.

rsi_14 > 50 AND pct_pos_days > 0.52


#### 3. Acceleration
Return horizon is accelerating + trend is smooth + recent breakout.

(ret_3m1m > ret_6m1m/2 OR ret_6m1m > ret_12m1m/2)
AND smoothness > 0.5
AND stpb_zscore_21d > 0


#### 4. Hi52W_Proximity
Price is close to 52-week high and in upper Bollinger Band.

proximity_52w_high > 0.75 AND bb_pct_b > 0.5


#### 5. Trend_Strength
Regression quality + residual momentum + smoothness vs universe.

rm_r2 > 0.3 AND residual_momentum > 0 AND smoothness > universe_median


#### 6. Volume_Confirm
Volume is confirming upward price action.

volume_price_pos_move_confirmed == 1 AND mfi_14 > 50 AND vol_ratio_21_252 > 1.0


---

### ROC Gate (post-scoring override)

Applied after all 6 metrics are scored. All three return horizons must be positive:

ret_12m1m > 0 AND ret_6m1m > 0 AND ret_3m1m > 0


If any horizon is zero or negative:
- `Score` → overridden to `0`
- `AbsMom_Tier` → `Skip (negative: <failed col names>)`
- Individual metric PASS/FAIL columns are left intact

NaN return values are treated as failed (insufficient history = no positive return).

---

### Scoring & Tier Logic

Each metric contributes 1 point (PASS) or 0 points (FAIL). Max score = 6.

| Score | Tier | Meaning |
|---|---|---|
| 6/6 | `Tier1_Perfect` | All metrics pass — strongest absolute momentum |
| 4–5/6 | `Tier2_Strong` | Solid momentum structure |
| 2–3/6 | `Tier3_Moderate` | Weak or partial momentum |
| <2/6 | `Skip` | Structurally too weak |
| ROC gate fail | `Skip (negative: ...)` | One or more return horizons negative |

---

### Output Columns Appended

| Column | Values | Description |
|---|---|---|
| `MA_Position` | PASS / FAIL | Weinstein Stage 2 + EMA50 |
| `RSI_Strength` | PASS / FAIL | RSI + positive day ratio |
| `Acceleration` | PASS / FAIL | Return acceleration + smoothness + zscore |
| `Hi52W_Proximity` | PASS / FAIL | 52W high proximity + Bollinger %B |
| `Trend_Strength` | PASS / FAIL | R2 + residual momentum + smoothness |
| `Volume_Confirm` | PASS / FAIL | Volume/price confirmation + MFI |
| `Score` | 0–6 | Count of metrics passing (0 if ROC gate fails) |
| `AbsMom_Tier` | Tier1/2/3/Skip | Final tier label |

---

### Public API

```python
from signals.stage6.metrics.abs_momentum_scorecard import score_momentum, compute_fresh_signals

# Score a dataframe of stocks
scored_df = score_momentum(df)

# Recompute 13 fresh signals for a single symbol from raw OHLCV
signals = compute_fresh_signals(sym_df, as_of_date)
```

---

### History

| Date | Change |
|---|---|
| Sep 2026 | Added `abs_momentum_scorecard.py` — 6-metric absolute momentum scorecard with ROC gate and tier logic |
| Sep 2026 | Batch-scored all 513 historical signal parquets (Jan 2016 – Jun 2026) with scorecard output columns |
