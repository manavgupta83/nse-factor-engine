"""
Index Price Fetch — Single Source of Truth
==========================================
Fetches daily OHLCV for all 12 canonical index tickers from yfinance.
Lives at data/fetch_index_data.py (repo root).

All consumers read from data/index_prices.parquet:
  - market_movement/compute_market_metrics.py
  - hmm-factor-engine/data/fetch_hmm_nifty_indices_data.py
  - hmm-factor-engine/regime/liquidity_risk_index.py

── Date convention (locked 2026-06-30, METHODOLOGY.md) ──
RUN_DATE   = IST calendar date this script executed -> used in FILENAME
as_of_date = T, the actual latest trading day reflected in fetched data
             -> stored as a COLUMN inside the file, computed from data

Output:
    data/index_prices.parquet              (full history, all 12 tickers, incremental)
    data/index_prices_{RUN_DATE}.parquet   (dated snapshot, this run)

Canonical 12 tickers and their consumers:
  ^NSEI              market_movement
  ^CRSLDX            market_movement, hmm_nifty_indices, liquidity_risk_index
  ^NSEMDCP50         market_movement
  NIFTYMIDCAP150.NS   market_movement  (replaces stale NIFTY_MIDCAP_100.NS)
  SML100CASE.NS      market_movement
  ^NSEBANK           market_movement
  ^CNXIT             market_movement
  ^CNXPHARMA         market_movement
  ^INDIAVIX          market_movement
  ^CNX100            liquidity_risk_index
  NIFTYMIDCAP150.NS  liquidity_risk_index
  NIFTYSMLCAP250.NS  liquidity_risk_index
"""

import time
import sys
import logging
import yfinance as yf
import pandas as pd
from datetime import date
from pathlib import Path

logging.getLogger("yfinance").setLevel(logging.CRITICAL)

# ── Config ────────────────────────────────────────
START_DATE     = "2000-01-01"   # unified history start; yfinance clips per ticker
SLEEP_SECS     = 2
MAX_RETRIES    = 2
EMPTY_GAP_DAYS = 7              # empty df within this gap = UP_TO_DATE, not failure
MKT_CLOSE_HOUR = 15
MKT_CLOSE_MIN  = 30

END_DATE = date.today()
RUN_DATE = END_DATE.strftime("%d%m%Y")

INDEX_SYMBOLS = [
    "^NSEI", "^CRSLDX", "^NSEMDCP50", "NIFTYMIDCAP150.NS",
    "SML100CASE.NS", "^NSEBANK", "^CNXIT", "^CNXPHARMA", "^INDIAVIX",
    "^CNX100", "NIFTYMIDCAP150.NS", "NIFTYSMLCAP250.NS",
]

# Repo-root guard
if not Path("signals").is_dir():
    sys.exit(
        "ERROR: 'signals/' not found in current directory.\n"
        "Run from repo root: cd /home/ec2-user/nse-factor-engine/"
    )

DATA_DIR      = Path("data")
PRICES_PATH   = DATA_DIR / "index_prices.parquet"
OUTPUT_PATH   = DATA_DIR / "index_prices_{}.parquet".format(RUN_DATE)
LAST_RUN_PATH = DATA_DIR / "last_run_date_index.txt"
FAILED_PATH   = DATA_DIR / "failed_index_symbols_{}.csv".format(RUN_DATE)


def market_closed_today():
    now_ist    = pd.Timestamp.now(tz="Asia/Kolkata")
    close_time = now_ist.normalize() + pd.Timedelta(hours=MKT_CLOSE_HOUR, minutes=MKT_CLOSE_MIN)
    return now_ist >= close_time


def classify_empty(fetch_start):
    gap_days = (END_DATE - fetch_start).days
    return "UP_TO_DATE" if gap_days <= EMPTY_GAP_DAYS else "NO_DATA"


def fetch_index(symbol, fetch_start):
    df = pd.DataFrame()
    try:
        raw = yf.download(
            tickers     = symbol,
            start       = fetch_start.strftime("%Y-%m-%d"),
            end         = END_DATE.strftime("%Y-%m-%d"),
            interval    = "1d",
            auto_adjust = True,
            progress    = False,
            threads     = False,
        )
        if raw.empty:
            return df, classify_empty(fetch_start), "yfinance returned empty dataframe"

        raw.columns    = [field for field, ticker in raw.columns]
        raw.index.name = "date"
        raw            = raw.reset_index()
        raw.columns    = [c.lower() for c in raw.columns]
        raw["symbol"]  = symbol
        raw            = raw[["symbol", "date", "open", "high", "low", "close", "volume"]]
        raw            = raw.dropna(subset=["open", "high", "low", "close"])
        df = raw

    except Exception as e:
        return df, "DOWNLOAD_ERROR", str(e)

    return df, None, None


# ── Idempotency guard ─────────────────────────────
print("=" * 60)
print("Index Price Fetch — Single Source of Truth")
print("Run Date : {}".format(END_DATE))
print("=" * 60)

if LAST_RUN_PATH.exists():
    last_run = LAST_RUN_PATH.read_text().strip()
    if last_run == END_DATE.strftime("%Y-%m-%d"):
        print("\n      Already ran today ({}). Nothing to do. Exiting.".format(last_run))
        sys.exit(0)
    else:
        print("      Last run : {} -- proceeding".format(last_run))
else:
    print("      No last run date found -- first run")

DATA_DIR.mkdir(parents=True, exist_ok=True)

# ── Load existing prices ──────────────────────────
if PRICES_PATH.exists():
    existing_prices = pd.read_parquet(PRICES_PATH)
    existing_prices["date"] = pd.to_datetime(existing_prices["date"])
    last_dates = existing_prices.groupby("symbol")["date"].max()
    print("\n[1/3] Existing : {} rows | {} tickers".format(
        existing_prices.shape[0], len(last_dates)))
else:
    existing_prices = pd.DataFrame()
    last_dates      = pd.Series(dtype="datetime64[ns]")
    print("\n[1/3] No existing data -- full fetch from {} for {} tickers".format(
        START_DATE, len(INDEX_SYMBOLS)))


def compute_fetch_start(symbol):
    if symbol in last_dates.index:
        return (last_dates[symbol] + pd.Timedelta(days=1)).date(), "INCR"
    return pd.to_datetime(START_DATE).date(), "FULL"


# ── Fetch loop ────────────────────────────────────
print("\n[2/3] Fetching {} tickers...".format(len(INDEX_SYMBOLS)))
if not market_closed_today():
    print("      NOTE: before market close -- fetching up to last trading day.")

new_price_rows = []
failed_symbols = {}

for idx, symbol in enumerate(INDEX_SYMBOLS, 1):
    try:
        fetch_start, mode = compute_fetch_start(symbol)

        if fetch_start > END_DATE:
            print("  [{:02d}] {:25s} UP TO DATE".format(idx, symbol))
            time.sleep(SLEEP_SECS)
            continue

        if fetch_start == END_DATE and not market_closed_today():
            print("  [{:02d}] {:25s} UP TO DATE (pre-close)".format(idx, symbol))
            time.sleep(SLEEP_SECS)
            continue

        df, failure, errmsg = fetch_index(symbol, fetch_start)

        if failure == "UP_TO_DATE":
            print("  [{:02d}] {:25s} UP TO DATE".format(idx, symbol))
            time.sleep(SLEEP_SECS)
            continue

        if failure in ("NO_DATA", "DOWNLOAD_ERROR"):
            failed_symbols[symbol] = {"failure_type": failure, "error_message": errmsg, "attempts": 1}
            print("  [{:02d}] {:25s} {} : {}".format(idx, symbol, failure, errmsg))
            time.sleep(SLEEP_SECS)
            continue

        new_price_rows.append(df)
        print("  [{:02d}] {:25s} {} {} rows".format(idx, symbol, mode, len(df)))

    except Exception as e:
        failed_symbols[symbol] = {"failure_type": "DOWNLOAD_ERROR", "error_message": str(e), "attempts": 1}
        print("  [{:02d}] {:25s} ERROR : {}".format(idx, symbol, str(e)))

    time.sleep(SLEEP_SECS)

# ── Retry loop ────────────────────────────────────
if failed_symbols:
    print("\n-- RETRY: {} failed tickers --".format(len(failed_symbols)))
    for attempt in range(1, MAX_RETRIES + 1):
        if not failed_symbols:
            break
        print("\n  Retry attempt {} of {}...".format(attempt, MAX_RETRIES))
        still_failing = {}
        for symbol, rec in failed_symbols.items():
            fetch_start, _ = compute_fetch_start(symbol)
            df, failure, errmsg = fetch_index(symbol, fetch_start)
            if failure == "UP_TO_DATE":
                print("  {:25s} UP TO DATE on retry".format(symbol))
                time.sleep(SLEEP_SECS)
                continue
            if failure in ("NO_DATA", "DOWNLOAD_ERROR"):
                rec["attempts"]      += 1
                rec["failure_type"]   = failure
                rec["error_message"]  = errmsg
                still_failing[symbol] = rec
                print("  {:25s} {} (attempt {})".format(symbol, failure, rec["attempts"]))
                time.sleep(SLEEP_SECS)
                continue
            new_price_rows.append(df)
            print("  {:25s} SUCCESS on retry {}".format(symbol, attempt))
            time.sleep(SLEEP_SECS)
        failed_symbols = still_failing

if failed_symbols:
    failed_df = pd.DataFrame([{"symbol": s, **v} for s, v in failed_symbols.items()])
    failed_df.to_csv(FAILED_PATH, index=False)
    print("\n  {} tickers still failing -- saved to {}".format(len(failed_symbols), FAILED_PATH))
else:
    if FAILED_PATH.exists():
        FAILED_PATH.unlink()

# ── Merge and save ────────────────────────────────
print("\n[3/3] Saving...")

if new_price_rows:
    new_prices = pd.concat(new_price_rows, ignore_index=True)
    combined   = pd.concat([existing_prices, new_prices], ignore_index=True) if not existing_prices.empty else new_prices
else:
    combined = existing_prices

if combined.empty:
    print("      No data -- nothing to save. Exiting.")
    sys.exit(1)

combined["date"] = pd.to_datetime(combined["date"])
combined = combined.drop_duplicates(subset=["symbol", "date"], keep="last")
combined = combined.sort_values(["symbol", "date"]).reset_index(drop=True)

combined.to_parquet(PRICES_PATH, index=False)
print("      {} : {} rows".format(PRICES_PATH.name, combined.shape[0]))

as_of_date     = combined["date"].max().date()
dated_snapshot = combined.copy()
dated_snapshot["as_of_date"] = as_of_date
dated_snapshot["run_date"]   = END_DATE
dated_snapshot.to_parquet(OUTPUT_PATH, index=False)
print("      {} : {} rows | as_of_date={} | run_date={}".format(
    OUTPUT_PATH.name, dated_snapshot.shape[0], as_of_date, END_DATE))

if not failed_symbols:
    LAST_RUN_PATH.write_text(END_DATE.strftime("%Y-%m-%d"))
    print("\n      last_run_date_index.txt updated : {}".format(END_DATE))
else:
    print("\n      last_run_date_index.txt NOT updated -- {} tickers still failing".format(len(failed_symbols)))

print("\n" + "=" * 60)
print("SUMMARY")
print("  Total tickers        : {}".format(len(INDEX_SYMBOLS)))
print("  Fetched successfully : {}".format(len(INDEX_SYMBOLS) - len(failed_symbols)))
print("  Failed after retries : {}".format(len(failed_symbols)))
if failed_symbols:
    print("  Failed tickers       : {}".format(list(failed_symbols.keys())))
print("  as_of_date (T)       : {}".format(as_of_date))
print("  RUN_DATE (filename)  : {}".format(RUN_DATE))
print("=" * 60)
