"""
fetch_hmm_data.py
=================
Fetches Nifty 500 daily price data from Yahoo Finance (^CRSLDX),
computes monthly excess return and realised volatility, and saves
the result as a parquet file for HMM training and evaluation.

Output
------
hmm-factor-engine/data/nifty500_hmm_data_200901_202412.parquet

Columns
-------
  excess_return  : monthly Nifty 500 return minus monthly risk-free rate
  realised_vol   : annualised std dev of daily returns within each month
  monthly_return : raw monthly return (kept for audit / diagnostics)
  rfr_monthly    : monthly risk-free rate used (annual rate / 12)

Windows (for reference — splitting is the training script's job)
----------------------------------------------------------------
  Construction : 2009-01 to 2019-12  (132 months)
  Evaluation   : 2020-01 to 2024-12  (60 months)

Risk-Free Rate
--------------
Hardcoded from India 91-day T-bill historical ranges (RBI / FBIL).
Using the midpoint of each period's range, as an annual rate.
Converted to monthly by dividing by 12.

  2009-01 to 2011-12 : 8.0%  pa  (midpoint of 7%-9% band, 2005-2011)
  2012-01 to 2019-12 : 7.0%  pa  (midpoint of 6%-8% band, 2012-2019)
  2020-01 to 2021-12 : 5.0%  pa  (midpoint of 4.5%-5.5% band, 2020-2021)
  2022-01 to 2024-12 : 7.0%  pa  (midpoint of 6.5%-7.5% band, 2022-2024)

Usage
-----
  python3 hmm-factor-engine/data/fetch_hmm_data.py

Dependencies
------------
  pip install yfinance pandas pyarrow
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
TICKER     = "^CRSLDX"       # Yahoo Finance proxy for Nifty 500
START_DATE = "2009-01-01"
END_DATE   = "2024-12-31"

OUTPUT_DIR  = Path(__file__).parent          # hmm-factor-engine/data/
OUTPUT_FILE = OUTPUT_DIR / "nifty500_hmm_data_200901_202412.parquet"

# ---------------------------------------------------------------------------
# Risk-free rate schedule (annual %, as decimal)
# Midpoint of each RBI 91-day T-bill band from the historical table
# ---------------------------------------------------------------------------
RFR_SCHEDULE = [
    ("2009-01", "2011-12", 0.080),   # 7%-9%   midpoint 8%
    ("2012-01", "2019-12", 0.070),   # 6%-8%   midpoint 7%
    ("2020-01", "2021-12", 0.050),   # 4.5%-5.5% midpoint 5%
    ("2022-01", "2024-12", 0.070),   # 6.5%-7.5% midpoint 7%
]


def build_rfr_series(index: pd.DatetimeIndex) -> pd.Series:
    """
    Builds a monthly risk-free rate series (as monthly decimal, i.e. annual/12)
    aligned to the provided DatetimeIndex.
    """
    rfr = pd.Series(np.nan, index=index, name="rfr_monthly")

    for start, end, annual_rate in RFR_SCHEDULE:
        mask = (index >= pd.Period(start, "M").to_timestamp()) & \
               (index <= pd.Period(end, "M").to_timestamp(how="end"))
        rfr[mask] = annual_rate / 12

    n_missing = rfr.isna().sum()
    if n_missing > 0:
        print(f"  WARNING: {n_missing} month(s) have no RFR mapping — check schedule.")

    return rfr


def fetch_daily_prices() -> pd.DataFrame:
    """
    Downloads daily adjusted close prices for ^CRSLDX from yfinance.
    Returns a single-column DataFrame with column 'close'.
    """
    print(f"Fetching {TICKER} daily data {START_DATE} → {END_DATE} ...")
    raw = yf.download(
        TICKER,
        start=START_DATE,
        end=END_DATE,
        auto_adjust=True,
        progress=False,
    )

    if raw.empty:
        print(f"ERROR: yfinance returned no data for {TICKER}. Check ticker / connection.")
        sys.exit(1)

    # yfinance may return MultiIndex columns depending on version
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(0)

    close = raw[["Close"]].rename(columns={"Close": "close"})
    close.index = pd.to_datetime(close.index)
    close.index.name = "date"

    print(f"  Downloaded {len(close)} daily rows  "
          f"({close.index[0].date()} → {close.index[-1].date()})")
    return close


def compute_monthly_features(daily: pd.DataFrame) -> pd.DataFrame:
    """
    From daily close prices, computes:
      - monthly_return  : last-close to last-close pct change per month
      - realised_vol    : annualised std dev of daily returns within each month
    Returns a DataFrame indexed by month-end dates (period-end Timestamps).
    """
    daily["daily_return"] = daily["close"].pct_change()

    # Monthly return: last close of each month / last close of prior month - 1
    monthly_close  = daily["close"].resample("ME").last()
    monthly_return = monthly_close.pct_change()
    monthly_return.name = "monthly_return"

    # Realised vol: std of daily returns within each calendar month, annualised
    realised_vol = daily["daily_return"].resample("ME").std() * np.sqrt(252)
    realised_vol.name = "realised_vol"

    df = pd.concat([monthly_return, realised_vol], axis=1)
    df.index.name = "date"
    return df


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Fetch daily prices
    daily = fetch_daily_prices()

    # 2. Compute monthly return + realised vol
    print("Computing monthly features ...")
    monthly = compute_monthly_features(daily)

    # 3. Build RFR series aligned to monthly index
    print("Building risk-free rate series ...")
    rfr = build_rfr_series(monthly.index)
    monthly["rfr_monthly"] = rfr

    # 4. Compute excess return
    monthly["excess_return"] = monthly["monthly_return"] - monthly["rfr_monthly"]

    # 5. Drop first row (NaN from pct_change) and any other NaNs
    monthly = monthly.dropna(subset=["excess_return", "realised_vol"])

    # 6. Reorder columns cleanly
    monthly = monthly[["excess_return", "realised_vol", "monthly_return", "rfr_monthly"]]

    # 7. Sanity checks
    print("\n--- Sanity checks ---")
    print(f"  Rows            : {len(monthly)}")
    print(f"  Date range      : {monthly.index[0].date()} → {monthly.index[-1].date()}")
    print(f"  Expected months : ~192 (Jan 2009 – Dec 2024)")
    print(f"  Null counts     : {monthly.isna().sum().to_dict()}")
    print(f"\n  Descriptive stats:")
    print(monthly.describe().round(4).to_string())

    # 8. Spot-check known stress periods — Crisis months should have high vol + negative return
    print("\n--- Spot-check: known stress periods ---")
    stress_periods = {
        "GFC recovery (2009-03)": "2009-03",
        "Taper tantrum (2013-06)": "2013-06",
        "COVID crash (2020-03)": "2020-03",
        "COVID crash (2020-04)": "2020-04",
    }
    for label, month in stress_periods.items():
        if month in monthly.index.strftime("%Y-%m"):
            row = monthly[monthly.index.strftime("%Y-%m") == month].iloc[0]
            print(f"  {label}: "
                  f"excess_ret={row['excess_return']:+.4f}  "
                  f"realised_vol={row['realised_vol']:.4f}")
        else:
            print(f"  {label}: not in index (check date range)")

    # 9. Save
    monthly.to_parquet(OUTPUT_FILE)
    print(f"\nSaved → {OUTPUT_FILE}")
    print("Done.")


if __name__ == "__main__":
    main()
