"""
compute_rmw.py
==============
RMW — Robust Minus Weak (Profitability) Factor

Three signals computed in parallel:
  roe    : net_profit / book_equity          (universal — all tickers)
  op_roe : operating_profit / book_equity    (non-fins)
           ppop / book_equity                (financials)
  roce   : operating_profit / (book_equity + total_debt)  (non-fins only)
           ROE used for financials (ROCE not meaningful for banks/NBFCs)

Z-scoring (all signals):
  score_within   : z-scored within fin/non-fin group separately, then concatenated
  score_combined : score_within re-z-scored across all tickers

Output
------
hmm-factor-engine/data/factor_rmw.parquet
  Columns: nse_ticker, date, is_financial,
           raw_roe, raw_op_roe, raw_roce,
           score_within_roe, score_within_op_roe, score_within_roce,
           score_combined_roe, score_combined_op_roe, score_combined_roce

Usage
-----
  python3 hmm-factor-engine/factors/compute_rmw.py
"""

import csv
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from universe import build_universe_lookup, get_clean_universe, load_prices_long, build_monthly_close

warnings.filterwarnings("ignore")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR        = Path(__file__).parent.parent
DATA_DIR        = BASE_DIR / "data"
FACTORS_DIR     = Path(__file__).parent / "data"

PRICE_FILE      = DATA_DIR / "prices_hmm_daily.parquet"
FUND_FILE       = DATA_DIR / "fundamentals_annual.parquet"
SECTOR_FILE     = DATA_DIR / "ticker_to_sector.csv"
SYMBOL_MAP_FILE = DATA_DIR / "symbol_map.csv"
CONSTITUENT_CSV = Path("/home/ec2-user/nse-factor-engine/nifty_constituent_history/"
                       "nifty500_2005-01-01_to_2026-06-30.csv")
OUTPUT_FILE         = FACTORS_DIR / "factor_rmw.parquet"
OUTPUT_ROE_RET      = FACTORS_DIR / "rmw_roe_returns.parquet"
OUTPUT_OP_ROE_RET   = FACTORS_DIR / "rmw_op_roe_returns.parquet"
OUTPUT_ROCE_RET     = FACTORS_DIR / "rmw_roce_returns.parquet"

LONG_PCTILE  = 0.90
SHORT_PCTILE = 0.10
MIN_STOCKS   = 20

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
BACKTEST_START = "2018-07"
BACKTEST_END   = "2026-06"
WINSOR_LOW     = 0.01
WINSOR_HIGH    = 0.99

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


def load_fundamentals() -> pd.DataFrame:
    df = pd.read_parquet(FUND_FILE)
    df["fy_end"]         = df["fiscal_year"].apply(parse_fiscal_year_end)
    df                   = df[df["fy_end"].notna()].copy()
    df["available_from"] = df["fy_end"] + pd.Timedelta(days=90)
    df                   = df.sort_values(["nse_ticker", "fy_end"]).reset_index(drop=True)
    return df


def load_sector_map() -> pd.DataFrame:
    df = pd.read_csv(SECTOR_FILE)
    df["is_financial"] = df["is_financial"].astype(str).str.strip().str.lower()
    df["is_financial"] = df["is_financial"].isin(["true", "1", "yes"])
    return df.set_index("nse_ticker")


def load_symbol_map() -> dict:
    sym_map = {}
    with open(SYMBOL_MAP_FILE) as f:
        for row in csv.DictReader(f):
            sym_map[row["csv_symbol"]] = row["parquet_col"]
    return sym_map


def winsorise(s: pd.Series) -> pd.Series:
    lo = s.quantile(WINSOR_LOW)
    hi = s.quantile(WINSOR_HIGH)
    return s.clip(lo, hi)


def zscore(s: pd.Series) -> pd.Series:
    mu, sigma = s.mean(), s.std()
    if sigma == 0 or pd.isna(sigma):
        return pd.Series(np.nan, index=s.index)
    return (s - mu) / sigma


# ---------------------------------------------------------------------------
# Core: get signals at date T
# ---------------------------------------------------------------------------
def get_signals_at_date(
    date       : pd.Timestamp,
    universe   : list,
    fund_df    : pd.DataFrame,
    sector_map : pd.DataFrame,
) -> pd.DataFrame:
    """
    For each ticker in universe, find the most recent fundamental row
    where available_from <= date. Return raw_roe, raw_op_roe, and raw_roce.
    raw_roce is computed for non-financials only (operating_profit / (book_equity + total_debt)).
    Financials get raw_roce = NaN (ROE is the appropriate signal for them).
    """
    avail  = fund_df[fund_df["available_from"] <= date]
    latest = (
        avail[avail["nse_ticker"].isin(universe)]
        .sort_values("fy_end")
        .groupby("nse_ticker", as_index=False)
        .nth(-1)
    )

    if latest.empty:
        return pd.DataFrame()

    latest = latest.join(sector_map[["is_financial"]], on="nse_ticker", how="left")
    latest["is_financial"] = latest["is_financial"].fillna(False)

    rows = []
    for _, row in latest.iterrows():
        ticker = row["nse_ticker"]
        is_fin = row["is_financial"]
        be     = row["book_equity"]

        if pd.isna(be) or be == 0:
            continue

        # ROE — universal
        np_ = row["net_profit"]
        raw_roe = (np_ / be) if pd.notna(np_) else np.nan

        # op_roe — operational efficiency, fin/non-fin split
        if is_fin:
            op = row.get("ppop", np.nan)
            if pd.isna(op):
                op = row.get("operating_profit", np.nan)
        else:
            op = row.get("operating_profit", np.nan)
        raw_op_roe = (op / be) if pd.notna(op) else np.nan

        # ROCE — non-financials only
        if is_fin:
            raw_roce = np.nan
        else:
            td = row.get("total_debt", np.nan)
            td = 0.0 if pd.isna(td) else td
            ce = be + td
            if ce == 0:
                raw_roce = np.nan
            else:
                raw_roce = (op / ce) if pd.notna(op) else np.nan

        # Skip if all three signals are null
        if pd.isna(raw_roe) and pd.isna(raw_op_roe) and pd.isna(raw_roce):
            continue

        rows.append({
            "nse_ticker"  : ticker,
            "is_financial": is_fin,
            "raw_roe"     : raw_roe,
            "raw_op_roe"  : raw_op_roe,
            "raw_roce"    : raw_roce,
        })

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Cross-sectional scoring — applied to one signal at a time
# ---------------------------------------------------------------------------
def compute_scores_for_signal(
    df          : pd.DataFrame,
    raw_col     : str,
    within_col  : str,
    combined_col: str,
) -> pd.DataFrame:
    df[within_col]   = np.nan
    df[combined_col] = np.nan

    for is_fin in [False, True]:
        mask = df["is_financial"] == is_fin
        grp  = df.loc[mask, raw_col].dropna()
        if len(grp) < 5:
            continue
        w = winsorise(grp)
        z = zscore(w)
        df.loc[grp.index, within_col] = z.values

    valid = df[within_col].dropna()
    if len(valid) >= 5:
        df[combined_col] = zscore(df[within_col])

    return df


def compute_scores(signals: pd.DataFrame) -> pd.DataFrame:
    if signals.empty:
        return signals

    result = signals.copy().reset_index(drop=True)

    result = compute_scores_for_signal(
        result, "raw_roe", "score_within_roe", "score_combined_roe"
    )
    result = compute_scores_for_signal(
        result, "raw_op_roe", "score_within_op_roe", "score_combined_op_roe"
    )
    result = compute_scores_for_signal(
        result, "raw_roce", "score_within_roce", "score_combined_roce"
    )

    return result


# ---------------------------------------------------------------------------
# Backtest loop
# ---------------------------------------------------------------------------
def run_backtest(
    prices_long : pd.DataFrame,
    monthly_px  : pd.DataFrame,
    universe_df : pd.DataFrame,
    fund_df     : pd.DataFrame,
    sector_map  : pd.DataFrame,
    sym_map     : dict,
) -> pd.DataFrame:

    periods     = pd.period_range(start=BACKTEST_START, end=BACKTEST_END, freq="M")
    dates       = [p.to_timestamp(how="end").normalize() for p in periods]
    valid_dates = [d for d in dates if d in monthly_px.index]

    all_records = []

    for date in valid_dates:
        universe = get_clean_universe(
            date, prices_long, universe_df, sym_map
        )
        if not universe:
            continue

        signals = get_signals_at_date(date, universe, fund_df, sector_map)
        if signals.empty or len(signals) < 10:
            print(f"  SKIP {date.strftime('%Y-%m')}: only {len(signals)} stocks with signal")
            continue

        scored         = compute_scores(signals)
        scored["date"] = date
        all_records.append(scored[[
            "nse_ticker", "date", "is_financial",
            "raw_roe", "raw_op_roe", "raw_roce",
            "score_within_roe", "score_within_op_roe", "score_within_roce",
            "score_combined_roe", "score_combined_op_roe", "score_combined_roce",
        ]])

    if not all_records:
        return pd.DataFrame()

    return pd.concat(all_records, ignore_index=True)


# ---------------------------------------------------------------------------
# Long-short return computation
# ---------------------------------------------------------------------------
def compute_long_short_returns(
    scores_df  : pd.DataFrame,
    score_col  : str,
    monthly_px : pd.DataFrame,
    sym_map    : dict,
    return_col : str = "factor_return",
) -> pd.DataFrame:
    records = []
    dates   = sorted(scores_df["date"].unique())

    for date in dates:
        month_df = scores_df[
            scores_df["date"] == date
        ][["nse_ticker", score_col]].dropna(subset=[score_col])

        if len(month_df) < MIN_STOCKS:
            continue

        long_thresh   = month_df[score_col].quantile(LONG_PCTILE)
        short_thresh  = month_df[score_col].quantile(SHORT_PCTILE)
        long_tickers  = month_df[month_df[score_col] >= long_thresh]["nse_ticker"].tolist()
        short_tickers = month_df[month_df[score_col] <= short_thresh]["nse_ticker"].tolist()

        if not long_tickers or not short_tickers:
            continue

        idx = monthly_px.index
        if date not in idx:
            continue
        pos = idx.get_loc(date)
        if pos >= len(idx) - 1:
            continue

        t_end  = idx[pos]
        t1_end = idx[pos + 1]

        def get_ret(ticker):
            col = sym_map.get(ticker, ticker)
            if col not in monthly_px.columns:
                return np.nan
            p0 = monthly_px.loc[t_end,  col]
            p1 = monthly_px.loc[t1_end, col]
            if pd.notna(p0) and pd.notna(p1) and p0 > 0:
                return (p1 / p0) - 1
            return np.nan

        long_rets  = [r for t in long_tickers  if not np.isnan(r := get_ret(t))]
        short_rets = [r for t in short_tickers if not np.isnan(r := get_ret(t))]

        if not long_rets or not short_rets:
            continue

        records.append({
            "date"        : date,
            return_col    : np.mean(long_rets) - np.mean(short_rets),
            "long_return" : np.mean(long_rets),
            "short_return": np.mean(short_rets),
            "long_count"  : len(long_rets),
            "short_count" : len(short_rets),
            "universe_size": len(month_df),
        })

    df = pd.DataFrame(records)
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date")
    return df


def print_return_stats(df: pd.DataFrame, name: str):
    if df.empty:
        print(f"  {name}: NO RESULTS")
        return
    ret_col  = [c for c in df.columns if c.endswith("_return") and "long" not in c and "short" not in c][0]
    r        = df[ret_col]
    ann_ret  = r.mean() * 12
    ann_vol  = r.std() * np.sqrt(12)
    sharpe   = ann_ret / ann_vol if ann_vol > 0 else 0
    cum      = (1 + r).cumprod()
    drawdown = (cum / cum.cummax() - 1).min()
    hit_rate = (r > 0).mean()
    print(f"  {name}: months={len(df)}  ann_ret={ann_ret*100:.2f}%  "
          f"vol={ann_vol*100:.2f}%  sharpe={sharpe:.3f}  "
          f"maxDD={drawdown*100:.2f}%  hit={hit_rate*100:.1f}%")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    FACTORS_DIR.mkdir(parents=True, exist_ok=True)

    print("Loading symbol map ...")
    sym_map = load_symbol_map()
    print(f"  {len(sym_map)} mappings loaded")

    print("Loading daily prices (long format) ...")
    prices_long = load_prices_long(PRICE_FILE)
    print(f"  Prices shape: {prices_long.shape}")
    print(f"  Symbols     : {prices_long['symbol'].nunique()}")
    print(f"  Date range  : {prices_long['date'].min().date()} -> {prices_long['date'].max().date()}")

    print("Building monthly close prices ...")
    monthly_px = build_monthly_close(prices_long)
    print(f"  Monthly shape: {monthly_px.shape}")

    print("Building point-in-time universe lookup ...")
    universe_df = build_universe_lookup(CONSTITUENT_CSV)
    print(f"  {len(universe_df)} rebalance snapshots loaded")

    print("Loading fundamentals ...")
    fund_df = load_fundamentals()
    print(f"  {len(fund_df)} rows, {fund_df['nse_ticker'].nunique()} tickers")
    print(f"  Fiscal year range   : {fund_df['fy_end'].min().date()} "
          f"to {fund_df['fy_end'].max().date()}")
    print(f"  Available from range: {fund_df['available_from'].min().date()} "
          f"to {fund_df['available_from'].max().date()}")

    print("Loading sector map ...")
    sector_map = load_sector_map()
    n_fin    = sector_map["is_financial"].sum()
    n_nonfin = (~sector_map["is_financial"]).sum()
    print(f"  {n_fin} financials, {n_nonfin} non-financials")

    print(f"\nRunning backtest: {BACKTEST_START} to {BACKTEST_END} ...")
    results = run_backtest(
        prices_long, monthly_px, universe_df, fund_df, sector_map, sym_map
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
    fin_pct       = results["is_financial"].mean() * 100

    print(f"\n{'='*60}")
    print(f"RMW FACTOR — SUMMARY")
    print(f"{'='*60}")
    print(f"  Months covered       : {n_months}")
    print(f"  Unique tickers       : {n_tickers}")
    print(f"  Avg stocks/month     : {avg_per_month:.0f}")
    print(f"  Financial rows       : {fin_pct:.1f}%")

    for sig, raw_col, within_col, combined_col in [
        ("ROE    (net_profit/BE)          ", "raw_roe",    "score_within_roe",    "score_combined_roe"),
        ("op_ROE (op_profit/BE)           ", "raw_op_roe", "score_within_op_roe", "score_combined_op_roe"),
        ("ROCE   (op_profit/BE+TD, nonfin)", "raw_roce",   "score_within_roce",   "score_combined_roce"),
    ]:
        print(f"\n  --- {sig} ---")
        print(f"  raw nulls      : {results[raw_col].isna().sum()} "
              f"({results[raw_col].isna().mean()*100:.1f}%)")
        print(f"  score_within   : mean={results[within_col].mean():.4f}  "
              f"std={results[within_col].std():.4f}  "
              f"min={results[within_col].min():.2f}  "
              f"max={results[within_col].max():.2f}")
        print(f"  score_combined : mean={results[combined_col].mean():.4f}  "
              f"std={results[combined_col].std():.4f}  "
              f"min={results[combined_col].min():.2f}  "
              f"max={results[combined_col].max():.2f}")

    last_date  = results["date"].max()
    last_month = results[results["date"] == last_date].copy()

    for sig_label, combined_col in [
        ("ROE",    "score_combined_roe"),
        ("op_ROE", "score_combined_op_roe"),
        ("ROCE",   "score_combined_roce"),
    ]:
        ranked = last_month.sort_values(combined_col, ascending=False)
        print(f"\n  Top 10 by {sig_label} ({last_date.strftime('%Y-%m')}):")
        print(ranked[["nse_ticker", "is_financial", "raw_roe", "raw_op_roe", "raw_roce",
                       combined_col]].head(10).to_string(index=False))
        print(f"\n  Bottom 10 by {sig_label} ({last_date.strftime('%Y-%m')}):")
        print(ranked[["nse_ticker", "is_financial", "raw_roe", "raw_op_roe", "raw_roce",
                       combined_col]].tail(10).to_string(index=False))

    print(f"\n  Stocks per month (first 5 and last 5):")
    monthly_counts = results.groupby("date").size()
    print(pd.concat([monthly_counts.head(5), monthly_counts.tail(5)]).to_string())

    both_valid = results[["raw_roe", "raw_op_roe", "raw_roce"]].dropna()
    print(f"\n  Signal correlations (rows with all 3 non-null: {len(both_valid)}):")
    print(both_valid.corr().round(4).to_string())

    results.to_parquet(OUTPUT_FILE)
    print(f"\nSaved -> {OUTPUT_FILE}")

    print("\nComputing long-short return series ...")

    print("  ROE signal ...")
    roe_returns = compute_long_short_returns(
        results, "score_combined_roe", monthly_px, sym_map, return_col="rmw_roe_return"
    )
    print_return_stats(roe_returns, "RMW_ROE")
    roe_returns.to_parquet(OUTPUT_ROE_RET)
    print(f"  Saved -> {OUTPUT_ROE_RET}")

    print("  op_ROE signal ...")
    op_roe_returns = compute_long_short_returns(
        results, "score_combined_op_roe", monthly_px, sym_map, return_col="rmw_op_roe_return"
    )
    print_return_stats(op_roe_returns, "RMW_OP_ROE")
    op_roe_returns.to_parquet(OUTPUT_OP_ROE_RET)
    print(f"  Saved -> {OUTPUT_OP_ROE_RET}")

    print("  ROCE signal ...")
    roce_returns = compute_long_short_returns(
        results, "score_combined_roce", monthly_px, sym_map, return_col="rmw_roce_return"
    )
    print_return_stats(roce_returns, "RMW_ROCE")
    roce_returns.to_parquet(OUTPUT_ROCE_RET)
    print(f"  Saved -> {OUTPUT_ROCE_RET}")

    print("Done.")


if __name__ == "__main__":
    main()
