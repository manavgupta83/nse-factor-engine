"""
fetch_hmm_nifty500_data.py
==========================
Fetches Nifty 500 daily price data from Yahoo Finance (^CRSLDX),
computes monthly features, and saves the result as a parquet file
for HMM training and evaluation.

Output
------
hmm-factor-engine/data/nifty500_hmm_data.parquet  <- single canonical file

Columns (HMM features)
-----------------------
  excess_return      : monthly Nifty 500 return minus monthly risk-free rate
  realised_vol       : annualised std dev of daily returns within each month
  trailing_2m_return : 2-month cumulative return

Audit columns
-------------
  monthly_return     : raw monthly return
  rfr_monthly        : monthly risk-free rate used (annual rate / 12)

Risk-Free Rate
--------------
  2005-04 to 2011-12 : 8.0% pa
  2012-01 to 2019-12 : 7.0% pa
  2020-01 to 2021-12 : 5.0% pa
  2022-01 onwards    : 7.0% pa

Usage
-----
  python3 hmm-factor-engine/data/fetch_hmm_nifty500_data.py
"""

import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
TICKER      = "^CRSLDX"
START_DATE  = "2005-04-03"
END_DATE    = date.today().strftime("%Y-%m-%d")   # always fetch up to today

OUTPUT_DIR  = Path(__file__).parent
OUTPUT_FILE = OUTPUT_DIR / "nifty500_hmm_data.parquet"   # single canonical file

HMM_FEATURES = ["excess_return", "realised_vol", "trailing_2m_return"]

# ---------------------------------------------------------------------------
# Risk-free rate schedule (annual %, as decimal)
# Extend last entry's end date far into future — covers live runs
# ---------------------------------------------------------------------------
RFR_SCHEDULE = [
    ("2005-04", "2011-12", 0.080),
    ("2012-01", "2019-12", 0.070),
    ("2020-01", "2021-12", 0.050),
    ("2022-01", "2099-12", 0.070),   # open-ended — update rate here if RBI changes
]


def build_rfr_series(index: pd.DatetimeIndex) -> pd.Series:
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
    print(f"Fetching {TICKER} daily data {START_DATE} -> {END_DATE} ...")
    raw = yf.download(
        TICKER,
        start=START_DATE,
        end=END_DATE,
        auto_adjust=True,
        progress=False,
    )

    if raw.empty:
        print(f"ERROR: yfinance returned no data for {TICKER}.")
        sys.exit(1)

    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(0)

    daily = raw[["Close"]].rename(columns={"Close": "close"})
    daily.index = pd.to_datetime(daily.index)
    daily.index.name = "date"

    print(f"  Downloaded {len(daily)} daily rows "
          f"({daily.index[0].date()} -> {daily.index[-1].date()})")
    return daily


def compute_monthly_features(daily: pd.DataFrame) -> pd.DataFrame:
    daily["daily_return"] = daily["close"].pct_change()

    monthly_close  = daily["close"].resample("ME").last()
    monthly_return = monthly_close.pct_change()
    monthly_return.name = "monthly_return"

    realised_vol = daily["daily_return"].resample("ME").std() * np.sqrt(252)
    realised_vol.name = "realised_vol"

    trailing_2m = (1 + monthly_return).rolling(2).apply(
        lambda x: x.prod(), raw=True
    ) - 1
    trailing_2m.name = "trailing_2m_return"

    df = pd.concat([monthly_return, realised_vol, trailing_2m], axis=1)
    df.index.name = "date"
    return df


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    daily   = fetch_daily_prices()

    print("Computing monthly features ...")
    monthly = compute_monthly_features(daily)

    print("Building risk-free rate series ...")
    rfr = build_rfr_series(monthly.index)
    monthly["rfr_monthly"]  = rfr
    monthly["excess_return"] = monthly["monthly_return"] - monthly["rfr_monthly"]

    monthly = monthly.dropna(subset=HMM_FEATURES)
    monthly = monthly[HMM_FEATURES + ["monthly_return", "rfr_monthly"]]

    print(f"\n--- Summary ---")
    print(f"  Rows       : {len(monthly)}")
    print(f"  Date range : {monthly.index[0].date()} -> {monthly.index[-1].date()}")
    print(f"  Null counts: {monthly.isna().sum().to_dict()}")
    print(f"\n  Last 6 rows:")
    print(monthly.tail(6).to_string())

    monthly.to_parquet(OUTPUT_FILE)
    print(f"\nSaved -> {OUTPUT_FILE}")
    print("Done.")


if __name__ == "__main__":
    main()
