# Stage 6 — Metrics

Portfolio selection engine for the MR_M (Momentum Ratio, Monthly) strategy.

## Files

| File | Purpose |
|---|---|
| `mr_score.py` | Scores the eligible universe using momentum ratio formula |
| `mr_reconstitute.py` | Determines final 25-stock portfolio using 4-step algorithm |
| `g6_gate.py` | Pre-scoring filter — removes stocks with circuit breaker hits |

---

## Methodology

### Step 1 — Scoring (`mr_score.py`)

All stocks passing the G6 gate are scored using a vol-adjusted momentum composite:

MR_12 = ret_12m1m / vol_252
MR_6 = ret_6m1m / vol_252

Z_12, Z_6 = cross-sectional Z-scores of MR_12, MR_6

Weighted_Z = 0.5 * Z_12 + 0.5 * Z_6

Momentum_Score = 1 + Weighted_Z if Weighted_Z >= 0
= 1 / (1 - Weighted_Z) if Weighted_Z < 0


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

## Key Constants

```python
PORTFOLIO_N = 25   # target portfolio size
FORCED_IN_N = 12   # compulsory inclusion threshold (rank <= 12)
BUFFER_ZONE = 38   # retention / exclusion boundary (rank <= 38 retained)
```

---

## Return Values

`apply_reconstitution(ranked_df, current_holdings)` returns:

```python
output_df         # DataFrame with action and tier columns for all relevant stocks
top25_symbols     # set of 25 symbols in final portfolio
weinstein_rejects # empty DataFrame (kept for stage6_assemble.py compatibility)
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
| Aug 2026 | Replaced MR_HYB_M (Weinstein SET1/SET2 architecture) with correct MR_M logic |
| Aug 2026 | Fixed SET 1 bug — was taking top 12 non-holders from full universe instead of ranks 1-12 |
