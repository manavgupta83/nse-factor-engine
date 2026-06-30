"""
Stage 3 — Metric: Weinstein Stage 2 Breakout

Classifies each stock as Stage 2 (True) or not (False) based on:
  - Weekly close price > 30-week moving average
  - 30-week MA slope is positive (MA this week > MA last week)

Both conditions must be True for weinstein_stage2 = True.

Inputs : prices (full price history), T, symbols (all 500)
Returns: dataframe with columns [symbol, weinstein_stage2]
"""

import pandas as pd
import numpy as np


def compute(prices: pd.DataFrame, T) -> pd.DataFrame:
    px = prices[prices['date'] <= T][['symbol', 'date', 'close']].copy()
    px = px.sort_values(['symbol', 'date'])

    # Weekly close = last close of each calendar week per symbol
    px['week'] = px['date'].dt.to_period('W')
    weekly = (
        px.groupby(['symbol', 'week'])['close']
        .last()
        .reset_index()
    )
    weekly = weekly.sort_values(['symbol', 'week'])

    # 30-week MA
    weekly['ma30w'] = (
        weekly.groupby('symbol')['close']
        .transform(lambda x: x.rolling(30, min_periods=20).mean())
    )
    weekly['ma30w_prev'] = (
        weekly.groupby('symbol')['ma30w']
        .transform(lambda x: x.shift(1))
    )

    weekly['ma_slope_pos']  = weekly['ma30w'] > weekly['ma30w_prev']
    weekly['price_above_ma'] = weekly['close'] > weekly['ma30w']
    weekly['weinstein_stage2'] = weekly['ma_slope_pos'] & weekly['price_above_ma']

    # Take latest week per symbol
    latest = (
        weekly.sort_values('week')
        .groupby('symbol')
        .last()
        .reset_index()[['symbol', 'weinstein_stage2']]
    )

    return latest
