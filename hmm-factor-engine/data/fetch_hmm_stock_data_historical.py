"""
fetch_hmm_stock_data_historical.py
===================================
Fetches daily adjusted close prices for all Nifty 500 ever-members.
Runs once to build the full historical dataset.
For incremental live updates use fetch_hmm_stock_data_live.py (to be built).

Output
------
hmm-factor-engine/data/prices_hmm_daily.parquet         <- single canonical file
hmm-factor-engine/data/prices_hmm_daily_volume.parquet  <- single canonical file

Usage
-----
  python3 hmm-factor-engine/data/fetch_hmm_stock_data_historical.py
"""

import time
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
START_DATE    = "2013-01-01"
END_DATE      = date.today().strftime("%Y-%m-%d")   # always fetch up to today

BATCH_SIZE    = 50
SLEEP_BETWEEN = 2
RETRY_SLEEP   = 5

WEEKLY_DROP_THRESHOLD  = 0.40
VOLUME_SPIKE_THRESHOLD = 0.40

CONSTITUENT_CSV = Path("/home/ec2-user/nse-factor-engine/nifty_constituent_history/"
                       "nifty500_2005-01-01_to_2026-06-30.csv")

OUTPUT_DIR   = Path(__file__).parent
PRICE_FILE   = OUTPUT_DIR / "prices_hmm_daily.parquet"
VOLUME_FILE  = OUTPUT_DIR / "prices_hmm_daily_volume.parquet"


# ---------------------------------------------------------------------------
# Step 1 — Build universe of all ever-members
# ---------------------------------------------------------------------------
def load_all_tickers(csv_path: Path) -> list[str]:
    print(f"Reading constituent CSV: {csv_path}")
    df = pd.read_csv(csv_path)

    all_tickers = set()
    for _, row in df.iterrows():
        symbols = str(row["symbols"]).strip()
        if symbols and symbols.lower() != "nan":
            for t in symbols.split(","):
                t = t.strip()
                if t:
                    all_tickers.add(t)

    for col in ["inclusions", "exclusions"]:
        if col in df.columns:
            for val in df[col].dropna():
                for t in str(val).split(","):
                    t = t.strip()
                    if t and t.lower() != "nan":
                        all_tickers.add(t)

    tickers = sorted(all_tickers)
    print(f"  Total unique tickers ever in Nifty 500: {len(tickers)}")
    return tickers


# ---------------------------------------------------------------------------
# Step 2 — Fetch in batches
# ---------------------------------------------------------------------------
def fetch_batch(tickers_ns: list[str], start: str, end: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    raw = yf.download(
        tickers_ns,
        start=start,
        end=end,
        auto_adjust=True,
        progress=False,
        threads=True,
    )

    if raw.empty:
        return pd.DataFrame(), pd.DataFrame()

    if isinstance(raw.columns, pd.MultiIndex):
        close  = raw["Close"].copy()
        volume = raw["Volume"].copy()
    else:
        close  = raw[["Close"]].rename(columns={"Close": tickers_ns[0]})
        volume = raw[["Volume"]].rename(columns={"Volume": tickers_ns[0]})

    close.columns  = [c.replace(".NS", "") for c in close.columns]
    volume.columns = [c.replace(".NS", "") for c in volume.columns]
    close.index    = pd.to_datetime(close.index)
    volume.index   = pd.to_datetime(volume.index)

    return close, volume


# ---------------------------------------------------------------------------
# Step 3 — Corporate action quality check
# ---------------------------------------------------------------------------
def find_bad_tickers(close: pd.DataFrame, volume: pd.DataFrame) -> list[str]:
    bad = []
    for ticker in close.columns:
        c = close[ticker].dropna()
        v = volume[ticker].dropna() if ticker in volume.columns else pd.Series(dtype=float)

        if len(c) < 10:
            continue

        weekly_ret = c.pct_change(5)

        if len(v) >= 10:
            vol_ratio = v.rolling(5).sum() / v.rolling(5).sum().shift(5) - 1
        else:
            vol_ratio = pd.Series(0, index=weekly_ret.index)

        flag = (weekly_ret < -WEEKLY_DROP_THRESHOLD) & (vol_ratio > VOLUME_SPIKE_THRESHOLD)
        if flag.any():
            n_flags   = flag.sum()
            worst_ret = weekly_ret[flag].min()
            bad.append(ticker)
            print(f"  FLAG: {ticker} — {n_flags} week(s) with drop+spike "
                  f"(worst weekly ret: {worst_ret:.1%})")

    return bad


# ---------------------------------------------------------------------------
# Step 4 — Re-fetch flagged tickers individually
# ---------------------------------------------------------------------------
def refetch_solo(tickers: list[str], start: str, end: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    close_parts, volume_parts = [], []

    for ticker in tickers:
        print(f"  Re-fetching solo: {ticker}")
        time.sleep(RETRY_SLEEP)
        c, v = fetch_batch([f"{ticker}.NS"], start, end)
        if not c.empty:
            close_parts.append(c)
            volume_parts.append(v)

    if close_parts:
        return pd.concat(close_parts, axis=1), pd.concat(volume_parts, axis=1)
    return pd.DataFrame(), pd.DataFrame()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    tickers    = load_all_tickers(CONSTITUENT_CSV)
    tickers_ns = [f"{t}.NS" for t in tickers]
    total      = len(tickers_ns)

    print(f"\nFetching {total} tickers in batches of {BATCH_SIZE} ...")
    print(f"Date range: {START_DATE} to {END_DATE}\n")

    all_close, all_volume, failed = [], [], []

    for i in range(0, total, BATCH_SIZE):
        batch      = tickers_ns[i : i + BATCH_SIZE]
        batch_num  = i // BATCH_SIZE + 1
        total_batches = (total + BATCH_SIZE - 1) // BATCH_SIZE
        print(f"Batch {batch_num}/{total_batches}: {len(batch)} tickers "
              f"({batch[0]} ... {batch[-1]})")

        c, v = fetch_batch(batch, START_DATE, END_DATE)

        if c.empty:
            print(f"  WARNING: batch {batch_num} returned empty — will retry individually")
            failed.extend([t.replace(".NS", "") for t in batch])
        else:
            n_ok    = c.notna().any().sum()
            n_empty = c.isna().all().sum()
            print(f"  OK: {n_ok} tickers with data, {n_empty} empty")
            all_close.append(c)
            all_volume.append(v)

        time.sleep(SLEEP_BETWEEN)

    if failed:
        print(f"\nRetrying {len(failed)} tickers individually ...")
        c, v = refetch_solo(failed, START_DATE, END_DATE)
        if not c.empty:
            all_close.append(c)
            all_volume.append(v)

    print("\nCombining all batches ...")
    close  = pd.concat(all_close,  axis=1)
    volume = pd.concat(all_volume, axis=1)

    close  = close.loc[:,  ~close.columns.duplicated()]
    volume = volume.loc[:, ~volume.columns.duplicated()]

    close.index.name  = "date"
    volume.index.name = "date"

    print(f"  Combined shape : {close.shape}")
    print(f"  Date range     : {close.index[0].date()} -> {close.index[-1].date()}")

    print("\nRunning corporate action quality check ...")
    bad_tickers = find_bad_tickers(close, volume)

    if bad_tickers:
        print(f"\n{len(bad_tickers)} ticker(s) flagged — re-fetching solo ...")
        c_fixed, v_fixed = refetch_solo(bad_tickers, START_DATE, END_DATE)
        if not c_fixed.empty:
            for t in c_fixed.columns:
                close[t]  = c_fixed[t]
                volume[t] = v_fixed[t] if t in v_fixed.columns else volume[t]
    else:
        print("  No corporate action artifacts detected.")

    print(f"\n--- Final Summary ---")
    print(f"  Shape        : {close.shape}")
    print(f"  Date range   : {close.index[0].date()} -> {close.index[-1].date()}")
    print(f"  Tickers      : {close.shape[1]}")
    print(f"  Avg coverage : {close.notna().mean().mean()*100:.1f}%")

    print(f"\nSaving ...")
    close.to_parquet(PRICE_FILE)
    volume.to_parquet(VOLUME_FILE)
    print(f"  Prices -> {PRICE_FILE}")
    print(f"  Volume -> {VOLUME_FILE}")
    print("Done.")


if __name__ == "__main__":
    main()
