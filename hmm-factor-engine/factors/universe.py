"""
universe.py
===========
Shared universe construction utility for all factor scripts.

Applies two filters at each month T:
  1. Point-in-time Nifty 500 membership (from constituent CSV)
  2. ADTV filter — average daily traded value (price x volume)
     over past 63 trading days (~3 months) >= threshold

ADTV threshold is time-varying (anchored to market size):
  pre-2018        : 10cr
  2018 to 2021    : 20cr
  2022 onwards    : 30cr

Usage
-----
from factors.universe import build_universe_lookup, get_clean_universe

universe_df = build_universe_lookup(CONSTITUENT_CSV)

# At each month T:
universe = get_clean_universe(
    date           = date,
    daily_prices   = daily_prices,
    daily_volume   = daily_volume,
    universe_df    = universe_df,
    sym_map        = sym_map,
)
"""

from pathlib import Path
import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
CRORE    = 1e7   # 1 crore = 10 million

# Time-varying ADTV thresholds (cr)
# Pre-2018     : 10cr — market was smaller, liquidity was lower
# 2018-2021    : 20cr — mid-phase
# 2022-present : 30cr — current threshold
def get_adtv_threshold(date: pd.Timestamp) -> float:
    """
    Return ADTV threshold in crore for a given date.
      pre-2018     : 10cr
      2018-2021    : 20cr
      2022-present : 30cr
    """
    year = date.year
    if year < 2018:
        return 10.0
    elif year <= 2021:
        return 20.0
    else:
        return 30.0


# ---------------------------------------------------------------------------
# Step 1 — Build point-in-time universe lookup
# ---------------------------------------------------------------------------
def build_universe_lookup(csv_file: Path) -> pd.DataFrame:
    """
    Load constituent CSV and build point-in-time lookup table.

    Returns
    -------
    pd.DataFrame with columns: effective_date, symbols_list
    Sorted by effective_date ascending.
    """
    df = pd.read_csv(csv_file)
    df["effective_date"] = pd.to_datetime(df["effective_date"])
    df = df.sort_values("effective_date").reset_index(drop=True)
    df["symbols_list"] = df["symbols"].apply(
        lambda s: [x.strip() for x in str(s).split(",") if x.strip()]
    )
    return df[["effective_date", "symbols_list"]]


def get_pit_universe(date: pd.Timestamp, universe_df: pd.DataFrame) -> list:
    """
    Get point-in-time Nifty 500 universe for a given date.
    Uses most recent snapshot with effective_date <= date.
    """
    valid = universe_df[universe_df["effective_date"] <= date]
    if valid.empty:
        return []
    return valid.iloc[-1]["symbols_list"]


# ---------------------------------------------------------------------------
# Step 2 — ADTV filter
# ---------------------------------------------------------------------------
def compute_adtv(
    date: pd.Timestamp,
    symbols: list,
    daily_prices: pd.DataFrame,
    daily_volume: pd.DataFrame,
    sym_map: dict,
    adtv_days: int = 63,
) -> pd.Series:
    """
    Compute average daily traded value (price x volume) for each stock
    over the past adtv_days trading days up to and including date.

    Returns
    -------
    pd.Series — ADTV in crore, indexed by symbol
    """
    idx       = daily_prices.index
    valid_idx = idx[idx <= date]

    if len(valid_idx) < adtv_days:
        window_idx = valid_idx
    else:
        window_idx = valid_idx[-adtv_days:]

    adtv = {}
    for sym in symbols:
        col = sym_map.get(sym, sym)
        if col not in daily_prices.columns:
            continue
        if col not in daily_volume.columns:
            continue

        px  = daily_prices[col].reindex(window_idx)
        vol = daily_volume[col].reindex(window_idx)

        dtv = (px * vol).dropna()

        if len(dtv) < 10:
            continue

        adtv_val = dtv.mean() / CRORE
        if pd.notna(adtv_val) and adtv_val > 0:
            adtv[sym] = adtv_val

    return pd.Series(adtv)


# ---------------------------------------------------------------------------
# Step 3 — Combined clean universe
# ---------------------------------------------------------------------------
def get_clean_universe(
    date: pd.Timestamp,
    daily_prices: pd.DataFrame,
    daily_volume: pd.DataFrame,
    universe_df: pd.DataFrame,
    sym_map: dict,
    adtv_crore: float = None,   # if None, uses time-varying threshold
    adtv_days: int    = 63,
) -> list:
    """
    Get clean universe at month-end date T.

    Applies:
      1. Point-in-time Nifty 500 membership
      2. ADTV >= threshold (time-varying by default, or fixed if passed)
      3. Must have price data (non-NaN) at date T

    Returns
    -------
    list of ticker symbols passing all filters
    """
    # 1. Point-in-time membership
    pit = get_pit_universe(date, universe_df)
    if not pit:
        return []

    # 2. Determine ADTV threshold
    threshold = adtv_crore if adtv_crore is not None else get_adtv_threshold(date)

    # 3. ADTV filter
    adtv      = compute_adtv(date, pit, daily_prices, daily_volume, sym_map, adtv_days)
    adtv_pass = adtv[adtv >= threshold].index.tolist()

    # 4. Must have price at date T
    clean = []
    for sym in adtv_pass:
        col = sym_map.get(sym, sym)
        if col not in daily_prices.columns:
            continue
        idx   = daily_prices.index
        valid = idx[idx <= date]
        if valid.empty:
            continue
        last_px = daily_prices.loc[valid[-1], col]
        if pd.notna(last_px) and last_px > 0:
            clean.append(sym)

    return clean
