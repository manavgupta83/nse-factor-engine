"""
Stage 3 — Metric: Relative Strength

Measures each stock's cumulative log return over T-252 → T-21 relative to
the equal-weighted market return over the same window.

rs_excess_ret : stock_cum_ret - market_cum_ret (equal-weighted all 500)
rs_rank_500   : percentile rank of stock_cum_ret vs all 500 symbols (1.0 = top)

Inputs : window (prices T-252 → T-21 with log_ret computed)
Returns: dataframe with columns [symbol, rs_excess_ret, rs_rank_500]
"""

import pandas as pd
import numpy as np


def compute(window: pd.DataFrame) -> pd.DataFrame:
    win = window.copy()
    win['log_ret'] = win.groupby('symbol')['close'].transform(
        lambda x: np.log(x / x.shift(1))
    )
    win = win.dropna(subset=['log_ret'])

    # Cumulative log return per symbol over window
    sym_cum_ret = (
        win.groupby('symbol')['log_ret']
        .sum()
        .rename('stock_cum_ret')
    )

    # Equal-weighted market cumulative return
    market_cum_ret = win.groupby('date')['log_ret'].mean().sum()

    result = sym_cum_ret.reset_index()
    result['rs_excess_ret'] = result['stock_cum_ret'] - market_cum_ret
    result['rs_rank_500']   = result['stock_cum_ret'].rank(pct=True)

    return result[['symbol', 'rs_excess_ret', 'rs_rank_500']]
