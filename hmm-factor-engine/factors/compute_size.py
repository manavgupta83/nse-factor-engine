"""
compute_size.py
===============
Size Factor Construction

Signal:
    size_raw = -log(market_cap)
    market_cap = month_end_price * shares_cr * 1e7

Negative log so smaller cap = higher size score.
Z-scored cross-sectionally each month.

Output
------
hmm-factor-engine/data/factor_size.parquet
    Columns: nse_ticker, date, raw, score

Usage
-----
    python3 hmm-factor-engine/factors/compute_size.py
"""

import csv
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from universe import build_universe_lookup, get_clean_universe

warnings.filterwarnings("ignore")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR        = Path(__file__).parent.parent
DATA_DIR        = BASE_DIR / "data"
FACTORS_DIR     = Path(__file__).parent / "data"

PRICE_FILE      = DATA_DIR / "prices_hmm_daily.parquet"
VOLUME_FILE     = DATA_DIR / "prices_hmm_daily_volume.parquet"
FUND_FILE       = DATA_DIR / "fundamentals_annual.parquet"
SYMBOL_MAP_FILE = DATA_DIR / "symbol_map.csv"
CONSTITUENT_CSV = Path("/home/ec2-user/nse-factor-engine/nifty_constituent_history/"
                       "nifty500_2005-01-01_to_2026-06-30.csv")
OUTPUT_FILE     = FACTORS_DIR / "factor_size.parquet"

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
BACKTEST_START  = "2018-07"
BACKTEST_END    = "2026-06"
EXCLUDE_TICKERS = {"ABBOTINDIA", "PFIZER"}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def parse_fiscal_year_end(fy_str: str) -> pd.Timestamp:
    month_map = {
        "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4,
        "May": 5, "Jun": 6, "Jul": 7, "Aug": 8,
        "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
    }
    try:
        mon_str, yr_str = fy_str[:3], fy_str[4:]
        month = month_map[mon_str]
        year  = 2000 + int(yr_str)
        ts    = pd.Timestamp(year=year, month=month, day=1)
        return ts + pd.offsets.MonthEnd(0)
    except Exception:
        return pd.NaT


def load_symbol_map() -> dict:
    sym_map = {}
    with open(SYMBOL_MAP_FILE) as f:
        for row in csv.DictReader(f):
            sym_map[row["csv_symbol"]] = row["parquet_col"]
    return sym_map


def zscore(s: pd.Series) -> pd.Series:
    mu, sigma = s.mean(), s.std()
    if sigma == 0 or pd.isna(sigma):
        return pd.Series(np.nan, index=s.index)
    return (s - mu) / sigma


# ---------------------------------------------------------------------------
# Load shares_cr — forward filled within ticker, with 90-day lag
# ---------------------------------------------------------------------------
def load_shares(fund_df: pd.DataFrame) -> pd.DataFrame:
    """
    Returns a long-format DataFrame:
        nse_ticker | fy_end | shares_cr | available_from
    With shares_cr forward-filled within ticker.
    Only rows where shares_cr is not null after ffill.
    """
    df = fund_df.copy()
    df["shares_cr"]      = df.groupby("nse_ticker")["shares_cr"].ffill()
    df["available_from"] = df["fy_end"] + pd.Timedelta(days=90)
    df = df[df["shares_cr"].notna()][["nse_ticker","fy_end","shares_cr","available_from"]]
    return df


# ---------------------------------------------------------------------------
# Get signals at date T
# ---------------------------------------------------------------------------
def get_signals_at_date(
    date      : pd.Timestamp,
    universe  : list,
    shares_df : pd.DataFrame,
    monthly_px: pd.DataFrame,
    sym_map   : dict,
) -> pd.DataFrame:
    """
    For each ticker in universe:
    - Get latest shares_cr available as of date T (90-day lag)
    - Get month-end price
    - Compute market_cap and size_raw = -log(market_cap)
    """
    avail  = shares_df[shares_df["available_from"] <= date]
    latest = (
        avail[avail["nse_ticker"].isin(universe)]
        .sort_values("fy_end")
        .groupby("nse_ticker", as_index=False)
        .nth(-1)
    )

    if latest.empty:
        return pd.DataFrame()

    rows = []
    for _, row in latest.iterrows():
        ticker = row["nse_ticker"]
        sc     = row["shares_cr"]

        if pd.isna(sc) or sc <= 0:
            continue

        # Get month-end price
        col = sym_map.get(ticker, ticker)
        if col not in monthly_px.columns:
            continue
        if date not in monthly_px.index:
            continue
        price = monthly_px.loc[date, col]
        if pd.isna(price) or price <= 0:
            continue

        # Market cap in Rs
        market_cap = price * sc * 1e7
        if market_cap <= 0:
            continue

        size_raw = -np.log(market_cap)

        rows.append({
            "nse_ticker": ticker,
            "raw"       : size_raw,
            "market_cap": market_cap,
        })

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Backtest loop
# ---------------------------------------------------------------------------
def run_backtest(
    daily_prices: pd.DataFrame,
    daily_volume: pd.DataFrame,
    universe_df : pd.DataFrame,
    shares_df   : pd.DataFrame,
    sym_map     : dict,
) -> pd.DataFrame:

    periods     = pd.period_range(start=BACKTEST_START, end=BACKTEST_END, freq="M")
    dates       = [p.to_timestamp(how="end").normalize() for p in periods]
    monthly_px  = daily_prices.resample("ME").last()
    valid_dates = [d for d in dates if d in monthly_px.index]

    all_records = []

    for date in valid_dates:
        universe = get_clean_universe(
            date, daily_prices, daily_volume, universe_df, sym_map
        )
        if not universe:
            continue

        universe = [t for t in universe if t not in EXCLUDE_TICKERS]

        signals = get_signals_at_date(
            date, universe, shares_df, monthly_px, sym_map
        )
        if signals.empty or len(signals) < 10:
            print(f"  SKIP {date.strftime('%Y-%m')}: only {len(signals)} stocks with signal")
            continue

        # Cross-sectional z-score
        signals["score"] = zscore(signals["raw"])
        signals["date"]  = date

        all_records.append(signals[["nse_ticker","date","raw","score","market_cap"]])

    if not all_records:
        return pd.DataFrame()

    return pd.concat(all_records, ignore_index=True)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    FACTORS_DIR.mkdir(parents=True, exist_ok=True)

    print("Loading symbol map ...")
    sym_map = load_symbol_map()
    print(f"  {len(sym_map)} mappings loaded")

    print("Loading daily prices and volume ...")
    daily_prices = pd.read_parquet(PRICE_FILE)
    daily_prices.index = pd.to_datetime(daily_prices.index)
    daily_volume = pd.read_parquet(VOLUME_FILE)
    daily_volume.index = pd.to_datetime(daily_volume.index)
    print(f"  Daily prices shape: {daily_prices.shape}")

    print("Building point-in-time universe lookup ...")
    universe_df = build_universe_lookup(CONSTITUENT_CSV)
    print(f"  {len(universe_df)} rebalance snapshots loaded")

    print("Loading fundamentals for shares_cr ...")
    fund_raw = pd.read_parquet(FUND_FILE)
    fund_raw["fy_end"] = fund_raw["fiscal_year"].apply(parse_fiscal_year_end)
    fund_raw = fund_raw[fund_raw["fy_end"].notna()].sort_values(
        ["nse_ticker","fy_end"]
    ).reset_index(drop=True)
    shares_df = load_shares(fund_raw)
    print(f"  {shares_df['nse_ticker'].nunique()} tickers with shares_cr data")

    print(f"\nRunning backtest: {BACKTEST_START} to {BACKTEST_END} ...")
    results = run_backtest(
        daily_prices, daily_volume, universe_df, shares_df, sym_map
    )

    if results.empty:
        print("ERROR: no results produced")
        return

    # ---------------------------------------------------------------------------
    # Summary
    # ---------------------------------------------------------------------------
    n_months      = results["date"].nunique()
    n_tickers     = results["nse_ticker"].nunique()
    avg_per_month = results.groupby("date").size().mean()

    # Market cap stats in Cr
    results["mcap_cr"] = results["market_cap"] / 1e7

    print(f"\n{'='*60}")
    print(f"SIZE FACTOR — SUMMARY")
    print(f"{'='*60}")
    print(f"  Months covered       : {n_months}")
    print(f"  Unique tickers       : {n_tickers}")
    print(f"  Avg stocks/month     : {avg_per_month:.0f}")
    print(f"\n  score stats:")
    print(f"    mean={results['score'].mean():.4f}  "
          f"std={results['score'].std():.4f}  "
          f"min={results['score'].min():.2f}  "
          f"max={results['score'].max():.2f}")

    last_date  = results["date"].max()
    last_month = results[results["date"] == last_date].sort_values(
        "score", ascending=False
    )

    print(f"\n  Top 10 Size scores ({last_date.strftime('%Y-%m')}) — smallest caps:")
    print(last_month[["nse_ticker","mcap_cr","raw","score"]].head(10).to_string(index=False))
    print(f"\n  Bottom 10 Size scores ({last_date.strftime('%Y-%m')}) — largest caps:")
    print(last_month[["nse_ticker","mcap_cr","raw","score"]].tail(10).to_string(index=False))

    # Market cap distribution in last month
    print(f"\n  Market cap distribution in {last_date.strftime('%Y-%m')} (Cr):")
    print(last_month["mcap_cr"].describe().apply(lambda x: f"{x:,.0f}").to_string())

    print(f"\n  Stocks per month (first 5 and last 5):")
    monthly_counts = results.groupby("date").size()
    print(pd.concat([monthly_counts.head(5), monthly_counts.tail(5)]).to_string())

    # Drop market_cap helper column before saving
    results = results.drop(columns=["market_cap"])
    results.to_parquet(OUTPUT_FILE)
    print(f"\nSaved -> {OUTPUT_FILE}")
    print("Done.")


if __name__ == "__main__":
    main()
