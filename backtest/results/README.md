# Backtest Registry — Metric Definitions

Single source of truth for all metrics tracked in `registry.csv`.
One row per variant per backtest run. Sorted by NAV descending.

---

## Identity Columns

| Column | Description |
|---|---|
| `run_date` | Date the backtest was executed (DD/MM/YYYY) |
| `script` | Backtest script that produced this result |
| `Variant` | Short strategy identifier (e.g. MR_HYB_M) |
| `Description` | Human-readable variant description |

---

## Return Metrics

| Column | Description |
|---|---|
| `CAGR` | Compound Annual Growth Rate over full backtest period |
| `BM CAGR` | Benchmark (Nifty 500) CAGR over same period |
| `Alpha` | CAGR minus BM CAGR — strategy outperformance vs benchmark |
| `NAV end (M)` | Final portfolio value in millions (Rs), starting from Rs 10M |
| `Periods` | Number of rebalancing periods in the backtest |

---

## Risk Metrics

| Column | Description |
|---|---|
| `Max DD` | Maximum Drawdown — largest peak-to-trough decline in NAV |
| `Ulcer_Index` | Root mean square of all drawdowns from peak (in %). Lower = less pain. Captures both depth and duration of drawdowns, unlike Max DD which only captures the single worst event |
| `Calmar` | CAGR / abs(Max DD) — return per unit of worst-case drawdown |

---

## Risk-Adjusted Return Metrics

| Column | Description |
|---|---|
| `Sharpe` | (CAGR - 7% risk-free rate) / annualised return volatility. Higher = better risk-adjusted return |
| `Martin_Ratio` | (CAGR - 7% risk-free rate) / Ulcer Index. Like Sharpe but uses Ulcer Index instead of volatility — penalises strategies that stay underwater for long periods |

---

## Consistency Metrics

| Column | Description |
|---|---|
| `Pct_Positive_Periods` | Percentage of rebalancing periods (weeks or months) where portfolio NAV was higher than the previous period. This is a period-level metric — not the same as stock-level win rate |
| `Avg Turnover` | Average percentage of portfolio replaced each rebalancing period |
| `K_Ratio` | Slope of cumulative log returns divided by standard error of that slope. Measures smoothness and consistency of the equity curve. Higher = more consistent compounding with less zigzagging. Two strategies with identical CAGR can have very different K-ratios |

---

## Trade / Position Metrics

All position metrics are computed at the **stock level** — each BUY-SELL pair for a single stock counts as one position. If a stock is bought and sold three times, that counts as three positions.

| Column | Description |
|---|---|
| `Total_Positions` | Total number of closed BUY-SELL pairs across full backtest |
| `Winners` | Number of positions closed at a profit (sell value > buy value) |
| `Losers` | Number of positions closed at a loss or breakeven |
| `Win_Rate_Pct` | Winners / Total Positions × 100. Stock-level win rate — different from Pct_Positive_Periods which is period-level |
| `Avg_Pos_Return_Pct` | Average percentage return on winning positions |
| `Avg_Neg_Return_Pct` | Average percentage loss on losing positions (expressed as positive number) |
| `Expectancy_Ratio` | (Win Rate × Avg Win%) − (Loss Rate × Avg Loss%). Average return per rupee invested per trade, capital-independent. Positive = strategy has edge. e.g. 0.06 means on average each trade returns 6 paise per rupee invested |
| `Profit_Factor` | Gross profit from all winners / Gross loss from all losers. > 1.5 = good, > 2.0 = very good |

---

## Holding Period Metrics

| Column | Description |
|---|---|
| `Avg_Hold_Days` | Average number of calendar days between BUY and SELL across all positions |
| `Max_Hold_Days` | Longest single position held in calendar days |
| `Max_Hold_Symbol` | Stock ticker that had the longest holding period |

---

## Strategy Naming Convention

| Prefix | Meaning |
|---|---|
| `MR` | Momentum Ratio scoring (ret/vol Z-score composite) |
| `_G6` | Full G6 gate applied (weinstein + lottery + alpha) |
| `_W` | Weinstein gate only |
| `_LO` | Lottery class excluded only |
| `_LA` | Lottery excluded + alpha > 0 |
| `_WA` | Weinstein + alpha > 0 |
| `_HYB` | Hybrid — Weinstein applied in reconstitution, not as pre-filter |
| `_M` | Monthly rebalancing cadence |
| `_B50` | Buffer zone = 50 (default is 38) |

---

## Notes

- All backtests use Friday signal → Monday open execution
- Starting capital: Rs 10M (1 Crore), equal-weighted across 25 positions
- Risk-free rate assumed: 7% per annum
- Benchmark: Nifty 500 weekly close prices
- Buffer zone reconstitution: retain holdings ranked ≤ BUFFER_ZONE, force-in top 12 non-holders regardless of Weinstein
- Production strategy as of Aug 2026: **MR_HYB_M** (hybrid Weinstein, monthly cadence, circuit gate < 3)
