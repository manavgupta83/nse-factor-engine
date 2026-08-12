"""
fetch_hmm_stock_data_historical.py
===================================
Master production script for daily stock OHLCV data.

Universe
--------
Union of current members from the 4 index symbol CSVs:
  /home/ec2-user/nse-factor-engine/nifty500_symbols.csv
  /home/ec2-user/nse-factor-engine/nifty100_symbols.csv
  /home/ec2-user/nse-factor-engine/niftymidcap150_symbols.csv
  /home/ec2-user/nse-factor-engine/niftysmallcap250_symbols.csv

Behaviour
---------
  - Incremental: fetches only new dates since last saved date
  - Maintains rolling 30-month window: drops rows older than today - 30 months
  - Corporate action check: weekly drop >40% + volume spike >40%
    -> if detected: refetch full price history + shares_outstanding for that symbol
    -> if still flagged after refetch: drop permanently to corporate_action_flags.parquet
  - shares_outstanding: fetched only on first run or corporate action detection
    -> saved to shares_outstanding.parquet as static lookup
  - New symbols: fetched from WINDOW_START, shares_outstanding fetched too

Output
------
  hmm-factor-engine/data/prices_hmm_daily.parquet
    Long format: symbol | date | open | high | low | close | volume
  hmm-factor-engine/data/prices_hmm_daily_volume.parquet
    Wide format: date (index) x symbol (columns), values = daily volume
  hmm-factor-engine/data/shares_outstanding.parquet
    symbol | shares_outstanding
  hmm-factor-engine/data/corporate_action_flags.parquet
    symbol | reason

Usage
-----
  python3 hmm-factor-engine/data/fetch_hmm_stock_data_historical.py
"""

import time
from datetime import date, timedelta
from dateutil.relativedelta import relativedelta
from pathlib import Path

import pandas as pd
import yfinance as yf

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
BASE             = Path("/home/ec2-user/nse-factor-engine")
WINDOW_MONTHS    = 240
BATCH_SIZE       = 20
SLEEP_BETWEEN    = 2
RETRY_SLEEP      = 5

WEEKLY_DROP_THRESHOLD  = 0.40
VOLUME_SPIKE_THRESHOLD = 0.40

UNIVERSE_FILES = [
    BASE / "nifty500_symbols.csv",
    BASE / "nifty100_symbols.csv",
    BASE / "niftymidcap150_symbols.csv",
    BASE / "niftysmallcap250_symbols.csv",
]

OUTPUT_DIR   = Path(__file__).parent
PRICE_FILE   = OUTPUT_DIR / "prices_hmm_daily.parquet"
VOLUME_FILE  = OUTPUT_DIR / "prices_hmm_daily_volume.parquet"
SHARES_FILE  = OUTPUT_DIR / "shares_outstanding.parquet"
FLAGS_FILE   = OUTPUT_DIR / "corporate_action_flags.parquet"

TODAY            = date.today()
END_DATE         = TODAY.strftime("%Y-%m-%d")
WINDOW_START     = TODAY - relativedelta(months=WINDOW_MONTHS)
WINDOW_START_STR = WINDOW_START.strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Step 1 — Load universe
# ---------------------------------------------------------------------------
def load_universe() -> list[str]:
    all_symbols = set()
    for f in UNIVERSE_FILES:
        df = pd.read_csv(f)
        df.columns = df.columns.str.strip().str.lower()
        syms = df["symbol"].dropna().str.strip().tolist()
        all_symbols.update(syms)
        print(f"  {f.name}: {len(syms)} symbols")
    tickers = sorted(all_symbols)
    print(f"  Total unique symbols: {len(tickers)}")
    return tickers


# ---------------------------------------------------------------------------
# Step 2 — Load existing data
# ---------------------------------------------------------------------------
def load_existing_prices() -> pd.DataFrame:
    if PRICE_FILE.exists():
        df = pd.read_parquet(PRICE_FILE)
        df.columns = df.columns.str.strip().str.lower()
        df["date"] = pd.to_datetime(df["date"])
        print(f"  Existing prices: {df.shape[0]} rows, "
              f"{df['symbol'].nunique()} symbols, "
              f"{df['date'].min().date()} -> {df['date'].max().date()}")
        return df
    print("  No existing prices found — full fetch for all symbols")
    return pd.DataFrame()


def load_existing_shares() -> pd.DataFrame:
    if SHARES_FILE.exists():
        df = pd.read_parquet(SHARES_FILE)
        df.columns = df.columns.str.strip().str.lower()
        print(f"  Existing shares_outstanding: {len(df)} symbols")
        return df
    print("  No existing shares_outstanding found")
    return pd.DataFrame()


# ---------------------------------------------------------------------------
# Step 3 — Fetch OHLCV in batches, return long format
# ---------------------------------------------------------------------------
def fetch_ohlcv(tickers_ns: list[str], start: str, end: str) -> pd.DataFrame:
    raw = yf.download(
        tickers_ns,
        start=start,
        end=end,
        auto_adjust=True,
        progress=False,
        threads=True,
    )

    if raw.empty:
        return pd.DataFrame()

    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns.names = ["field", "ticker"]
        df = raw.stack(level="ticker", future_stack=True).reset_index()
        df.columns = df.columns.str.strip().str.lower()
        df = df.rename(columns={"ticker": "symbol"})
    else:
        df = raw.reset_index()
        df.columns = df.columns.str.strip().str.lower()
        df["symbol"] = tickers_ns[0].replace(".NS", "")

    df["symbol"] = df["symbol"].str.replace(".NS", "", regex=False)
    df["date"]   = pd.to_datetime(df["date"])

    cols = ["symbol", "date", "open", "high", "low", "close", "volume"]
    df   = df[[c for c in cols if c in df.columns]]
    df   = df.dropna(subset=["close"])
    df   = df[df["volume"] > 0]

    return df


# ---------------------------------------------------------------------------
# Step 4 — Corporate action detection
# ---------------------------------------------------------------------------
def detect_corporate_actions(prices: pd.DataFrame) -> list[str]:
    flagged = []
    for sym, grp in prices.groupby("symbol"):
        grp = grp.sort_values("date").set_index("date")
        if len(grp) < 10:
            continue
        weekly_ret   = grp["close"].pct_change(5)
        vol_roll     = grp["volume"].rolling(5).sum()
        vol_ratio    = (vol_roll / vol_roll.shift(5)) - 1
        flag         = (weekly_ret < -WEEKLY_DROP_THRESHOLD) & (vol_ratio > VOLUME_SPIKE_THRESHOLD)
        if flag.any():
            n_flags   = flag.sum()
            worst_ret = weekly_ret[flag].min()
            flagged.append(sym)
            print(f"  CORP ACTION FLAG: {sym} — {n_flags} week(s), "
                  f"worst weekly ret: {worst_ret:.1%}")
    return flagged


# ---------------------------------------------------------------------------
# Step 5 — Fetch shares_outstanding
# ---------------------------------------------------------------------------
def fetch_shares_outstanding(symbols: list[str]) -> pd.DataFrame:
    rows = []
    for sym in symbols:
        try:
            info   = yf.Ticker(f"{sym}.NS").info
            shares = info.get("sharesOutstanding")
            rows.append({"symbol": sym, "shares_outstanding": shares})
            status = f"{shares:,.0f}" if shares else "N/A"
            print(f"  {sym}: {status}")
        except Exception as e:
            rows.append({"symbol": sym, "shares_outstanding": None})
            print(f"  {sym}: ERROR — {e}")
        time.sleep(0.3)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Step 6 — Build and save wide volume parquet from long price dataframe
# ---------------------------------------------------------------------------
def save_volume_parquet(combined: pd.DataFrame) -> None:
    volume = (
        combined[["symbol", "date", "volume"]]
        .pivot(index="date", columns="symbol", values="volume")
    )
    volume.index.name  = None
    volume.columns.name = None
    volume.to_parquet(VOLUME_FILE)
    print(f"  Saved volume : {volume.shape} -> {VOLUME_FILE.name}")
    print(f"  Date range   : {volume.index.min().date()} -> {volume.index.max().date()}")
    print(f"  Tickers      : {volume.shape[1]}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print("HMM Stock Data Fetch — Production")
    print(f"Run date      : {END_DATE}")
    print(f"Window start  : {WINDOW_START_STR}  ({WINDOW_MONTHS} months)")
    print("=" * 60)

    # ── Load universe ─────────────────────────────────────────────
    print("\n[1/6] Loading universe...")
    tickers    = load_universe()
    tickers_ns = [f"{t}.NS" for t in tickers]
    total      = len(tickers_ns)

    # ── Load existing data ────────────────────────────────────────
    print("\n[2/6] Loading existing data...")
    existing_prices = load_existing_prices()
    existing_shares = load_existing_shares()

    # ── Determine fetch window and new symbols ────────────────────
    print("\n[3/6] Determining fetch window and new symbols...")
    if not existing_prices.empty:
        last_date     = existing_prices["date"].max().date()
        fetch_start   = (last_date + timedelta(days=1)).strftime("%Y-%m-%d")
        existing_syms = set(existing_prices["symbol"].unique())
        new_syms      = [t for t in tickers if t not in existing_syms]
        print(f"  Last date in parquet  : {last_date}")
        print(f"  Incremental fetch from: {fetch_start}")
        print(f"  New symbols           : {len(new_syms)}")
    else:
        fetch_start = WINDOW_START_STR
        new_syms    = tickers
        print(f"  Full fetch from: {fetch_start}")

    # ── Fetch prices ──────────────────────────────────────────────
    print(f"\n[4/6] Fetching prices...")
    all_parts = []
    failed    = []

    # Full history for new symbols
    if new_syms:
        print(f"  Fetching full history for {len(new_syms)} new symbols...")
        new_ns = [f"{s}.NS" for s in new_syms]
        for i in range(0, len(new_ns), BATCH_SIZE):
            batch     = new_ns[i : i + BATCH_SIZE]
            batch_num = i // BATCH_SIZE + 1
            total_b   = (len(new_ns) + BATCH_SIZE - 1) // BATCH_SIZE
            print(f"  New batch {batch_num}/{total_b}: {len(batch)} tickers")
            df = fetch_ohlcv(batch, WINDOW_START_STR, END_DATE)
            if df.empty:
                failed.extend([t.replace(".NS", "") for t in batch])
            else:
                print(f"    OK: {df['symbol'].nunique()} tickers, {len(df)} rows")
                all_parts.append(df)
            time.sleep(SLEEP_BETWEEN)

    # Incremental fetch for existing symbols
    if fetch_start < END_DATE:
        existing_ns = [f"{t}.NS" for t in tickers if t not in new_syms]
        for i in range(0, len(existing_ns), BATCH_SIZE):
            batch     = existing_ns[i : i + BATCH_SIZE]
            batch_num = i // BATCH_SIZE + 1
            total_b   = (len(existing_ns) + BATCH_SIZE - 1) // BATCH_SIZE
            print(f"  Incremental batch {batch_num}/{total_b}: {len(batch)} tickers")
            df = fetch_ohlcv(batch, fetch_start, END_DATE)
            if df.empty:
                failed.extend([t.replace(".NS", "") for t in batch])
            else:
                print(f"    OK: {df['symbol'].nunique()} tickers, {len(df)} rows")
                all_parts.append(df)
            time.sleep(SLEEP_BETWEEN)

    # Retry failed
    if failed:
        print(f"\n  Retrying {len(failed)} failed tickers solo...")
        for sym in failed:
            print(f"    {sym}...")
            time.sleep(RETRY_SLEEP)
            start = WINDOW_START_STR if sym in new_syms else fetch_start
            df = fetch_ohlcv([f"{sym}.NS"], start, END_DATE)
            if not df.empty:
                all_parts.append(df)
                print(f"    OK")
            else:
                print(f"    STILL EMPTY — skipping {sym}")

    # ── Merge ─────────────────────────────────────────────────────
    print("\n[5/6] Corporate action check + merge...")
    if all_parts:
        new_data = pd.concat(all_parts, ignore_index=True)

        if not existing_prices.empty:
            combined = pd.concat([existing_prices, new_data], ignore_index=True)
            combined = combined.drop_duplicates(subset=["symbol", "date"], keep="last")
        else:
            combined = new_data

        combined = combined.sort_values(["symbol", "date"]).reset_index(drop=True)

        # Corporate action detection
        print("  Running corporate action check...")
        flagged = detect_corporate_actions(combined)

        if flagged:
            print(f"  {len(flagged)} symbol(s) flagged — refetching full history...")
            refetch_parts = []
            for sym in flagged:
                time.sleep(RETRY_SLEEP)
                df = fetch_ohlcv([f"{sym}.NS"], WINDOW_START_STR, END_DATE)
                if not df.empty:
                    refetch_parts.append(df)
                    print(f"    {sym}: refetched {len(df)} rows")

            if refetch_parts:
                refetch_data = pd.concat(refetch_parts, ignore_index=True)
                combined     = combined[~combined["symbol"].isin(flagged)]
                combined     = pd.concat([combined, refetch_data], ignore_index=True)
                combined     = combined.sort_values(["symbol", "date"]).reset_index(drop=True)

            # Second check — if still flagged, yfinance didn't fix it
            print("  Re-checking corporate actions after refetch...")
            still_flagged = detect_corporate_actions(
                combined[combined["symbol"].isin(flagged)]
            )

            if still_flagged:
                print(f"  {len(still_flagged)} symbol(s) still flagged — dropping permanently:")
                print(f"  {still_flagged}")
                new_flags = pd.DataFrame({"symbol": still_flagged, "reason": "yfinance_unadjusted"})
                if FLAGS_FILE.exists():
                    existing_flags = pd.read_parquet(FLAGS_FILE)
                    new_flags = pd.concat([existing_flags, new_flags]).drop_duplicates(subset=["symbol"], keep="last")
                new_flags.to_parquet(FLAGS_FILE, index=False)
                print(f"  Saved -> corporate_action_flags.parquet")
                combined = combined[~combined["symbol"].isin(still_flagged)]
                flagged  = [s for s in flagged if s not in still_flagged]
            else:
                print("  All refetched symbols look clean.")

            # Refetch shares_outstanding for remaining clean flagged symbols
            if flagged:
                print(f"  Refetching shares_outstanding for {len(flagged)} flagged symbols...")
                new_shares = fetch_shares_outstanding(flagged)
                if not existing_shares.empty:
                    existing_shares = existing_shares[~existing_shares["symbol"].isin(flagged)]
                    existing_shares = pd.concat([existing_shares, new_shares], ignore_index=True)
                else:
                    existing_shares = new_shares
        else:
            print("  No corporate action artifacts detected.")

        # Drop rows outside rolling 30-month window
        cutoff = pd.Timestamp(WINDOW_START)
        before = len(combined)
        combined = combined[combined["date"] >= cutoff]
        dropped  = before - len(combined)
        if dropped > 0:
            print(f"  Dropped {dropped} rows older than {WINDOW_START_STR}")

        combined.to_parquet(PRICE_FILE, index=False)
        print(f"  Saved prices : {combined.shape} -> {PRICE_FILE.name}")
        print(f"  Date range   : {combined['date'].min().date()} -> {combined['date'].max().date()}")
        print(f"  Symbols      : {combined['symbol'].nunique()}")

        # Save wide volume parquet in sync with price parquet
        save_volume_parquet(combined)

    else:
        print("  No new price data fetched.")

    # ── Shares outstanding — fetch for missing symbols ────────────
    print("\n[6/6] Shares outstanding...")
    known_syms   = set(existing_shares["symbol"].tolist()) if not existing_shares.empty else set()
    missing_syms = [t for t in tickers if t not in known_syms]

    if missing_syms:
        print(f"  Fetching shares_outstanding for {len(missing_syms)} symbols...")
        new_shares_df = fetch_shares_outstanding(missing_syms)
        if not existing_shares.empty:
            existing_shares = pd.concat([existing_shares, new_shares_df], ignore_index=True)
        else:
            existing_shares = new_shares_df
        existing_shares = existing_shares.drop_duplicates(subset=["symbol"], keep="last")
        existing_shares.to_parquet(SHARES_FILE, index=False)
        print(f"  Saved shares_outstanding: {len(existing_shares)} symbols -> {SHARES_FILE.name}")
    else:
        print("  All symbols already have shares_outstanding. No fetch needed.")

    print("\n" + "=" * 60)
    print("Done.")
    print("=" * 60)


if __name__ == "__main__":
    main()
