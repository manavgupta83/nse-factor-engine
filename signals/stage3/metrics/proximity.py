"""
Stage 3 — Metric: 52-Week High Proximity

Inputs : prices (full), T, T_252
Returns: dataframe with columns [symbol, proximity_52w_high]
"""

import pandas as pd
import numpy as np


def compute(prices: pd.DataFrame, T, T_252) -> pd.DataFrame:
    close_T  = prices[prices['date'] == T][['symbol', 'close']].rename(columns={'close': 'close_T'})
    full_win = prices[(prices['date'] >= T_252) & (prices['date'] <= T)]
    high_52w = (
        full_win.groupby('symbol')['high']
        .max()
        .reset_index()
        .rename(columns={'high': 'high_52w'})
    )
    prox_df = close_T.merge(high_52w, on='symbol', how='left')
    prox_df['proximity_52w_high'] = prox_df['close_T'] / prox_df['high_52w']
    return prox_df[['symbol', 'proximity_52w_high']]
