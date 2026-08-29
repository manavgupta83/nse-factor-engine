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
