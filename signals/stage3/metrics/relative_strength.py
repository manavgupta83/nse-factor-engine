"""
Stage 3 — Metric: Relative Strength

Measures each stock's cumulative log return over T-252 → T-21 relative to
(a) the equal-weighted market return and (b) its own industry's return,
over the same window.

alpha_12m1m_ew       : stock_cum_ret - market_cum_ret (equal-weighted all 500)
alpha_12m1m_industry : stock_cum_ret - industry_cum_ret (equal-weighted, same industry, self included)
momentum_rank_12m1m  : percentile rank of stock_cum_ret vs all 500 symbols (1.0 = top)

Inputs : window (prices T-252 → T-21 with log_ret computed), meta (universe_metadata)
Returns: dataframe with columns [symbol, alpha_12m1m_ew, alpha_12m1m_industry, momentum_rank_12m1m]
"""

import pandas as pd
import numpy as np


def compute(window: pd.DataFrame, meta: pd.DataFrame) -> pd.DataFrame:
    win = window.copy()
    win['log_ret'] = win.groupby('symbol')['close'].transform(
        lambda x: np.log(x / x.shift(1))
    )
    win = win.dropna(subset=['log_ret'])
    win['industry'] = win['symbol'].map(meta.set_index('symbol')['industry'])

    # Cumulative log return per symbol over window
    sym_cum_ret = (
        win.groupby('symbol')['log_ret']
        .sum()
        .rename('stock_cum_ret')
    )

    # Equal-weighted market cumulative return
    market_cum_ret = win.groupby('date')['log_ret'].mean().sum()

    # Equal-weighted industry cumulative return (self included, same as leading_industry.py)
    industry_cum_ret = (
        win.groupby(['industry', 'date'])['log_ret']
        .mean()
        .groupby('industry')
        .sum()
        .rename('industry_cum_ret')
    )

    sym_industry = meta.set_index('symbol')['industry']

    result = sym_cum_ret.reset_index()
    result['industry']         = result['symbol'].map(sym_industry)
    result['industry_cum_ret'] = result['industry'].map(industry_cum_ret)

    result['alpha_12m1m_ew']       = result['stock_cum_ret'] - market_cum_ret
    result['alpha_12m1m_industry'] = result['stock_cum_ret'] - result['industry_cum_ret']
    result['momentum_rank_12m1m']  = result['stock_cum_ret'].rank(pct=True)

    return result[['symbol', 'alpha_12m1m_ew', 'alpha_12m1m_industry', 'momentum_rank_12m1m']]
