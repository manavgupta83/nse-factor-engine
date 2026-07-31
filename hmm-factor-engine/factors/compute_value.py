"""
compute_value.py
================
Value Factor Construction

Non-financials:
    Value = Z(E/P)/3 + Z(B/P)/3 + Z(S/P)/3

Financials:
    Value = Z(E/P)/2 + Z(B/P)/2

Where (P = month-end price from prices_hmm_daily.parquet):
    market_cap = P * shares_cr * 1e7
    E/P        = net_profit_ann * 1e7 / market_cap
    B/P        = book_equity   * 1e7 / market_cap
    S/P        = sales_ann     * 1e7 / market_cap  (null for financials)

Key data rules:
    - book_equity, shares_cr, total_debt forward-filled within ticker
    - net_profit and sales annualised for current incomplete fiscal year
    - Proportional reweighting when S/P is null
    - Winsorise all ratios at 1st/99th percentile before z-scoring
    - Exclude ABBOTINDIA, PFIZER (no shares_cr)
    - Z-score cross-sectionally each month across all tickers

Output
------
hmm-factor-engine/data/factor_value.parquet
    Columns: nse_ticker, date, is_financial,
             raw_ep, raw_bp, raw_sp,
             score_within, score_combined

Usage
-----
    python3 hmm-factor-engine/factors/compute_value.py
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
OUTPUT_FILE      = FACTORS_DIR / "factor_value.parquet"
OUTPUT_RET       = FACTORS_DIR / "value_returns.parquet"

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
BACKTEST_START  = "2018-07"
BACKTEST_END    = "2026-06"
WINSOR_LOW      = 0.01
WINSOR_HIGH     = 0.99
EXCLUDE_TICKERS = {"ABBOTINDIA", "PFIZER"}
MIN_STOCKS      = 20
LONG_PCTILE     = 0.90
SHORT_PCTILE    = 0.10

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
# Annualise P&L fields for current incomplete fiscal year
# ---------------------------------------------------------------------------
def annualise_current_year(grp: pd.DataFrame) -> pd.DataFrame:
    """
    For the most recent incomplete fiscal year (non-March latest row),
    annualise net_profit and sales using available quarterly rows.

    Standalone quarterly logic:
        Jun  → Q1 * 4
        Sep  → (Sep + Jun) * 2    fallback: Sep * 2
        Dec  → (Dec+Sep+Jun)*4/3  fallback: (Dec+Jun)*4/3 or Dec*4/3
        Mar  → full year, no scaling
    """
    grp    = grp.copy().sort_values("fy_end").reset_index(drop=True)
    latest = grp.iloc[-1]

    if latest["fy_month"] == 3:
        return grp

    latest_year     = latest["fy_end"].year
    current_yr_rows = grp[grp["fy_end"].dt.year == latest_year]

    def get_q(month, field):
        row = current_yr_rows[current_yr_rows["fy_month"] == month]
        if len(row) == 0:
            return np.nan
        return row.iloc[0][field]

    for field in ["net_profit_ann", "sales_ann"]:
        src = "net_profit" if field == "net_profit_ann" else "sales"
        q1  = get_q(6,  src)
        q2  = get_q(9,  src)
        q3  = get_q(12, src)
        lm  = latest["fy_month"]

        ann = np.nan
        if lm == 6:
            if pd.notna(q1):
                ann = q1 * 4
        elif lm == 9:
            if pd.notna(q2) and pd.notna(q1):
                ann = (q2 + q1) * 2
            elif pd.notna(q2):
                ann = q2 * 2
        elif lm == 12:
            if pd.notna(q3) and pd.notna(q2) and pd.notna(q1):
                ann = (q3 + q2 + q1) * (4/3)
            elif pd.notna(q3) and pd.notna(q1):
                ann = (q3 + q1) * (4/3)
            elif pd.notna(q3):
                ann = q3 * (4/3)

        grp.loc[grp.index[-1], field] = ann

    return grp


# ---------------------------------------------------------------------------
# Load and prepare fundamentals
# ---------------------------------------------------------------------------
def load_fundamentals() -> pd.DataFrame:
    df = pd.read_parquet(FUND_FILE)

    df["fy_end"]   = df["fiscal_year"].apply(parse_fiscal_year_end)
    df             = df[df["fy_end"].notna()].copy()
    df["fy_month"] = df["fy_end"].dt.month
    df             = df.sort_values(["nse_ticker", "fy_end"]).reset_index(drop=True)

    # Forward fill B/S fields within ticker
    for col in ["book_equity", "shares_cr", "total_debt"]:
        df[col] = df.groupby("nse_ticker")[col].ffill()

    # Initialise annualised fields
    df["net_profit_ann"] = df["net_profit"]
    df["sales_ann"]      = df["sales"]

    # Apply annualisation for tickers with non-March latest row
    results = []
    for ticker, grp in df.groupby("nse_ticker"):
        if grp.iloc[-1]["fy_month"] != 3:
            grp = annualise_current_year(grp)
        results.append(grp)

    df = pd.concat(results, ignore_index=True)

    # 90-day filing lag
    df["available_from"] = df["fy_end"] + pd.Timedelta(days=90)

    return df


# ---------------------------------------------------------------------------
# Get signals at date T
# ---------------------------------------------------------------------------
def get_signals_at_date(
    date      : pd.Timestamp,
    universe  : list,
    fund_df   : pd.DataFrame,
    sector_map: pd.DataFrame,
    monthly_px: pd.DataFrame,
    sym_map   : dict,
) -> pd.DataFrame:
    """
    For each ticker in universe:
    - Get latest fundamental row with book_equity not null, available_from <= date
    - Get month-end price
    - Compute E/P, B/P, S/P
    """
    # Filter to rows with book_equity available at date T
    avail  = fund_df[
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
        sc     = row["shares_cr"]

        # Need book_equity and shares_cr for market cap
        if pd.isna(be) or pd.isna(sc) or sc <= 0:
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

        # Market cap in Cr
        market_cap = price * sc * 1e7 / 1e7  # price * shares = Rs, /1e7 = Cr
        # Simplified: market_cap_cr = price * sc * 1e7 / 1e7 = price * sc
        market_cap_cr = price * sc

        if market_cap_cr <= 0:
            continue

        # E/P — earnings yield
        np_ann = row["net_profit_ann"]
        raw_ep = (np_ann / market_cap_cr) if pd.notna(np_ann) else np.nan

        # B/P — book yield
        raw_bp = be / market_cap_cr

        # S/P — sales yield (null for financials)
        if not is_fin:
            s_ann  = row["sales_ann"]
            raw_sp = (s_ann / market_cap_cr) if pd.notna(s_ann) else np.nan
        else:
            raw_sp = np.nan

        # Need at least E/P and B/P
        if pd.isna(raw_ep) and pd.isna(raw_bp):
            continue

        rows.append({
            "nse_ticker"  : ticker,
            "is_financial": is_fin,
            "raw_ep"      : raw_ep,
            "raw_bp"      : raw_bp,
            "raw_sp"      : raw_sp,
        })

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Cross-sectional scoring
# ---------------------------------------------------------------------------
def compute_scores(signals: pd.DataFrame) -> pd.DataFrame:
    """
    Non-fins: Value = Z(E/P)/3 + Z(B/P)/3 + Z(S/P)/3
    Fins    : Value = Z(E/P)/2 + Z(B/P)/2
    Proportional reweighting when component is missing.
    score_within  : z-scored cross-sectionally across all tickers
    score_combined: score_within re-z-scored
    """
    if signals.empty:
        return signals

    result = signals.copy().reset_index(drop=True)

    # Winsorise and z-score each component cross-sectionally (all tickers)
    for col, z_col in [("raw_ep", "z_ep"), ("raw_bp", "z_bp"), ("raw_sp", "z_sp")]:
        valid = result[col].dropna()
        if len(valid) >= 5:
            w = winsorise(valid)
            z = zscore(w)
            result.loc[valid.index, z_col] = z.values
        else:
            result[z_col] = np.nan

    # Composite score with proportional reweighting
    result["score_within"] = np.nan
    for idx, row in result.iterrows():
        is_fin    = row["is_financial"]
        z_ep      = row.get("z_ep", np.nan)
        z_bp      = row.get("z_bp", np.nan)
        z_sp      = row.get("z_sp", np.nan)
        have_ep   = pd.notna(z_ep)
        have_bp   = pd.notna(z_bp)
        have_sp   = pd.notna(z_sp)

        if is_fin:
            if have_ep and have_bp:
                s = 0.5*z_ep + 0.5*z_bp
            elif have_ep:
                s = z_ep
            elif have_bp:
                s = z_bp
            else:
                s = np.nan
        else:
            if have_ep and have_bp and have_sp:
                s = z_ep/3 + z_bp/3 + z_sp/3
            elif have_ep and have_bp:
                s = 0.5*z_ep + 0.5*z_bp
            elif have_ep and have_sp:
                s = 0.5*z_ep + 0.5*z_sp
            elif have_bp and have_sp:
                s = 0.5*z_bp + 0.5*z_sp
            elif have_ep:
                s = z_ep
            elif have_bp:
                s = z_bp
            else:
                s = np.nan

        result.loc[idx, "score_within"] = s

    # Re-z-score combined
    valid = result["score_within"].dropna()
    if len(valid) >= 5:
        result["score_combined"] = zscore(result["score_within"])
    else:
        result["score_combined"] = np.nan

    return result


# ---------------------------------------------------------------------------
# Backtest loop
# ---------------------------------------------------------------------------
def run_backtest(
    daily_prices: pd.DataFrame,
    daily_volume: pd.DataFrame,
    universe_df : pd.DataFrame,
    fund_df     : pd.DataFrame,
    sector_map  : pd.DataFrame,
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

        # Exclude known broken tickers
        universe = [t for t in universe if t not in EXCLUDE_TICKERS]

        signals = get_signals_at_date(
            date, universe, fund_df, sector_map, monthly_px, sym_map
        )
        if signals.empty or len(signals) < 10:
            print(f"  SKIP {date.strftime('%Y-%m')}: only {len(signals)} stocks with signal")
            continue

        scored         = compute_scores(signals)
        scored["date"] = date
        all_records.append(scored[[
            "nse_ticker", "date", "is_financial",
            "raw_ep", "raw_bp", "raw_sp",
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
            "value_return": long_ret - short_ret,
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

    print("Loading sector map ...")
    sector_map = load_sector_map()
    n_fin    = sector_map["is_financial"].sum()
    n_nonfin = (~sector_map["is_financial"]).sum()
    print(f"  {n_fin} financials, {n_nonfin} non-financials")

    print(f"\nRunning backtest: {BACKTEST_START} to {BACKTEST_END} ...")
    results = run_backtest(
        daily_prices, daily_volume, universe_df, fund_df, sector_map, sym_map
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
    print(f"VALUE FACTOR — SUMMARY")
    print(f"{'='*60}")
    print(f"  Months covered       : {n_months}")
    print(f"  Unique tickers       : {n_tickers}")
    print(f"  Avg stocks/month     : {avg_per_month:.0f}")
    print(f"  Financial rows       : {fin_pct:.1f}%")
    print(f"\n  Raw signal null rates:")
    print(f"    raw_ep : {results['raw_ep'].isna().mean()*100:.1f}%")
    print(f"    raw_bp : {results['raw_bp'].isna().mean()*100:.1f}%")
    print(f"    raw_sp : {results['raw_sp'].isna().mean()*100:.1f}%")
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
    ranked     = last_month.sort_values("score_combined", ascending=False)

    print(f"\n  Top 10 Value ({last_date.strftime('%Y-%m')}):")
    print(ranked[["nse_ticker","is_financial","raw_ep","raw_bp",
                  "raw_sp","score_combined"]].head(10).to_string(index=False))
    print(f"\n  Bottom 10 Value ({last_date.strftime('%Y-%m')}):")
    print(ranked[["nse_ticker","is_financial","raw_ep","raw_bp",
                  "raw_sp","score_combined"]].tail(10).to_string(index=False))

    print(f"\n  Stocks per month (first 5 and last 5):")
    monthly_counts = results.groupby("date").size()
    print(pd.concat([monthly_counts.head(5), monthly_counts.tail(5)]).to_string())

    results.to_parquet(OUTPUT_FILE)
    print(f"\nSaved -> {OUTPUT_FILE}")

    # Compute and save long-short return series
    print("\nComputing long-short return series ...")
    monthly_px = daily_prices.resample("ME").last()

    value_returns = compute_long_short_returns(
        results, "score_combined", monthly_px, sym_map
    )
    print_return_stats(value_returns, "VALUE")
    value_returns.to_parquet(OUTPUT_RET)
    print(f"  Saved -> {OUTPUT_RET}")

    print("Done.")


if __name__ == "__main__":
    main()
