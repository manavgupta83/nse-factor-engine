"""
compute_quality.py
==================
Quality Factor Construction

Non-financials:
    Quality = 0.33*Z(ROE) - 0.33*Z(D/E) - 0.33*Z(EPS_growth_std)

Financials:
    Quality = 0.5*Z(ROE) - 0.5*Z(EPS_growth_std)

Where:
    ROE           = net_profit_ann / book_equity
    D/E           = total_debt / book_equity
    EPS           = net_profit_ann / shares_cr
    EPS_growth_std= std dev of annual EPS growth (min 3 years, prefer 5)

Key data rules:
    - book_equity, shares_cr, total_debt forward-filled within ticker
    - For most recent incomplete fiscal year: annualise net_profit
      using available quarters (Jun*4, Sep+Jun*2, Dec+Sep+Jun*4/3, Mar as-is)
    - EPS history uses only March rows (full year actuals) + annualised
      current year estimate
    - Z-score fins and non-fins separately, then combine
    - score_within : z-scored within group, concatenated
    - score_combined: score_within re-z-scored across all tickers

Output
------
hmm-factor-engine/data/factor_quality.parquet
    Columns: nse_ticker, date, is_financial,
             raw_roe, raw_de, raw_eps_std,
             score_within, score_combined

Usage
-----
    python3 hmm-factor-engine/factors/compute_quality.py
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
SECTOR_FILE     = DATA_DIR / "ticker_to_sector.csv"
SYMBOL_MAP_FILE = DATA_DIR / "symbol_map.csv"
CONSTITUENT_CSV = Path("/home/ec2-user/nse-factor-engine/nifty_constituent_history/"
                       "nifty500_2005-01-01_to_2026-06-30.csv")
OUTPUT_FILE      = FACTORS_DIR / "factor_quality.parquet"
OUTPUT_RET       = FACTORS_DIR / "quality_returns.parquet"

LONG_PCTILE  = 0.90
SHORT_PCTILE = 0.10
MIN_STOCKS   = 20

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
BACKTEST_START  = "2020-07"
BACKTEST_END    = "2026-06"
WINSOR_LOW      = 0.01
WINSOR_HIGH     = 0.99
MIN_EPS_YEARS   = 3    # minimum annual EPS observations for growth std

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


def load_sector_map() -> pd.DataFrame:
    df = pd.read_csv(SECTOR_FILE)
    df["is_financial"] = df["is_financial"].astype(str).str.strip().str.lower()
    df["is_financial"] = df["is_financial"].isin(["true", "1", "yes"])
    return df.set_index("nse_ticker")


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
# Annualise net_profit for most recent incomplete fiscal year
# ---------------------------------------------------------------------------
def annualise_current_year(ticker_df: pd.DataFrame) -> pd.DataFrame:
    """
    For the most recent fiscal year that does not have a March row,
    reconstruct annualised net_profit from available quarterly rows.

    Logic (standalone quarters, not cumulative):
        Mar  → full year as-is
        Dec  → (Dec + Sep + Jun) * 4/3  fallback: (Dec+Jun)*4/3 or Dec*4/3
        Sep  → (Sep + Jun) * 2          fallback: Sep*2
        Jun  → Jun * 4

    Only applied to the most recent incomplete year.
    All historical March rows are left unchanged.
    """
    df = ticker_df.copy().sort_values("fy_end").reset_index(drop=True)

    # Identify the latest fiscal year end
    latest = df.iloc[-1]

    # If latest row is March → full year filed, no reconstruction needed
    if latest["fy_month"] == 3:
        return df

    # Latest row is an interim filing — reconstruct annualised net_profit
    latest_year  = latest["fy_end"].year
    latest_month = latest["fy_month"]

    # Get all rows for the current fiscal year (same calendar year)
    current_yr_rows = df[df["fy_end"].dt.year == latest_year].copy()

    # Extract quarterly net_profit values
    def get_q(month):
        row = current_yr_rows[current_yr_rows["fy_month"] == month]
        if len(row) == 0:
            return np.nan
        return row.iloc[0]["net_profit"]

    q1 = get_q(6)   # Jun
    q2 = get_q(9)   # Sep
    q3 = get_q(12)  # Dec

    ann_np = np.nan
    method = "DROPPED"

    if latest_month == 6:
        # Only Q1 available
        if pd.notna(q1):
            ann_np = q1 * 4
            method = "Jun*4"

    elif latest_month == 9:
        # Q2 available
        if pd.notna(q2) and pd.notna(q1):
            ann_np = (q2 + q1) * 2
            method = "Sep+Jun*2"
        elif pd.notna(q2):
            ann_np = q2 * 2
            method = "Sep*2 (Jun missing)"

    elif latest_month == 12:
        # Q3 available
        if pd.notna(q3) and pd.notna(q2) and pd.notna(q1):
            ann_np = (q3 + q2 + q1) * (4/3)
            method = "Dec+Sep+Jun*4/3"
        elif pd.notna(q3) and pd.notna(q1):
            ann_np = (q3 + q1) * (4/3)
            method = "Dec+Jun*4/3 (Sep missing)"
        elif pd.notna(q3):
            ann_np = q3 * (4/3)
            method = "Dec*4/3 (Sep+Jun missing)"

    # Update net_profit_ann for current year rows
    # Only update the latest row (use as the single current-year data point)
    df.loc[df.index[-1], "net_profit_ann"] = ann_np
    df.loc[df.index[-1], "ann_method"]     = method

    return df


# ---------------------------------------------------------------------------
# Load and prepare fundamentals
# ---------------------------------------------------------------------------
def load_fundamentals() -> pd.DataFrame:
    df = pd.read_parquet(FUND_FILE)

    # Parse fiscal year end
    df["fy_end"]   = df["fiscal_year"].apply(parse_fiscal_year_end)
    df             = df[df["fy_end"].notna()].copy()
    df["fy_month"] = df["fy_end"].dt.month

    # Sort for correct ffill
    df = df.sort_values(["nse_ticker", "fy_end"]).reset_index(drop=True)

    # Forward fill B/S fields within ticker
    for col in ["book_equity", "shares_cr", "total_debt"]:
        df[col] = df.groupby("nse_ticker")[col].ffill()

    # Fill NaN total_debt with 0 (verified debt-free companies)
    df["total_debt"] = df["total_debt"].fillna(0)

    # Initialise net_profit_ann as copy of net_profit for all rows
    df["net_profit_ann"] = df["net_profit"]
    df["ann_method"]     = "Mar_actual"

    # Apply annualisation only to tickers whose latest row is not March
    results = []
    for ticker, grp in df.groupby("nse_ticker"):
        latest_month = grp.iloc[-1]["fy_month"]
        if latest_month != 3:
            grp = annualise_current_year(grp)
        results.append(grp)

    df = pd.concat(results, ignore_index=True)

    # 90-day filing lag
    df["available_from"] = df["fy_end"] + pd.Timedelta(days=90)

    return df


# ---------------------------------------------------------------------------
# Build EPS history per ticker (annual rows only + annualised current year)
# ---------------------------------------------------------------------------
def build_eps_history(fund_df: pd.DataFrame) -> dict:
    """
    Returns dict: ticker -> pd.Series of annual EPS indexed by fy_end date.
    Only uses:
      - Historical March rows (full year actuals)
      - Annualised current year row (if latest is non-March)
    """
    eps_history = {}

    for ticker, grp in fund_df.groupby("nse_ticker"):
        grp = grp.sort_values("fy_end").copy()

        # Separate March rows (full year) and latest non-March (annualised)
        march_rows  = grp[grp["fy_month"] == 3].copy()
        latest_row  = grp.iloc[[-1]]

        if latest_row.iloc[0]["fy_month"] != 3:
            # Use annualised net_profit_ann for current year
            interim = latest_row.copy()
            annual_rows = pd.concat([march_rows, interim], ignore_index=True)
        else:
            annual_rows = march_rows.copy()

        # Compute EPS from net_profit_ann / shares_cr
        annual_rows = annual_rows[
            annual_rows["net_profit_ann"].notna() &
            annual_rows["shares_cr"].notna() &
            (annual_rows["shares_cr"] > 0)
        ].copy()

        if len(annual_rows) < MIN_EPS_YEARS:
            continue

        annual_rows["eps"] = annual_rows["net_profit_ann"] / annual_rows["shares_cr"]
        eps_series = annual_rows.set_index("fy_end")["eps"]
        eps_history[ticker] = eps_series

    return eps_history


# ---------------------------------------------------------------------------
# Compute rolling EPS growth std for a ticker as of date T
# ---------------------------------------------------------------------------
def get_eps_growth_std(
    ticker     : str,
    date       : pd.Timestamp,
    eps_history: dict,
    available_from_map: dict,
) -> float:
    """
    Get EPS growth std dev using up to 5 most recent annual EPS values
    available as of date T (respecting 90-day filing lag).
    Min observations: MIN_EPS_YEARS annual EPS values.
    """
    if ticker not in eps_history:
        return np.nan

    eps = eps_history[ticker]

    # Filter to rows available at date T
    avail_dates = available_from_map.get(ticker, {})
    valid_eps   = eps[
        eps.index.map(lambda d: avail_dates.get(d, pd.NaT)) <= date
    ]

    if len(valid_eps) < MIN_EPS_YEARS:
        return np.nan

    # Use last 5 years
    valid_eps  = valid_eps.iloc[-5:]
    eps_growth = valid_eps.pct_change().dropna()

    if len(eps_growth) < 2:
        return np.nan

    return eps_growth.std()


# ---------------------------------------------------------------------------
# Get signals at date T
# ---------------------------------------------------------------------------
def get_signals_at_date(
    date              : pd.Timestamp,
    universe          : list,
    fund_df           : pd.DataFrame,
    sector_map        : pd.DataFrame,
    eps_history       : dict,
    available_from_map: dict,
) -> pd.DataFrame:
    """
    For each ticker in universe:
    - Find latest row with book_equity not null and available_from <= date
    - Compute ROE, D/E, EPS_growth_std
    """
    # Filter to rows with book_equity available at date T
    avail = fund_df[
        (fund_df["available_from"] <= date) &
        (fund_df["book_equity"].notna())
    ]

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

        # ROE — use net_profit_ann
        np_ann  = row["net_profit_ann"]
        raw_roe = (np_ann / be) if pd.notna(np_ann) else np.nan

        # D/E — non-financials only
        if not is_fin:
            td     = row["total_debt"]
            raw_de = (td / be) if pd.notna(td) else 0.0
        else:
            raw_de = np.nan

        # EPS growth std
        raw_eps_std = get_eps_growth_std(
            ticker, date, eps_history, available_from_map
        )

        # Need at least ROE and EPS std
        if pd.isna(raw_roe) and pd.isna(raw_eps_std):
            continue

        rows.append({
            "nse_ticker"  : ticker,
            "is_financial": is_fin,
            "raw_roe"     : raw_roe,
            "raw_de"      : raw_de,
            "raw_eps_std" : raw_eps_std,
        })

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Cross-sectional scoring
# ---------------------------------------------------------------------------
def compute_scores(signals: pd.DataFrame) -> pd.DataFrame:
    """
    Non-financials: Quality = 0.33*Z(ROE) - 0.33*Z(D/E) - 0.33*Z(EPS_std)
    Financials    : Quality = 0.50*Z(ROE) - 0.50*Z(EPS_std)

    score_within  : computed within fin/non-fin group, concatenated
    score_combined: score_within re-z-scored across all tickers
    """
    if signals.empty:
        return signals

    result = signals.copy().reset_index(drop=True)
    result["score_within"]   = np.nan
    result["score_combined"] = np.nan

    for is_fin in [False, True]:
        mask = result["is_financial"] == is_fin
        grp  = result[mask].copy()
        if len(grp) < 5:
            continue

        # Z-score each component within group
        if grp["raw_roe"].notna().sum() >= 5:
            z_roe = zscore(winsorise(grp["raw_roe"].dropna()))
            result.loc[grp["raw_roe"].notna() & mask, "z_roe"] = z_roe.values
        else:
            result.loc[mask, "z_roe"] = np.nan

        if grp["raw_eps_std"].notna().sum() >= 5:
            z_eps = zscore(winsorise(grp["raw_eps_std"].dropna()))
            result.loc[grp["raw_eps_std"].notna() & mask, "z_eps_std"] = z_eps.values
        else:
            result.loc[mask, "z_eps_std"] = np.nan

        if not is_fin and grp["raw_de"].notna().sum() >= 5:
            z_de = zscore(winsorise(grp["raw_de"].dropna()))
            result.loc[grp["raw_de"].notna() & mask, "z_de"] = z_de.values
        else:
            result.loc[mask, "z_de"] = np.nan

        # Composite score — proportional reweighting for missing components
        # Never substitute 0 for missing — reweight among available components
        for idx in result[mask].index:
            roe     = result.loc[idx, "z_roe"]
            de      = result.loc[idx, "z_de"]
            eps_std = result.loc[idx, "z_eps_std"]

            if not is_fin:
                have_roe     = pd.notna(roe)
                have_de      = pd.notna(de)
                have_eps_std = pd.notna(eps_std)

                if have_roe and have_de and have_eps_std:
                    s = 0.33*roe - 0.33*de - 0.33*eps_std
                elif have_roe and have_de:
                    s = 0.50*roe - 0.50*de
                elif have_roe and have_eps_std:
                    s = 0.50*roe - 0.50*eps_std
                elif have_roe:
                    s = roe
                else:
                    s = np.nan
            else:
                have_roe     = pd.notna(roe)
                have_eps_std = pd.notna(eps_std)

                if have_roe and have_eps_std:
                    s = 0.50*roe - 0.50*eps_std
                elif have_roe:
                    s = roe
                else:
                    s = np.nan

            result.loc[idx, "score_within"] = s

    # Re-z-score across all tickers
    valid = result["score_within"].dropna()
    if len(valid) >= 5:
        result["score_combined"] = zscore(result["score_within"])

    return result


# ---------------------------------------------------------------------------
# Backtest loop
# ---------------------------------------------------------------------------
def run_backtest(
    daily_prices      : pd.DataFrame,
    daily_volume      : pd.DataFrame,
    universe_df       : pd.DataFrame,
    fund_df           : pd.DataFrame,
    sector_map        : pd.DataFrame,
    eps_history       : dict,
    available_from_map: dict,
    sym_map           : dict,
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

        signals = get_signals_at_date(
            date, universe, fund_df, sector_map, eps_history, available_from_map
        )
        if signals.empty or len(signals) < 10:
            print(f"  SKIP {date.strftime('%Y-%m')}: only {len(signals)} stocks with signal")
            continue

        scored         = compute_scores(signals)
        scored["date"] = date
        all_records.append(scored[[
            "nse_ticker", "date", "is_financial",
            "raw_roe", "raw_de", "raw_eps_std",
            "score_within", "score_combined",
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

        long_ret  = np.mean(long_rets)
        short_ret = np.mean(short_rets)

        records.append({
            "date"         : date,
            "quality_return": long_ret - short_ret,
            "long_return"  : long_ret,
            "short_return" : short_ret,
            "long_count"   : len(long_rets),
            "short_count"  : len(short_rets),
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
    ret_col = [c for c in df.columns if c.endswith("_return") and "long" not in c and "short" not in c][0]
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

    print("Loading daily prices and volume ...")
    daily_prices = pd.read_parquet(PRICE_FILE)
    daily_prices.index = pd.to_datetime(daily_prices.index)
    daily_volume = pd.read_parquet(VOLUME_FILE)
    daily_volume.index = pd.to_datetime(daily_volume.index)
    print(f"  Daily prices shape: {daily_prices.shape}")

    print("Building point-in-time universe lookup ...")
    universe_df = build_universe_lookup(CONSTITUENT_CSV)
    print(f"  {len(universe_df)} rebalance snapshots loaded")

    print("Loading and preparing fundamentals ...")
    fund_df = load_fundamentals()
    print(f"  {len(fund_df)} rows after preparation")
    print(f"  Annualised rows: {(fund_df['ann_method'] != 'Mar_actual').sum()}")
    dropped = fund_df[fund_df['ann_method'] == 'DROPPED']
    print(f"  Dropped (no quarterly data): {len(dropped)} rows")
    if len(dropped) > 0:
        print(f"  Dropped tickers: {dropped['nse_ticker'].unique().tolist()}")

    print("Loading sector map ...")
    sector_map = load_sector_map()
    n_fin    = sector_map["is_financial"].sum()
    n_nonfin = (~sector_map["is_financial"]).sum()
    print(f"  {n_fin} financials, {n_nonfin} non-financials")

    print("Building EPS history ...")
    eps_history = build_eps_history(fund_df)
    print(f"  {len(eps_history)} tickers with valid EPS history")

    # Build available_from lookup per ticker per fy_end
    print("Building available_from lookup ...")
    available_from_map = {}
    for ticker, grp in fund_df.groupby("nse_ticker"):
        available_from_map[ticker] = dict(
            zip(grp["fy_end"], grp["available_from"])
        )

    print(f"\nRunning backtest: {BACKTEST_START} to {BACKTEST_END} ...")
    results = run_backtest(
        daily_prices, daily_volume, universe_df, fund_df,
        sector_map, eps_history, available_from_map, sym_map
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
    print(f"QUALITY FACTOR — SUMMARY")
    print(f"{'='*60}")
    print(f"  Months covered       : {n_months}")
    print(f"  Unique tickers       : {n_tickers}")
    print(f"  Avg stocks/month     : {avg_per_month:.0f}")
    print(f"  Financial rows       : {fin_pct:.1f}%")
    print(f"\n  Raw signal null rates:")
    print(f"    raw_roe     : {results['raw_roe'].isna().mean()*100:.1f}%")
    print(f"    raw_de      : {results['raw_de'].isna().mean()*100:.1f}%")
    print(f"    raw_eps_std : {results['raw_eps_std'].isna().mean()*100:.1f}%")
    print(f"\n  score_within  : mean={results['score_within'].mean():.4f}  "
          f"std={results['score_within'].std():.4f}  "
          f"min={results['score_within'].min():.2f}  "
          f"max={results['score_within'].max():.2f}")
    print(f"  score_combined: mean={results['score_combined'].mean():.4f}  "
          f"std={results['score_combined'].std():.4f}  "
          f"min={results['score_combined'].min():.2f}  "
          f"max={results['score_combined'].max():.2f}")

    last_date  = results["date"].max()
    last_month = results[results["date"] == last_date].copy()

    ranked = last_month.sort_values("score_combined", ascending=False)
    print(f"\n  Top 10 Quality ({last_date.strftime('%Y-%m')}):")
    print(ranked[["nse_ticker","is_financial","raw_roe","raw_de",
                  "raw_eps_std","score_combined"]].head(10).to_string(index=False))
    print(f"\n  Bottom 10 Quality ({last_date.strftime('%Y-%m')}):")
    print(ranked[["nse_ticker","is_financial","raw_roe","raw_de",
                  "raw_eps_std","score_combined"]].tail(10).to_string(index=False))

    print(f"\n  Stocks per month (first 5 and last 5):")
    monthly_counts = results.groupby("date").size()
    print(pd.concat([monthly_counts.head(5), monthly_counts.tail(5)]).to_string())

    results.to_parquet(OUTPUT_FILE)
    print(f"\nSaved -> {OUTPUT_FILE}")

    # ---------------------------------------------------------------------------
    # Compute and save long-short return series
    # ---------------------------------------------------------------------------
    print("\nComputing long-short return series ...")
    monthly_px = daily_prices.resample("ME").last()

    quality_returns = compute_long_short_returns(
        results, "score_combined", monthly_px, sym_map
    )
    print_return_stats(quality_returns, "QUALITY")
    quality_returns.to_parquet(OUTPUT_RET)
    print(f"  Saved -> {OUTPUT_RET}")

    print("Done.")


if __name__ == "__main__":
    main()
