# NSE Factor Engine — Stage 8 Backlog

Items deferred from earlier stages for post-development cleanup and enhancement.

---

## [S8-001] Batch fetch in production pipeline
**Source:** Stage 6 session
**Detail:** `run_pipeline.py` fetches prices ticker-by-ticker. Backtest fetch proved batch mode (50 symbols per request, yfinance threads=True) is ~7x faster. Migrate production fetch to same approach.

---

## [S8-002] Add rs_excess_ret_industry to production pipeline
**Source:** Stage 6 session
**Detail:** Add `rs_excess_ret_industry = stock_cum_ret - industry_cum_ret` to `signals/stage3/metrics/relative_strength.py`. Currently only in backtest pipeline.

---

## [S8-003] Rename rs_excess_ret → rs_excess_ret_mkt in production
**Source:** Stage 6 session
**Detail:** Rename `rs_excess_ret` → `rs_excess_ret_mkt` in `relative_strength.py` and all downstream references for clarity. Currently only renamed in backtest pipeline.

---

## [S8-004] Add stock_cum_ret to production signal output
**Source:** Stage 6 session
**Detail:** `stock_cum_ret` (cumulative log return over formation window) is an intermediate variable currently dropped. Add to final signal output in `relative_strength.py`.

---

## [S8-005] Implement full Harvey & Liu (2015) deflated Sharpe
**Source:** Stage 6 session
**Detail:** Current backtest implementation uses Bailey & López de Prado (2012) approximation — threshold = sqrt(log(25)/2) = 1.269. This is conservative because it assumes 25 independent strategies. Our 25 cells are highly correlated (same universe, overlapping stocks). True H&L with correlation matrix would give lower threshold — several cells would likely pass. Implement in `backtest/metrics/compute_metrics.py`.

---

## [S8-006] Entry/Exit/Hold summary report
**Source:** Stage 6 session
**Detail:** For every rebalance action (BUY, SELL, HOLD) in the portfolio activity log, generate a short natural-language summary explaining the rationale:
- **BUY:** why entering — e.g. momentum rank, gate passage, FIP score, proximity to 52w high
- **SELL:** why exiting — e.g. dropped out of top-25, failed gate, momentum reversal
- **HOLD:** why holding — e.g. still in top-25, strong rank persistence, no better candidate
Basis: prior week's signal values + rank movement week-on-week.
Framework to be refined later — initial version should be rule-based, not LLM-generated.

---

## [S8-007] Macro/asset class factor overlay
**Source:** Stage 6 session
**Detail:** Incorporate non-stock macro signals into the strategy — e.g. gold rally, metals rally, oil moves, USD/INR, FII flows. Two potential approaches:
1. **Sector rotation signal** — if metals industry rank is rising, overweight metals stocks in C4/C5 composite
2. **Regime filter** — if macro environment is risk-off (gold up, VIX up, broad market down), shift cash allocation or tighten gates
Data sources to evaluate: gold ETF (GOLDBEES.NS), metal index, RBI data, FII/DII flows from NSE.
Framework design deferred — needs separate design session before coding.

---
