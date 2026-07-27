"""
fetch_hmm_data.py
=================
Fetches Nifty 500 daily price data from Yahoo Finance (^CRSLDX),
computes monthly features, and saves the result as a parquet file
for HMM training and evaluation.

Output
------
hmm-factor-engine/data/nifty500_hmm_data_200504_202412_v2.parquet

Columns (HMM features)
-----------------------
  excess_return      : monthly Nifty 500 return minus monthly risk-free rate
  realised_vol       : annualised std dev of daily returns within each month
  trailing_2m_return : 2-month cumulative return (captures quick trend shifts)

Audit columns (not fed to HMM)
-------------------------------
  monthly_return     : raw monthly return
  rfr_monthly        : monthly risk-free rate used (annual rate / 12)

Windows (splitting is the training script's job)
------------------------------------------------
  Construction : 2007-03 to 2019-12  (154 months)
  Evaluation   : 2020-01 to 2024-12  (60 months)

Note on data availability
--------------------------
  ^CRSLDX daily data starts 2005-09-26 on yfinance.
  trailing_2m_return requires 2 months of history.
  First valid row is therefore 2007-03-31 (214 rows total).

Risk-Free Rate
--------------
Hardcoded from India 91-day T-bill historical ranges (RBI / FBIL).
Midpoint of each period's range, as annual rate, converted to monthly.

  2005-04 to 2011-12 : 8.0% pa  (midpoint of 7%-9% band, 2005-2011)
  2012-01 to 2019-12 : 7.0% pa  (midpoint of 6%-8% band, 2012-2019)
  2020-01 to 2021-12 : 5.0% pa  (midpoint of 4.5%-5.5% band, 2020-2021)
  2022-01 to 2024-12 : 7.0% pa  (midpoint of 6.5%-7.5% band, 2022-2024)

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
TICKER     = "^CRSLDX"
START_DATE = "2005-04-03"
END_DATE   = "2024-12-31"

OUTPUT_DIR  = Path(__file__).parent
OUTPUT_FILE = OUTPUT_DIR / "nifty500_hmm_data_200504_202412_v2.parquet"

HMM_FEATURES = ["excess_return", "realised_vol", "trailing_2m_return"]

# ---------------------------------------------------------------------------
# Risk-free rate schedule (annual %, as decimal)
# ---------------------------------------------------------------------------
RFR_SCHEDULE = [
    ("2005-04", "2011-12", 0.080),
    ("2012-01", "2019-12", 0.070),
    ("2020-01", "2021-12", 0.050),
    ("2022-01", "2024-12", 0.070),
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

    print(f"  Downloaded {len(daily)} daily rows  "
          f"({daily.index[0].date()} -> {daily.index[-1].date()})")
    return daily


def compute_monthly_features(daily: pd.DataFrame) -> pd.DataFrame:
    daily["daily_return"] = daily["close"].pct_change()

    # Monthly close and return
    monthly_close  = daily["close"].resample("ME").last()
    monthly_return = monthly_close.pct_change()
    monthly_return.name = "monthly_return"

    # Realised vol — annualised std of daily returns within each month
    realised_vol = daily["daily_return"].resample("ME").std() * np.sqrt(252)
    realised_vol.name = "realised_vol"

    # Trailing 2-month cumulative return: (1+r_t) * (1+r_{t-1}) - 1
    trailing_2m = (1 + monthly_return).rolling(2).apply(
        lambda x: x.prod(), raw=True
    ) - 1
    trailing_2m.name = "trailing_2m_return"

    df = pd.concat([monthly_return, realised_vol, trailing_2m], axis=1)
    df.index.name = "date"
    return df


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Fetch daily prices
    daily = fetch_daily_prices()

    # 2. Compute monthly features
    print("Computing monthly features ...")
    monthly = compute_monthly_features(daily)

    # 3. Build RFR series
    print("Building risk-free rate series ...")
    rfr = build_rfr_series(monthly.index)
    monthly["rfr_monthly"] = rfr

    # 4. Excess return
    monthly["excess_return"] = monthly["monthly_return"] - monthly["rfr_monthly"]

    # 5. Drop rows with any NaN in HMM features
    monthly = monthly.dropna(subset=HMM_FEATURES)

    # 6. Reorder columns — HMM features first, audit columns after
    monthly = monthly[HMM_FEATURES + ["monthly_return", "rfr_monthly"]]

    # 7. Sanity checks
    print("\n--- Sanity checks ---")
    print(f"  Rows            : {len(monthly)}")
    print(f"  Date range      : {monthly.index[0].date()} -> {monthly.index[-1].date()}")
    print(f"  Null counts     : {monthly.isna().sum().to_dict()}")
    print(f"\n  Descriptive stats (HMM features):")
    print(monthly[HMM_FEATURES].describe().round(4).to_string())

    # 8. Spot-check known periods
    print("\n--- Spot-check ---")
    periods = {
        # Crisis (expect: negative excess_ret, high vol, negative trailing_2m)
        "GFC onset (2008-10)":           "2008-10",
        "GFC trough (2009-01)":          "2009-01",
        "Euro crisis (2011-08)":         "2011-08",
        "Taper tantrum (2013-06)":       "2013-06",
        "NBFC crisis (2018-09)":         "2018-09",
        "COVID crash (2020-03)":         "2020-03",
        "COVID crash (2020-04)":         "2020-04",
        "Russia-Ukraine (2022-06)":      "2022-06",
        # Recovery
        "COVID recovery (2020-11)":      "2020-11",
        # Bull sanity checks
        "Modi election rally (2014-06)": "2014-06",
        "Bull run mid (2017-06)":        "2017-06",
        # Disputed
        "Disputed: 2007-08":             "2007-08",
    }
    print(f"\n  {'Period':<35} {'ExcessRet':>10} {'RVol':>8} {'Trail2m':>10}")
    print(f"  {'-'*35} {'-'*10} {'-'*8} {'-'*10}")
    for label, month in periods.items():
        match = monthly[monthly.index.strftime("%Y-%m") == month]
        if not match.empty:
            r = match.iloc[0]
            print(f"  {label:<35} {r['excess_return']:>+10.4f} "
                  f"{r['realised_vol']:>8.4f} "
                  f"{r['trailing_2m_return']:>+10.4f}")
        else:
            print(f"  {label:<35} not in data")

    # 9. Save
    monthly.to_parquet(OUTPUT_FILE)
    print(f"\nSaved -> {OUTPUT_FILE}")
    print("Done.")


if __name__ == "__main__":
    main()
