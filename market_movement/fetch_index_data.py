"""
Market Movement — Index & VIX Price Fetch
Separate from main NSE Factor Engine pipeline (Stage 8 scope).

Fetches daily OHLCV for benchmark/sectoral indices + India VIX.
No market cap, no ADTV, no in_universe gating -- price history only.
Metrics computation happens in a separate script; this is fetch-only.

── Date convention (locked 2026-06-30, METHODOLOGY.md) ──
RUN_DATE   = IST calendar date this script executed -> used in FILENAME
as_of_date = T, the actual latest trading day reflected in fetched data
             -> stored as a COLUMN inside the file, computed from data
             (never assumed equal to RUN_DATE; can lag if today's data
             isn't posted yet, same as the rest of the pipeline).

Output:
    market_movement/data/index_prices.parquet              (rolling store, all history)
    market_movement/data/index_prices_{RUN_DATE}.parquet    (dated snapshot, this run)
"""

import time
import sys
import logging
import yfinance as yf
import pandas as pd
from datetime import date
from dateutil.relativedelta import relativedelta
from pathlib import Path

# Silence yfinance's internal logger. Its "possibly delisted; no price data
# found" message fires on ordinary empty-range responses (e.g. weekend/
# not-yet-closed trading day) as well as genuine delistings -- indistinguishable
# from the outside, and misleading in the common case. This script's own
# classify_empty()/failure handling covers the real logic; the raw yfinance
# message is suppressed so it doesn't read as an error when it isn't one.
logging.getLogger("yfinance").setLevel(logging.CRITICAL)

# ── Config ────────────────────────────────────────
FULL_START_MONTHS = 15          # matches stock-side lookback (run_universe.py)
SLEEP_SECS        = 2
MAX_RETRIES       = 2
EMPTY_GAP_DAYS    = 7            # empty df within this gap = UP TO DATE, not failure
MKT_CLOSE_HOUR    = 15
MKT_CLOSE_MIN     = 30

END_DATE   = date.today()
FULL_START = END_DATE - relativedelta(months=FULL_START_MONTHS)
RUN_DATE   = END_DATE.strftime("%d%m%Y")     # filename convention -- IST run date, DDMMYYYY

# Final ticker list -- literal, as provided. No suffixing.
# Mix of "^"-prefixed index tickers and ".NS"-suffixed tickers is intentional.
INDEX_SYMBOLS = [
    "^NSEI", "^CRSLDX", "^NSEMDCP50", "NIFTY_MIDCAP_100.NS",
    "SML100CASE.NS", "^NSEBANK", "^CNXIT",
    "^CNXPHARMA", "^INDIAVIX",
]

# Repo-root guard: this script uses relative paths and must be run from
# /home/ec2-user/nse-factor-engine/ (repo root) -- same assumption run_universe.py
# and run_backtest.py make. Fail loudly here instead of silently writing data
# to the wrong place if run from the wrong cwd.
if not Path("signals").is_dir():
    sys.exit(
        "ERROR: 'signals/' not found in current directory.\n"
        "This script must be run from the repo root (nse-factor-engine/), e.g.:\n"
        "    cd /home/ec2-user/nse-factor-engine/\n"
        "    python market_movement/fetch_index_data.py"
    )

DATA_DIR      = Path("market_movement/data")
PRICES_PATH   = DATA_DIR / "index_prices.parquet"
OUTPUT_PATH   = DATA_DIR / "index_prices_{}.parquet".format(RUN_DATE)
LAST_RUN_PATH = DATA_DIR / "last_run_date.txt"
FAILED_PATH   = DATA_DIR / "failed_index_symbols_{}.csv".format(RUN_DATE)   # DDMMYYYY, consistent


def market_closed_today():
    """True if current IST time is after today's market close (3:30 PM).
    Reused as-is from run_universe.py for consistency."""
    now_ist    = pd.Timestamp.now(tz="Asia/Kolkata")
    close_time = now_ist.normalize() + pd.Timedelta(hours=MKT_CLOSE_HOUR, minutes=MKT_CLOSE_MIN)
    return now_ist >= close_time


def classify_empty(fetch_start):
    """Empty df within EMPTY_GAP_DAYS of END_DATE = UP_TO_DATE, not a real failure."""
    gap_days = (END_DATE - fetch_start).days
    return "UP_TO_DATE" if gap_days <= EMPTY_GAP_DAYS else "NO_DATA"


def fetch_index(symbol, fetch_start):
    """
    Fetch OHLCV only -- no .info call (indices/VIX have no market cap to check).
    Returns (df, failure_type, error_message)
    failure_type: None | NO_DATA | DOWNLOAD_ERROR | UP_TO_DATE
    """
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
        # NOTE: deliberately no "volume != 0" filter here (unlike run_universe.py).
        # India VIX volume is not a meaningful/comparable concept the way stock volume is --
        # filtering on it could silently drop legitimate VIX rows.
        df = raw

    except Exception as e:
        return df, "DOWNLOAD_ERROR", str(e)

    return df, None, None


# ── Step 0: Idempotency guard ─────────────────────
print("=" * 60)
print("Market Movement -- Index & VIX Fetch")
print("Run Date : {}".format(END_DATE))
print("=" * 60)

if LAST_RUN_PATH.exists():
    last_run = LAST_RUN_PATH.read_text().strip()
    if last_run == END_DATE.strftime("%Y-%m-%d"):
        print("\n      Run already completed today (last_run_date = {}). Nothing to do. Exiting.".format(last_run))
        sys.exit(0)
    else:
        print("      Last run date : {} -- proceeding".format(last_run))
else:
    print("      No last run date found -- first run")

DATA_DIR.mkdir(parents=True, exist_ok=True)

# ── Step 1: Load existing prices ──────────────────
if PRICES_PATH.exists():
    existing_prices = pd.read_parquet(PRICES_PATH)
    existing_prices["date"] = pd.to_datetime(existing_prices["date"])
    last_dates = existing_prices.groupby("symbol")["date"].max()
    print("\n[1/3] Existing prices : {} rows | {} tickers".format(
        existing_prices.shape[0], len(last_dates)))
else:
    existing_prices = pd.DataFrame()
    last_dates      = pd.Series(dtype="datetime64[ns]")
    print("\n[1/3] No existing prices -- full {}M fetch for all {} tickers".format(
        FULL_START_MONTHS, len(INDEX_SYMBOLS)))


def compute_fetch_start(symbol):
    if symbol in last_dates.index:
        return (last_dates[symbol] + pd.Timedelta(days=1)).date(), "INCR"
    return FULL_START, "FULL"


# ── Step 2: Fetch loop ────────────────────────────
print("\n[2/3] Processing {} tickers...".format(len(INDEX_SYMBOLS)))
if not market_closed_today():
    print("      NOTE: Current IST time is before market close (3:30 PM).")
    print("            Today's data not yet available -- fetching up to last trading day.")

new_price_rows = []
failed_symbols = {}

for idx, symbol in enumerate(INDEX_SYMBOLS, 1):
    try:
        fetch_start, mode = compute_fetch_start(symbol)

        if fetch_start > END_DATE:
            print("  [{:02d}] {:20s} UP TO DATE".format(idx, symbol))
            time.sleep(SLEEP_SECS)
            continue

        if fetch_start == END_DATE and not market_closed_today():
            print("  [{:02d}] {:20s} UP TO DATE (pre-close)".format(idx, symbol))
            time.sleep(SLEEP_SECS)
            continue

        df, failure, errmsg = fetch_index(symbol, fetch_start)

        if failure == "UP_TO_DATE":
            print("  [{:02d}] {:20s} UP TO DATE".format(idx, symbol))
            time.sleep(SLEEP_SECS)
            continue

        if failure in ("NO_DATA", "DOWNLOAD_ERROR"):
            failed_symbols[symbol] = {"failure_type": failure, "error_message": errmsg, "attempts": 1}
            print("  [{:02d}] {:20s} {} : {}".format(idx, symbol, failure, errmsg))
            time.sleep(SLEEP_SECS)
            continue

        new_price_rows.append(df)
        print("  [{:02d}] {:20s} {} {} rows".format(idx, symbol, mode, len(df)))

    except Exception as e:
        failed_symbols[symbol] = {"failure_type": "DOWNLOAD_ERROR", "error_message": str(e), "attempts": 1}
        print("  [{:02d}] {:20s} ERROR : {}".format(idx, symbol, str(e)))

    time.sleep(SLEEP_SECS)

# ── Retry loop ─────────────────────────────────────
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
                print("  {:20s} UP TO DATE on retry".format(symbol))
                time.sleep(SLEEP_SECS)
                continue
            if failure in ("NO_DATA", "DOWNLOAD_ERROR"):
                rec["attempts"] += 1
                rec["failure_type"]  = failure
                rec["error_message"] = errmsg
                still_failing[symbol] = rec
                print("  {:20s} {} (attempt {})".format(symbol, failure, rec["attempts"]))
                time.sleep(SLEEP_SECS)
                continue
            new_price_rows.append(df)
            print("  {:20s} SUCCESS on retry {}".format(symbol, attempt))
            time.sleep(SLEEP_SECS)
        failed_symbols = still_failing

if failed_symbols:
    failed_df = pd.DataFrame([{"symbol": s, **v} for s, v in failed_symbols.items()])
    failed_df.to_csv(FAILED_PATH, index=False)
    print("\n  {} tickers still failing -- saved to {}".format(len(failed_symbols), FAILED_PATH))
else:
    if FAILED_PATH.exists():
        FAILED_PATH.unlink()

# ── Step 3: Merge, stamp as_of_date, save ─────────
print("\n[3/3] Saving...")

if new_price_rows:
    new_prices = pd.concat(new_price_rows, ignore_index=True)
    combined = pd.concat([existing_prices, new_prices], ignore_index=True) if not existing_prices.empty else new_prices
else:
    combined = existing_prices

if combined.empty:
    print("      No data available at all -- nothing to save. Exiting.")
    sys.exit(1)

combined["date"] = pd.to_datetime(combined["date"])
combined = combined.drop_duplicates(subset=["symbol", "date"], keep="last")
combined = combined.sort_values(["symbol", "date"]).reset_index(drop=True)

# Rolling full-history store -- undated filename, always overwritten
combined.to_parquet(PRICES_PATH, index=False)
print("      {} : {} rows".format(PRICES_PATH.name, combined.shape[0]))

# as_of_date (T) = actual latest trading date reflected in fetched data.
# Computed from data, NOT date.today() -- per pipeline-wide convention
# (METHODOLOGY.md, locked 2026-06-30). Filename uses RUN_DATE; this column carries T.
as_of_date = combined["date"].max().date()

dated_snapshot = combined.copy()
dated_snapshot["as_of_date"] = as_of_date
dated_snapshot["run_date"]   = END_DATE

dated_snapshot.to_parquet(OUTPUT_PATH, index=False)
print("      {} : {} rows | as_of_date={} | run_date={}".format(
    OUTPUT_PATH.name, dated_snapshot.shape[0], as_of_date, END_DATE))

# ── Update last run date ───────────────────────────
if not failed_symbols:
    LAST_RUN_PATH.write_text(END_DATE.strftime("%Y-%m-%d"))
    print("\n      last_run_date.txt updated : {}".format(END_DATE))
else:
    print("\n      last_run_date.txt NOT updated -- {} tickers still failing".format(len(failed_symbols)))

# ── Summary ────────────────────────────────────────
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
