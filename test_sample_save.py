import time
import yfinance as yf
import pandas as pd
from datetime import date
from dateutil.relativedelta import relativedelta

SYMBOLS    = ["RELIANCE", "INFY", "HDFCBANK", "TCS", "WIPRO"]
SUFFIX     = ".NS"
END_DATE   = date.today()
START_DATE = END_DATE - relativedelta(months=15)
SLEEP_SECS = 2

print("=" * 60)
print("NSE Factor Engine - Sample Save Test")
print(f"Period  : {START_DATE} to {END_DATE}")
print(f"Symbols : {SYMBOLS}")
print("=" * 60)

prices_rows   = []
metadata_rows = []

for symbol in SYMBOLS:
    ticker_str = f"{symbol}{SUFFIX}"
    print(f"\nFetching : {ticker_str}")

    try:
        # OHLCV
        df = yf.download(
            tickers     = ticker_str,
            start       = START_DATE.strftime("%Y-%m-%d"),
            end         = END_DATE.strftime("%Y-%m-%d"),
            interval    = "1d",
            auto_adjust = True,
            progress    = False,
        )

        if df.empty:
            print(f"  NO DATA — skipping")
            time.sleep(SLEEP_SECS)
            continue

        # Flatten MultiIndex columns
        df.columns    = [field for field, ticker in df.columns]
        df.index.name = "date"
        df            = df.reset_index()
        df.columns    = [c.lower() for c in df.columns]
        df["symbol"]  = symbol
        df            = df[["symbol", "date", "open", "high", "low", "close", "volume"]]
        prices_rows.append(df)

        # Metadata from ticker.info
        info = yf.Ticker(ticker_str).info
        metadata_rows.append({
            "symbol"       : symbol,
            "company_name" : info.get("longName", None),
            "industry"     : info.get("sector",   None),
            "market_cap_cr": round(info.get("marketCap", 0) / 1e7, 0),
        })

        print(f"  Rows    : {len(df)} | {df.date.min().date()} to {df.date.max().date()}")
        print(f"  Mkt Cap : Rs {metadata_rows[-1]['market_cap_cr']:,.0f} Cr")

    except Exception as e:
        print(f"  ERROR   : {e}")

    time.sleep(SLEEP_SECS)

# Build DataFrames
prices   = pd.concat(prices_rows, ignore_index=True)
metadata = pd.DataFrame(metadata_rows)

# Save to parquet
prices.to_parquet("data/prices.parquet",             index=False)
metadata.to_parquet("data/universe_metadata.parquet", index=False)

print("\n" + "=" * 60)
print("SAVED")
print(f"  prices.parquet   : {prices.shape[0]} rows x {prices.shape[1]} cols")
print(f"  metadata.parquet : {metadata.shape[0]} rows x {metadata.shape[1]} cols")
print("\nPrices - last 3 rows:")
print(prices.tail(3).to_string(index=False))
print("\nMetadata:")
print(metadata.to_string(index=False))
print("=" * 60)
