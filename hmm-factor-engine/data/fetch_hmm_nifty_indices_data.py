"""
fetch_hmm_nifty_indices_data.py
================================
Fetches daily price data for Nifty 500 and 10 NSE sector indices
from Yahoo Finance, computes monthly HMM features per index, and
saves one parquet file per index.

Tickers and output files
------------------------
  ^CRSLDX      -> nifty500_hmm_data.parquet
  ^CNXAUTO     -> cnxauto_hmm_data.parquet
  ^NSEBANK     -> nsebank_hmm_data.parquet
  ^CNXIT       -> cnxit_hmm_data.parquet
  ^CNXPHARMA   -> cnxpharma_hmm_data.parquet
  ^CNXFMCG     -> cnxfmcg_hmm_data.parquet
  ^CNXMETAL    -> cnxmetal_hmm_data.parquet
  ^CNXREALTY   -> cnxrealty_hmm_data.parquet
  ^CNXMEDIA    -> cnxmedia_hmm_data.parquet
  ^CNXPSUBANK  -> cnxpsubank_hmm_data.parquet
  ^CNXENERGY   -> cnxenergy_hmm_data.parquet

Columns (HMM features) per parquet
------------------------------------
  excess_return      : monthly index return minus monthly risk-free rate
  realised_vol       : annualised std dev of daily returns within each month
  trailing_2m_return : 2-month cumulative return

Audit columns
-------------
  monthly_return : raw monthly return
  rfr_monthly    : monthly risk-free rate used (annual rate / 12)

Note on data availability
--------------------------
  ^CRSLDX starts 2005-09-26 — longest history
  Sector indices start between 2007-09 and 2011-08
  Each parquet covers the full available history for that ticker

Risk-Free Rate
--------------
  2000-01 to 2011-12 : 8.0% pa
  2012-01 to 2019-12 : 7.0% pa
  2020-01 to 2021-12 : 5.0% pa
  2022-01 onwards    : 7.0% pa  (open-ended)

Usage
-----
  python3 hmm-factor-engine/data/fetch_hmm_nifty_indices_data.py

Dependencies
------------
  pip install yfinance pandas pyarrow
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
START_DATE = "2000-01-01"   # fetch from earliest possible — yfinance will clip
END_DATE   = date.today().strftime("%Y-%m-%d")

OUTPUT_DIR  = Path(__file__).parent
HMM_FEATURES = ["excess_return", "realised_vol", "trailing_2m_return"]

INDICES = {
    "^CRSLDX":     "nifty500_hmm_data.parquet",
    "^CNXAUTO":    "cnxauto_hmm_data.parquet",
    "^NSEBANK":    "nsebank_hmm_data.parquet",
    "^CNXIT":      "cnxit_hmm_data.parquet",
    "^CNXPHARMA":  "cnxpharma_hmm_data.parquet",
    "^CNXFMCG":    "cnxfmcg_hmm_data.parquet",
    "^CNXMETAL":   "cnxmetal_hmm_data.parquet",
    "^CNXREALTY":  "cnxrealty_hmm_data.parquet",
    "^CNXMEDIA":   "cnxmedia_hmm_data.parquet",
    "^CNXPSUBANK": "cnxpsubank_hmm_data.parquet",
    "^CNXENERGY":  "cnxenergy_hmm_data.parquet",
}

# ---------------------------------------------------------------------------
# Risk-free rate schedule (annual %, as decimal)
# ---------------------------------------------------------------------------
RFR_SCHEDULE = [
    ("2000-01", "2011-12", 0.080),
    ("2012-01", "2019-12", 0.070),
    ("2020-01", "2021-12", 0.050),
    ("2022-01", "2099-12", 0.070),   # open-ended
]


def build_rfr_series(index: pd.DatetimeIndex) -> pd.Series:
    rfr = pd.Series(np.nan, index=index, name="rfr_monthly")
    for start, end, annual_rate in RFR_SCHEDULE:
        mask = (index >= pd.Period(start, "M").to_timestamp()) & \
               (index <= pd.Period(end, "M").to_timestamp(how="end"))
        rfr[mask] = annual_rate / 12
    n_missing = rfr.isna().sum()
    if n_missing > 0:
        print(f"    WARNING: {n_missing} month(s) have no RFR mapping")
    return rfr


def fetch_daily_prices(ticker: str) -> pd.DataFrame:
    raw = yf.download(
        ticker,
        start=START_DATE,
        end=END_DATE,
        auto_adjust=True,
        progress=False,
    )
    if raw.empty:
        return pd.DataFrame()

    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(0)

    daily = raw[["Close"]].rename(columns={"Close": "close"})
    daily.index = pd.to_datetime(daily.index)
    daily.index.name = "date"
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


def process_ticker(ticker: str, output_file: Path) -> bool:
    print(f"\n  Fetching {ticker} ...")
    daily = fetch_daily_prices(ticker)

    if daily.empty:
        print(f"    ERROR: no data returned — skipping")
        return False

    print(f"    Daily rows : {len(daily)}  "
          f"({daily.index[0].date()} -> {daily.index[-1].date()})")

    monthly = compute_monthly_features(daily)
    rfr     = build_rfr_series(monthly.index)
    monthly["rfr_monthly"]   = rfr
    monthly["excess_return"] = monthly["monthly_return"] - monthly["rfr_monthly"]

    monthly = monthly.dropna(subset=HMM_FEATURES)
    monthly = monthly[HMM_FEATURES + ["monthly_return", "rfr_monthly"]]

    print(f"    Monthly rows: {len(monthly)}  "
          f"({monthly.index[0].date()} -> {monthly.index[-1].date()})")
    print(f"    Null counts : {monthly.isna().sum().to_dict()}")

    monthly.to_parquet(output_file)
    print(f"    Saved -> {output_file}")
    return True


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Fetching {len(INDICES)} indices | {START_DATE} -> {END_DATE}")
    print("=" * 60)

    results = {}
    for ticker, filename in INDICES.items():
        output_file = OUTPUT_DIR / filename
        ok = process_ticker(ticker, output_file)
        results[ticker] = "OK" if ok else "FAILED"

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"  {'Ticker':<15} {'Output File':<35} {'Status'}")
    print(f"  {'-'*15} {'-'*35} {'-'*6}")
    for ticker, filename in INDICES.items():
        print(f"  {ticker:<15} {filename:<35} {results[ticker]}")
    print("\nDone.")


if __name__ == "__main__":
    main()
