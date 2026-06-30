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
print("NSE Factor Engine - yfinance Sample Test")
print(f"Period  : {START_DATE} to {END_DATE}")
print(f"Symbols : {SYMBOLS}")
print("=" * 60)

all_rows = []

for symbol in SYMBOLS:
    ticker_str = f"{symbol}{SUFFIX}"
    print(f"\n{'-' * 60}")
    print(f"Fetching : {ticker_str}")

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
            print("  OHLCV  : NO DATA RETURNED")
            time.sleep(SLEEP_SECS)
            continue

        # Flatten MultiIndex columns
        df.columns = [field for field, ticker in df.columns]
        df.index.name = "date"
        df = df.reset_index()
        df.columns = [c.lower() for c in df.columns]

        # Add symbol column
        df["symbol"] = symbol

        # Metadata from ticker.info
        info               = yf.Ticker(ticker_str).info
        df["company_name"] = info.get("longName", None)
        df["industry"]     = info.get("sector", None)
        df["market_cap_cr"]= round(info.get("marketCap", 0) / 1e7, 0)

        # Reorder columns
        df = df[["symbol", "date", "open", "high", "low", "close", "volume",
                 "company_name", "industry", "market_cap_cr"]]

        all_rows.append(df)

        print(f"  Rows    : {len(df)}")
        print(f"  Range   : {df.date.min()} to {df.date.max()}")
        print(f"  Mkt Cap : Rs {df.market_cap_cr.iloc[0]:,.0f} Cr")
        print(f"  Sample (last 3 rows):")
        print(df.tail(3).to_string(index=False))

    except Exception as e:
        print(f"  ERROR   : {e}")

    time.sleep(SLEEP_SECS)

final = pd.concat(all_rows, ignore_index=True)
print("\n" + "=" * 60)
print(f"FINAL DATAFRAME")
print(f"Shape   : {final.shape}")
print(f"Columns : {final.columns.tolist()}")
print(f"Symbols : {final.symbol.unique().tolist()}")
print("=" * 60)
