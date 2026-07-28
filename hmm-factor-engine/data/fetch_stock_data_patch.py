"""
fetch_stock_data_patch.py
=========================
Patches prices_hmm_daily.parquet with two sets of tickers:

SET A — Renamed/merged tickers (old symbol -> new symbol on NSE)
  Fetches full history under new symbol, writes into old symbol column.
  This ensures point-in-time factor engine gets prices when constituent
  CSV references the old name.

SET B — New symbols approved manually (no old symbol mapping needed)
  Fetches and adds as new columns to the parquet.

Does NOT re-fetch tickers that already have good data.
Applies same corporate action quality check as historical script.

Output
------
Overwrites: hmm-factor-engine/data/prices_hmm_daily.parquet
Overwrites: hmm-factor-engine/data/prices_hmm_daily_volume.parquet

Usage
-----
  python3 hmm-factor-engine/data/fetch_stock_data_patch.py
"""

import time
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
START_DATE    = "2013-01-01"
END_DATE      = "2026-06-30"
RETRY_SLEEP   = 3

WEEKLY_DROP_THRESHOLD  = 0.40
VOLUME_SPIKE_THRESHOLD = 0.40

OUTPUT_DIR   = Path(__file__).parent
PRICE_FILE   = OUTPUT_DIR / "prices_hmm_daily.parquet"
VOLUME_FILE  = OUTPUT_DIR / "prices_hmm_daily_volume.parquet"

# ---------------------------------------------------------------------------
# SET A — old symbol -> new symbol (fetch under new, write into old column)
# ---------------------------------------------------------------------------
RENAME_MAP = {
    # old_symbol : new_symbol
    "8KMILES"    : "SECURKLOUD",
    "ADLABS"     : "IMAGICAA",
    "AKZOINDIA"  : "JSWDULUX",
    "APPAPER"    : "IPAPPM",
    "CENTURYTEX" : "ABREL",
    "EXCEL"      : "LANDSMILL",
    "GSKCONS"    : "HINDUNILVR",
    "GUJGASLTD"  : "GUJENERGY",
    "IBVENTURES" : "DHANI",
    "INFIBEAM"   : "CCAVENUE",
    "MERCK"      : "PGHL",
    "MOTHERSUMI" : "MOTHERSON",
    "ORIENTREF"  : "RHIM",
    "SEQUENT"    : "VIYASH",
    "SKSMICRO"   : "BHARATFIN",
    "SMLISUZU"   : "SMLMAH",
    "TATAMOTORS" : "TMPV",
    "TATASPONGE" : "TATASTLLP",
    "UCALFUEL"   : "UCAL",
    "WIDIA"      : "KENNAMET",
    "ALSTOMT&D"  : "GVT&D",
}

# ---------------------------------------------------------------------------
# SET B — new symbols to add as fresh columns
# ---------------------------------------------------------------------------
NEW_SYMBOLS = [
    "BALAMINES",
    "CEMPRO",
    "EQUITASBNK",
    "GARFIBRES",
    "GRWRHITECH",
    "ICICIBANK",
    "JUBLINGREA",
    "LTM",
    "M&M",
    "MOTILALOFS",
    "PATANJALI",
    "PENIND",
    "REPRO",
    "UJJIVANSFB",
    "HDFCBANK",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def fetch_single(symbol: str) -> tuple[pd.Series, pd.Series]:
    """Fetch close + volume for one symbol. Returns (close_series, volume_series)."""
    ticker_ns = f"{symbol}.NS"
    print(f"  Fetching {ticker_ns} ...")
    try:
        raw = yf.download(
            ticker_ns,
            start=START_DATE,
            end=END_DATE,
            auto_adjust=True,
            progress=False,
        )
        if raw.empty:
            print(f"    WARNING: no data returned for {ticker_ns}")
            return pd.Series(dtype=float, name=symbol), pd.Series(dtype=float, name=symbol)

        if isinstance(raw.columns, pd.MultiIndex):
            close  = raw["Close"].iloc[:, 0]
            volume = raw["Volume"].iloc[:, 0]
        else:
            close  = raw["Close"]
            volume = raw["Volume"]

        close.index  = pd.to_datetime(close.index)
        volume.index = pd.to_datetime(volume.index)
        close.name   = symbol
        volume.name  = symbol

        n = close.notna().sum()
        print(f"    OK: {n} trading days with data "
              f"({close.first_valid_index().date()} -> {close.last_valid_index().date()})")
        return close, volume

    except Exception as e:
        print(f"    ERROR fetching {ticker_ns}: {e}")
        return pd.Series(dtype=float, name=symbol), pd.Series(dtype=float, name=symbol)


def quality_check(close: pd.Series, volume: pd.Series, symbol: str) -> bool:
    """Returns True if ticker looks like it has a corporate action artifact."""
    c = close.dropna()
    v = volume.dropna()
    if len(c) < 10:
        return False
    weekly_ret = c.pct_change(5)
    if len(v) >= 10:
        vol_ratio = v.rolling(5).sum() / v.rolling(5).sum().shift(5) - 1
    else:
        vol_ratio = pd.Series(0, index=weekly_ret.index)
    flag = (weekly_ret < -WEEKLY_DROP_THRESHOLD) & (vol_ratio > VOLUME_SPIKE_THRESHOLD)
    if flag.any():
        print(f"    FLAG: {symbol} has {flag.sum()} week(s) with drop+spike — may need manual check")
        return True
    return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    # Load existing parquets
    print("Loading existing price parquets ...")
    prices = pd.read_parquet(PRICE_FILE)
    volume = pd.read_parquet(VOLUME_FILE)
    print(f"  Prices shape before patch: {prices.shape}")

    # Align index — use prices index as master
    master_index = prices.index

    # -----------------------------------------------------------------------
    # SET A — renamed tickers
    # -----------------------------------------------------------------------
    print(f"\n{'='*60}")
    print(f"SET A — {len(RENAME_MAP)} renamed tickers")
    print(f"{'='*60}")

    set_a_results = {}
    for old_sym, new_sym in RENAME_MAP.items():
        print(f"\n[{old_sym}] -> fetching as [{new_sym}]")
        c, v = fetch_single(new_sym)
        time.sleep(RETRY_SLEEP)

        if c.empty or c.notna().sum() == 0:
            print(f"  SKIP: no data for {new_sym}")
            continue

        quality_check(c, v, new_sym)

        # Reindex to master index
        c = c.reindex(master_index)
        v = v.reindex(master_index)

        # Write into OLD symbol column
        before = prices[old_sym].notna().sum() if old_sym in prices.columns else 0
        prices[old_sym] = c.values
        volume[old_sym] = v.values
        after = prices[old_sym].notna().sum()

        # Also add NEW symbol column if not already present
        if new_sym not in prices.columns:
            prices[new_sym] = c.values
            volume[new_sym] = v.values
            print(f"  Also added new column [{new_sym}]")

        set_a_results[old_sym] = (new_sym, before, after)
        print(f"  [{old_sym}] filled: {before} -> {after} non-NaN rows")

    # -----------------------------------------------------------------------
    # SET B — new symbols
    # -----------------------------------------------------------------------
    print(f"\n{'='*60}")
    print(f"SET B — {len(NEW_SYMBOLS)} new symbols")
    print(f"{'='*60}")

    set_b_results = {}
    for sym in NEW_SYMBOLS:
        print(f"\n[{sym}]")

        # Skip if already has good data
        if sym in prices.columns and prices[sym].notna().sum() > 100:
            print(f"  SKIP: already has {prices[sym].notna().sum()} rows — no fetch needed")
            set_b_results[sym] = ("already_exists", prices[sym].notna().sum())
            continue

        c, v = fetch_single(sym)
        time.sleep(RETRY_SLEEP)

        if c.empty or c.notna().sum() == 0:
            print(f"  SKIP: no data returned")
            set_b_results[sym] = ("no_data", 0)
            continue

        quality_check(c, v, sym)

        c = c.reindex(master_index)
        v = v.reindex(master_index)

        prices[sym] = c.values
        volume[sym] = v.values
        n = prices[sym].notna().sum()
        set_b_results[sym] = ("fetched", n)
        print(f"  Added [{sym}]: {n} non-NaN rows")

    # -----------------------------------------------------------------------
    # Summary
    # -----------------------------------------------------------------------
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")

    print("\nSET A — Renamed tickers:")
    print(f"  {'OLD':15s} {'NEW':15s} {'BEFORE':>8} {'AFTER':>8}")
    print(f"  {'-'*15} {'-'*15} {'-'*8} {'-'*8}")
    for old, (new, before, after) in set_a_results.items():
        status = "OK" if after > before else "NO CHANGE"
        print(f"  {old:15s} {new:15s} {before:>8} {after:>8}  {status}")

    print("\nSET B — New symbols:")
    print(f"  {'SYMBOL':15s} {'STATUS':15s} {'ROWS':>8}")
    print(f"  {'-'*15} {'-'*15} {'-'*8}")
    for sym, (status, n) in set_b_results.items():
        print(f"  {sym:15s} {status:15s} {n:>8}")

    print(f"\nPrices shape after patch: {prices.shape}")
    print(f"Tickers with any data   : {prices.notna().any().sum()}")
    print(f"Tickers all-NaN         : {prices.isna().all().sum()}")

    # -----------------------------------------------------------------------
    # Save
    # -----------------------------------------------------------------------
    print("\nSaving patched parquets ...")
    prices.to_parquet(PRICE_FILE)
    volume.to_parquet(VOLUME_FILE)
    print(f"  Prices -> {PRICE_FILE}")
    print(f"  Volume -> {VOLUME_FILE}")
    print("Done.")


if __name__ == "__main__":
    main()
