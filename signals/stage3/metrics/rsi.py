"""
Stage 3 — Metric: RSI (Wilder)

Computes Wilder's Relative Strength Index for each stock as of T.

  RS      = Wilder smoothed avg gain / Wilder smoothed avg loss over N periods
  RSI     = 100 - (100 / (1 + RS))

Implementation follows Wilder's original smoothing (equivalent to EMA with
alpha = 1/N), NOT a simple rolling average. This matches the standard
TA-Lib / TradingView definition.

Steps:
  1. Compute daily price changes from close prices.
  2. Separate gains (positive changes) and losses (absolute negative changes).
  3. Seed the first avg_gain / avg_loss as a simple mean over the first N periods.
  4. Apply Wilder smoothing for all subsequent periods:
       avg_gain[t] = (avg_gain[t-1] * (N-1) + gain[t]) / N
       avg_loss[t] = (avg_loss[t-1] * (N-1) + loss[t]) / N
  5. RS  = avg_gain / avg_loss  (avg_loss == 0 → RS = inf → RSI = 100)
  6. RSI = 100 - (100 / (1 + RS))

Inputs:
  prices : full prices.parquet dataframe (all dates up to and including T)
  T      : as-of date (pd.Timestamp) — RSI computed using all rows ≤ T
  n      : lookback period (default 14, Wilder standard)

Returns:
  dataframe with columns [symbol, rsi_14]
  Symbols with fewer than N+1 price observations return NaN.
"""

import pandas as pd
import numpy as np


def _wilder_rsi(close: pd.Series, n: int) -> float:
    """
    Compute RSI for a single symbol's close price series.
    Returns the most recent RSI value as a float, or NaN if insufficient data.
    """
    close = close.dropna().reset_index(drop=True)
    if len(close) < n + 1:
        return np.nan

    delta = close.diff().dropna()
    gain  = delta.clip(lower=0)
    loss  = (-delta).clip(lower=0)

    # Seed: simple mean of first N periods
    avg_gain = gain.iloc[:n].mean()
    avg_loss = loss.iloc[:n].mean()

    # Wilder smoothing for remaining periods
    for i in range(n, len(gain)):
        avg_gain = (avg_gain * (n - 1) + gain.iloc[i]) / n
        avg_loss = (avg_loss * (n - 1) + loss.iloc[i]) / n

    if avg_loss == 0:
        return 100.0

    rs  = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return round(rsi, 4)


def compute(prices: pd.DataFrame, T: pd.Timestamp, n: int = 14) -> pd.DataFrame:
    """
    Compute Wilder RSI-N for every symbol in prices, as of T.

    Parameters
    ----------
    prices : DataFrame with columns [symbol, date, close, ...]
    T      : latest date to include (inclusive)
    n      : RSI period (default 14)

    Returns
    -------
    DataFrame with columns [symbol, rsi_{n}]
    """
    col_name = f"rsi_{n}"

    df = prices[prices['date'] <= T][['symbol', 'date', 'close']].copy()
    df = df.sort_values(['symbol', 'date']).reset_index(drop=True)

    results = (
        df.groupby('symbol')['close']
        .apply(lambda s: _wilder_rsi(s, n))
        .reset_index()
        .rename(columns={'close': col_name})
    )

    n_null = results[col_name].isnull().sum()
    if n_null > 0:
        print(f"WARNING: {n_null} symbol(s) have NaN {col_name} (insufficient price history < {n+1} rows)")

    print(f"RSI-{n} computed for {len(results)} symbols. "
          f"Range: [{results[col_name].min():.2f}, {results[col_name].max():.2f}]")

    return results[['symbol', col_name]]
