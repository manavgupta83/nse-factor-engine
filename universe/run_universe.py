import time
import yfinance as yf
import pandas as pd
from datetime import date
from dateutil.relativedelta import relativedelta
from pathlib import Path

# ── Config ────────────────────────────────────────
SUFFIX          = ".NS"
END_DATE        = date.today()
FULL_START      = END_DATE - relativedelta(months=15)
SLEEP_SECS      = 2
MKTCAP_FLOOR    = 500
ADTV_FLOOR      = 10
ADTV_WINDOW     = 63
MAX_RETRIES     = 2
EMPTY_GAP_DAYS  = 7          # empty df within this gap = UP TO DATE, not failure
MKT_CLOSE_HOUR  = 15
MKT_CLOSE_MIN   = 30

PRICES_PATH     = Path("data/prices.parquet")
METADATA_PATH   = Path("data/universe_metadata.parquet")
ADTV_PATH       = Path("data/adtv.parquet")
UNIVERSE_DIR    = Path("universe")
SYMBOLS_CSV     = Path("data/raw/nifty500_symbols.csv")
LAST_RUN_PATH   = Path("data/last_run_date.txt")
FAILED_PATH     = Path("data/failed_symbols_{}.csv".format(END_DATE.strftime("%Y%m%d")))


def market_closed_today():
    """True if current IST time is after today's market close (3:30 PM)."""
    now_ist     = pd.Timestamp.now(tz="Asia/Kolkata")
    close_time  = now_ist.normalize() + pd.Timedelta(hours=MKT_CLOSE_HOUR, minutes=MKT_CLOSE_MIN)
    return now_ist >= close_time


def classify_empty(fetch_start):
    """
    Decide how to treat an empty dataframe.
    Returns 'UP_TO_DATE' or 'NO_DATA'.
    """
    gap_days = (END_DATE - fetch_start).days
    if gap_days <= EMPTY_GAP_DAYS:
        return "UP_TO_DATE"
    return "NO_DATA"


def fetch_symbol(symbol, fetch_start, fetch_info=True):
    """
    Fetch market cap info and/or OHLCV for a symbol.
    Returns (info_dict, price_df, failure_type, error_message)
    failure_type: None | INFO_ERROR | NO_DATA | DOWNLOAD_ERROR | UP_TO_DATE
    """
    ticker_str = "{}{}".format(symbol, SUFFIX)
    info       = {}
    df         = pd.DataFrame()

    if fetch_info:
        try:
            info = yf.Ticker(ticker_str).info
        except Exception as e:
            return info, df, "INFO_ERROR", str(e)

    try:
        raw = yf.download(
            tickers     = ticker_str,
            start       = fetch_start.strftime("%Y-%m-%d"),
            end         = END_DATE.strftime("%Y-%m-%d"),
            interval    = "1d",
            auto_adjust = True,
            progress    = False,
        )
        if raw.empty:
            return info, df, classify_empty(fetch_start), "yfinance returned empty dataframe"

        raw.columns    = [field for field, ticker in raw.columns]
        raw.index.name = "date"
        raw            = raw.reset_index()
        raw.columns    = [c.lower() for c in raw.columns]
        raw["symbol"]  = symbol
        raw            = raw[["symbol", "date", "open", "high", "low", "close", "volume"]]
        raw            = raw[raw["volume"] != 0]
        raw            = raw.dropna(subset=["open", "high", "low", "close", "volume"])
        df             = raw

    except Exception as e:
        return info, df, "DOWNLOAD_ERROR", str(e)

    return info, df, None, None


# ── Step 0: Load symbols ──────────────────────────
print("=" * 60)
print("NSE Factor Engine - Universe & Liquidity")
print("Run Date : {}".format(END_DATE))
print("=" * 60)

print("\n[0/5] Loading symbol list...")
if not SYMBOLS_CSV.exists():
    raise FileNotFoundError("Symbol list not found at {}".format(SYMBOLS_CSV))
SYMBOLS = pd.read_csv(SYMBOLS_CSV)["symbol"].tolist()
print("      {} symbols loaded from {}".format(len(SYMBOLS), SYMBOLS_CSV))

# ── Step 0b: Check last run date ──────────────────
skip_price_fetch = False
if LAST_RUN_PATH.exists():
    last_run = LAST_RUN_PATH.read_text().strip()
    if last_run == END_DATE.strftime("%Y-%m-%d"):
        print("\n      Run already completed today (last_run_date = {}). Nothing to do. Exiting.".format(last_run))
        import sys
        sys.exit(0)
    else:
        print("      Last run date : {} -- proceeding".format(last_run))
else:
    print("      No last run date found -- first run")

# ── Step 1: Load existing prices ──────────────────
print("\n[1/5] Checking existing prices...")
if PRICES_PATH.exists():
    existing_prices = pd.read_parquet(PRICES_PATH)
    existing_prices["date"] = pd.to_datetime(existing_prices["date"])
    last_dates = existing_prices.groupby("symbol")["date"].max()
    print("      Existing prices : {} rows | {} symbols".format(
        existing_prices.shape[0], len(last_dates)))
else:
    existing_prices = pd.DataFrame()
    last_dates      = pd.Series(dtype="datetime64[ns]")
    print("      No existing prices -- full 15M fetch for all qualifying symbols")

# ── Step 2: Price fetch loop ──────────────────────
metadata_rows  = []
new_price_rows = []
failed_symbols = {}

def compute_fetch_start(symbol):
    if symbol in last_dates.index:
        return (last_dates[symbol] + pd.Timedelta(days=1)).date(), "INCR"
    return FULL_START, "FULL"

if not skip_price_fetch:
    print("\n[2/5] Processing {} symbols...".format(len(SYMBOLS)))
    if not market_closed_today():
        print("      NOTE: Current IST time is before market close (3:30 PM).")
        print("            Today's data not yet available -- fetching up to last trading day.")
    print("      Market cap first -> prices only if >= Rs {} Cr\n".format(MKTCAP_FLOOR))

    for idx, symbol in enumerate(SYMBOLS, 1):
        try:
            fetch_start, mode = compute_fetch_start(symbol)

            # Rule 1: fetch_start in the future -> up to date
            if fetch_start > END_DATE:
                # still need market cap for metadata
                info, _, failure, errmsg = fetch_symbol(symbol, fetch_start, fetch_info=True)
                if failure == "INFO_ERROR":
                    failed_symbols[symbol] = {"failure_type": "INFO_ERROR",
                                              "error_message": errmsg, "attempts": 1}
                    print("  [{:03d}] {:20s} INFO_ERROR : {}".format(idx, symbol, errmsg))
                    time.sleep(SLEEP_SECS)
                    continue
                mktcap    = info.get("marketCap") or info.get("nonDilutedMarketCap", 0) or 0
                mktcap_cr = round(mktcap / 1e7, 0)
                metadata_rows.append({"symbol": symbol,
                                      "company_name": info.get("longName"),
                                      "industry": info.get("sector"),
                                      "market_cap_cr": mktcap_cr})
                print("  [{:03d}] {:20s} Rs {:>10,.0f} Cr  UP TO DATE".format(idx, symbol, mktcap_cr))
                time.sleep(SLEEP_SECS)
                continue

            # Rule 2: fetch_start == today and market not closed yet -> up to date
            if fetch_start == END_DATE and not market_closed_today():
                info, _, failure, errmsg = fetch_symbol(symbol, fetch_start, fetch_info=True)
                if failure == "INFO_ERROR":
                    failed_symbols[symbol] = {"failure_type": "INFO_ERROR",
                                              "error_message": errmsg, "attempts": 1}
                    print("  [{:03d}] {:20s} INFO_ERROR : {}".format(idx, symbol, errmsg))
                    time.sleep(SLEEP_SECS)
                    continue
                mktcap    = info.get("marketCap") or info.get("nonDilutedMarketCap", 0) or 0
                mktcap_cr = round(mktcap / 1e7, 0)
                metadata_rows.append({"symbol": symbol,
                                      "company_name": info.get("longName"),
                                      "industry": info.get("sector"),
                                      "market_cap_cr": mktcap_cr})
                print("  [{:03d}] {:20s} Rs {:>10,.0f} Cr  UP TO DATE (pre-close)".format(idx, symbol, mktcap_cr))
                time.sleep(SLEEP_SECS)
                continue

            # Normal fetch
            info, df, failure, errmsg = fetch_symbol(symbol, fetch_start, fetch_info=True)

            if failure == "INFO_ERROR":
                failed_symbols[symbol] = {"failure_type": "INFO_ERROR",
                                          "error_message": errmsg, "attempts": 1}
                print("  [{:03d}] {:20s} INFO_ERROR : {}".format(idx, symbol, errmsg))
                time.sleep(SLEEP_SECS)
                continue

            mktcap       = info.get("marketCap") or info.get("nonDilutedMarketCap", 0) or 0
            mktcap_cr    = round(mktcap / 1e7, 0)
            metadata_rows.append({"symbol": symbol,
                                  "company_name": info.get("longName"),
                                  "industry": info.get("sector"),
                                  "market_cap_cr": mktcap_cr})

            if mktcap_cr < MKTCAP_FLOOR:
                print("  [{:03d}] {:20s} Rs {:>10,.0f} Cr  SKIPPED".format(idx, symbol, mktcap_cr))
                time.sleep(SLEEP_SECS)
                continue

            # Empty df classified as UP_TO_DATE -> not a failure
            if failure == "UP_TO_DATE":
                print("  [{:03d}] {:20s} Rs {:>10,.0f} Cr  UP TO DATE".format(idx, symbol, mktcap_cr))
                time.sleep(SLEEP_SECS)
                continue

            # Empty df classified as NO_DATA, or DOWNLOAD_ERROR -> failure
            if failure in ("NO_DATA", "DOWNLOAD_ERROR"):
                failed_symbols[symbol] = {"failure_type": failure,
                                          "error_message": errmsg, "attempts": 1}
                print("  [{:03d}] {:20s} Rs {:>10,.0f} Cr  {} : {}".format(
                    idx, symbol, mktcap_cr, failure, errmsg))
                time.sleep(SLEEP_SECS)
                continue

            # Success
            new_price_rows.append(df)

            if idx % 50 == 0:
                print("  CHECKPOINT {} -- saving...".format(idx))
                checkpoint = pd.concat(new_price_rows, ignore_index=True)
                if not existing_prices.empty:
                    checkpoint = pd.concat([existing_prices, checkpoint], ignore_index=True)
                checkpoint["date"] = pd.to_datetime(checkpoint["date"])
                checkpoint = checkpoint.drop_duplicates(subset=["symbol", "date"], keep="last")
                checkpoint = checkpoint.sort_values(["symbol", "date"]).reset_index(drop=True)
                checkpoint.to_parquet(PRICES_PATH, index=False)
                print("  CHECKPOINT saved : {} rows".format(checkpoint.shape[0]))

            print("  [{:03d}] {:20s} Rs {:>10,.0f} Cr  {} {} rows".format(
                idx, symbol, mktcap_cr, mode, len(df)))

        except Exception as e:
            failed_symbols[symbol] = {"failure_type": "INFO_ERROR",
                                      "error_message": str(e), "attempts": 1}
            print("  [{:03d}] {:20s} ERROR : {}".format(idx, symbol, str(e)))

        time.sleep(SLEEP_SECS)

    # ── Retry loop ────────────────────────────────
    if failed_symbols:
        print("\n-- RETRY: {} failed symbols --".format(len(failed_symbols)))

        for attempt in range(1, MAX_RETRIES + 1):
            if not failed_symbols:
                break
            print("\n  Retry attempt {} of {}...".format(attempt, MAX_RETRIES))
            still_failing = {}

            for symbol, rec in failed_symbols.items():
                fetch_info        = rec["failure_type"] == "INFO_ERROR"
                fetch_start, _    = compute_fetch_start(symbol)

                info, df, failure, errmsg = fetch_symbol(symbol, fetch_start, fetch_info=fetch_info)

                if failure == "INFO_ERROR":
                    rec["attempts"] += 1
                    rec["error_message"] = errmsg
                    still_failing[symbol] = rec
                    print("  {:20s} INFO_ERROR (attempt {})".format(symbol, rec["attempts"]))
                    time.sleep(SLEEP_SECS)
                    continue

                if fetch_info:
                    mktcap    = info.get("marketCap") or info.get("nonDilutedMarketCap", 0) or 0
                    mktcap_cr = round(mktcap / 1e7, 0)
                    metadata_rows.append({"symbol": symbol,
                                          "company_name": info.get("longName"),
                                          "industry": info.get("sector"),
                                          "market_cap_cr": mktcap_cr})

                if failure == "UP_TO_DATE":
                    print("  {:20s} UP TO DATE on retry".format(symbol))
                    time.sleep(SLEEP_SECS)
                    continue

                if failure in ("NO_DATA", "DOWNLOAD_ERROR"):
                    rec["attempts"] += 1
                    rec["failure_type"] = failure
                    rec["error_message"] = errmsg
                    still_failing[symbol] = rec
                    print("  {:20s} {} (attempt {})".format(symbol, failure, rec["attempts"]))
                    time.sleep(SLEEP_SECS)
                    continue

                new_price_rows.append(df)
                print("  {:20s} SUCCESS on retry {}".format(symbol, attempt))
                time.sleep(SLEEP_SECS)

            failed_symbols = still_failing

    # ── Save / clear failed symbols ───────────────
    if failed_symbols:
        failed_df = pd.DataFrame([{"symbol": s, **v} for s, v in failed_symbols.items()])
        failed_df.to_csv(FAILED_PATH, index=False)
        print("\n  {} symbols still failing -- saved to {}".format(len(failed_symbols), FAILED_PATH))
    else:
        if FAILED_PATH.exists():
            FAILED_PATH.unlink()

else:
    print("\n[2/5] Price fetch skipped -- loading existing metadata...")
    if METADATA_PATH.exists():
        metadata_rows = pd.read_parquet(METADATA_PATH).to_dict("records")

# ── Step 3: Save metadata ─────────────────────────
print("\n[3/5] Saving metadata...")
metadata = pd.DataFrame(metadata_rows).drop_duplicates(subset=["symbol"], keep="last")
metadata.to_parquet(METADATA_PATH, index=False)
print("      universe_metadata.parquet : {} rows".format(len(metadata)))

# ── Step 4: Merge & save prices ───────────────────
print("\n[4/5] Saving prices...")
if new_price_rows:
    new_prices = pd.concat(new_price_rows, ignore_index=True)
    if not existing_prices.empty:
        combined = pd.concat([existing_prices, new_prices], ignore_index=True)
    else:
        combined = new_prices
    combined["date"] = pd.to_datetime(combined["date"])
    combined = combined.drop_duplicates(subset=["symbol", "date"], keep="last")
    combined = combined.sort_values(["symbol", "date"]).reset_index(drop=True)
    combined.to_parquet(PRICES_PATH, index=False)
    print("      prices.parquet : {} rows".format(combined.shape[0]))
else:
    combined = existing_prices
    print("      No new price rows to add")

# ── Step 5: Compute ADTV & build universe ─────────
print("\n[5/5] Computing ADTV and building universe snapshot...")

combined["date"]        = pd.to_datetime(combined["date"])
combined["daily_value"] = combined["close"] * combined["volume"]

adtv_rows = []
for symbol, grp in combined.groupby("symbol"):
    grp = grp.sort_values("date").copy()
    grp["adtv_63_cr"] = (
        grp["daily_value"].rolling(window=ADTV_WINDOW, min_periods=1).mean() / 1e7
    ).round(2)
    adtv_rows.append(grp[["symbol", "date", "adtv_63_cr"]])

adtv = pd.concat(adtv_rows, ignore_index=True)
adtv.to_parquet(ADTV_PATH, index=False)
print("      adtv.parquet : {} rows".format(adtv.shape[0]))

latest_adtv = (adtv.sort_values("date").groupby("symbol").last()
               .reset_index()[["symbol", "adtv_63_cr"]])

universe = metadata.merge(latest_adtv, on="symbol", how="left")
universe["passes_mktcap"] = universe["market_cap_cr"] >= MKTCAP_FLOOR
universe["passes_adtv"]   = universe["adtv_63_cr"]   >= ADTV_FLOOR
universe["in_universe"]   = universe["passes_mktcap"] & universe["passes_adtv"]

UNIVERSE_DIR.mkdir(parents=True, exist_ok=True)
universe_path = UNIVERSE_DIR / "universe_{}.parquet".format(END_DATE.strftime("%Y%m%d"))
universe.to_parquet(universe_path, index=False)
print("      {} : {} rows | {} in universe".format(
    universe_path.name, len(universe), universe["in_universe"].sum()))

# ── Update last run date ──────────────────────────
if not failed_symbols:
    LAST_RUN_PATH.write_text(END_DATE.strftime("%Y-%m-%d"))
    print("\n      last_run_date.txt updated : {}".format(END_DATE))
else:
    print("\n      last_run_date.txt NOT updated -- {} symbols still failing".format(
        len(failed_symbols)))

# ── Summary ───────────────────────────────────────
print("\n" + "=" * 60)
print("SUMMARY")
print("  Total symbols       : {}".format(len(SYMBOLS)))
print("  In universe         : {}".format(universe["in_universe"].sum()))
print("  Failed after retries: {}".format(len(failed_symbols)))
if failed_symbols:
    print("  Failed symbols      : {}".format(list(failed_symbols.keys())))
print("=" * 60)
