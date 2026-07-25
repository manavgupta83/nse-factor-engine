"""
Stage 3 — Metric: Bollinger Bands

Computes 20-day Bollinger Bands and derived metrics for each stock as of T.

  Middle Band  = 20D SMA of close as of T
  Std_20       = 20D rolling std dev of close (ddof=1)
  Upper Band   = Middle + (2 × Std_20)
  Lower Band   = Middle - (2 × Std_20)
  %B           = (Close_T - Lower Band) / (Upper Band - Lower Band)
  Bandwidth    = (Upper Band - Lower Band) / Middle × 100  [normalised width]

Interpretation (%B):
  > 1.0     : Price above upper band — extended
  0.5 – 1.0 : Upper half of bands — bullish
  0.0 – 0.5 : Lower half of bands — cautious
  < 0.0     : Price below lower band — oversold

Bandwidth:
  Contracting to multi-week low → squeeze → big move imminent, direction unknown
  Expanding after squeeze       → move has started; watch %B for direction

Breakout setup (all three required):
  1. bb_bandwidth contracting for ≥3 weeks (squeeze)
  2. bb_pct_b crosses above 1.0 (first close outside upper band)
  3. MFI rising above 55 (volume participating)

False breakout:
  bb_pct_b > 1.0 but MFI < 45 → price above band without volume → likely snap back

Combined with EMA distance:
  bb_pct_b > 1.0 + dist_ema_50 > 20% = doubly extended, highest reversion risk

Inputs:
  prices : full prices.parquet dataframe (all dates up to and including T)
  T      : as-of date (pd.Timestamp)

Returns:
  DataFrame with columns [symbol, bb_middle, bb_upper, bb_lower, bb_pct_b, bb_bandwidth]
  Symbols with fewer than 20 price observations return NaN.
  If upper == lower (zero std), %B and bandwidth are NaN.
"""

import pandas as pd
import numpy as np

BB_PERIOD = 20
BB_STD    = 2


def _compute_bb(close: pd.Series) -> dict:
    """
    Compute Bollinger Band values for a single symbol's close series.
    Returns dict of {bb_middle, bb_upper, bb_lower, bb_pct_b, bb_bandwidth},
    all NaN if insufficient data.
    """
    close = close.dropna().reset_index(drop=True)
    nan_row = dict(bb_middle=np.nan, bb_upper=np.nan, bb_lower=np.nan,
                   bb_pct_b=np.nan, bb_bandwidth=np.nan)

    if len(close) < BB_PERIOD:
        return nan_row

    window = close.iloc[-BB_PERIOD:]
    middle = window.mean()
    std    = window.std(ddof=1)
    upper  = middle + BB_STD * std
    lower  = middle - BB_STD * std
    close_t = close.iloc[-1]

    band_width = upper - lower

    if band_width == 0:
        pct_b     = np.nan
        bandwidth = np.nan
    else:
        pct_b     = round((close_t - lower) / band_width, 4)
        bandwidth = round(band_width / middle * 100, 4) if middle != 0 else np.nan

    return dict(
        bb_middle   = round(float(middle), 4),
        bb_upper    = round(float(upper),  4),
        bb_lower    = round(float(lower),  4),
        bb_pct_b    = pct_b,
        bb_bandwidth= bandwidth,
    )


def compute(prices: pd.DataFrame, T: pd.Timestamp) -> pd.DataFrame:
    """
    Compute Bollinger Band metrics for every symbol in prices, as of T.

    Parameters
    ----------
    prices : DataFrame with columns [symbol, date, close, ...]
    T      : latest date to include (inclusive)

    Returns
    -------
    DataFrame with columns [symbol, bb_middle, bb_upper, bb_lower, bb_pct_b, bb_bandwidth]
    """
    df = prices[prices['date'] <= T][['symbol', 'date', 'close']].copy()
    df = df.sort_values(['symbol', 'date']).reset_index(drop=True)

    records = []
    for symbol, grp in df.groupby('symbol', sort=False):
        row = _compute_bb(grp['close'])
        row['symbol'] = symbol
        records.append(row)

    results = pd.DataFrame(records)

    for col in ['bb_middle', 'bb_pct_b', 'bb_bandwidth']:
        n_null = results[col].isnull().sum()
        if n_null > 0:
            print(f"WARNING: {n_null} symbol(s) have NaN {col} (< {BB_PERIOD} price rows or zero std)")

    valid_pct = results['bb_pct_b'].dropna()
    valid_bw  = results['bb_bandwidth'].dropna()
    if len(valid_pct):
        print(f"bb_pct_b   : [{valid_pct.min():.4f}, {valid_pct.max():.4f}]")
    if len(valid_bw):
        print(f"bb_bandwidth: [{valid_bw.min():.4f}, {valid_bw.max():.4f}]")

    return results[['symbol', 'bb_middle', 'bb_upper', 'bb_lower', 'bb_pct_b', 'bb_bandwidth']]
