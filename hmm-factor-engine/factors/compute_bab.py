"""
bab.py
======
BAB — Betting Against Beta Factor Construction

Signal   : rolling 60-month OLS beta vs Nifty 500 excess return
           beta = cov(stock_excess, nifty_excess) / var(nifty_excess)
           minimum 36 monthly observations required
           only positive beta stocks included (negative beta excluded)
Long leg : lowest beta decile, levered to beta=1 (max 2x leverage)
Short leg: highest beta decile, de-levered to beta=1 (max 2x)
Return   : (long_raw * leverage_long) - (short_raw * leverage_short)

Two modes:
  BACKTEST — loops over all months, produces return series
  LIVE     — single month signal + portfolio for production use

Output
------
hmm-factor-engine/factors/data/bab_returns.parquet
  Columns: date, bab_return, long_return, short_return,
           long_count, short_count, universe_size,
           avg_beta_long, avg_beta_short

Usage
-----
  python3 hmm-factor-engine/factors/bab.py
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
HMM_FILE        = DATA_DIR / "nifty500_hmm_data.parquet"
CONSTITUENT_CSV = Path("/home/ec2-user/nse-factor-engine/nifty_constituent_history/"
                       "nifty500_2005-01-01_to_2026-06-30.csv")
SYMBOL_MAP_FILE = DATA_DIR / "symbol_map.csv"
OUTPUT_FILE     = FACTORS_DIR / "bab_returns.parquet"
SAVE_SIGNALS    = True
SIGNALS_FILE    = FACTORS_DIR / "factor_bab.parquet"

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
BACKTEST_START  = "2014-01"
BACKTEST_END    = "2026-06"
MIN_STOCKS      = 20
BETA_WINDOW     = 60
MIN_OBS         = 36
LONG_PCTILE     = 0.10
SHORT_PCTILE    = 0.90

MAX_LEVERAGE    = 2.0
MIN_BETA        = 0.20    # exclude stocks with beta < 0.20 from long leg
MAX_BETA        = 5.0     # exclude stocks with beta > 5.0

RFR_SCHEDULE = [
    ("2005-04", "2011-12", 0.080),
    ("2012-01", "2019-12", 0.070),
    ("2020-01", "2021-12", 0.050),
    ("2022-01", "2099-12", 0.070),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def load_symbol_map(map_file: Path) -> dict:
    sym_map = {}
    with open(map_file) as f:
        for row in csv.DictReader(f):
            sym_map[row["csv_symbol"]] = row["parquet_col"]
    return sym_map


def build_rfr_series(index: pd.DatetimeIndex) -> pd.Series:
    rfr = pd.Series(np.nan, index=index, name="rfr_monthly")
    for start, end, annual_rate in RFR_SCHEDULE:
        mask = (index >= pd.Period(start, "M").to_timestamp()) & \
               (index <= pd.Period(end, "M").to_timestamp(how="end"))
        rfr[mask] = annual_rate / 12
    return rfr


def load_nifty_excess_returns(hmm_file: Path) -> pd.Series:
    hmm    = pd.read_parquet(hmm_file)
    excess = hmm["excess_return"].copy()
    excess.index = pd.to_datetime(excess.index)
    print(f"  Nifty excess returns: {excess.index[0].date()} -> "
          f"{excess.index[-1].date()} ({len(excess)} months)")
    return excess


def build_monthly_stock_excess(price_file: Path) -> pd.DataFrame:
    daily = pd.read_parquet(price_file)
    daily.index = pd.to_datetime(daily.index)
    monthly     = daily.resample("ME").last()
    monthly_ret = monthly.pct_change()
    rfr         = build_rfr_series(monthly_ret.index)
    stock_excess = monthly_ret.subtract(rfr, axis=0)
    return stock_excess


# ---------------------------------------------------------------------------
# Core signal — compute beta for each stock
# ---------------------------------------------------------------------------
def compute_bab_signal(
    stock_excess: pd.DataFrame,
    nifty_excess: pd.Series,
    universe_symbols: list,
    date: pd.Timestamp,
    sym_map: dict,
) -> pd.Series:
    """
    Compute rolling 60-month beta for each stock at month T.
    Only includes stocks with positive beta >= MIN_BETA.
    Winsorises at MAX_BETA.
    """
    idx = stock_excess.index
    if date not in idx:
        return pd.Series(dtype=float)

    pos = idx.get_loc(date)
    if pos < MIN_OBS:
        return pd.Series(dtype=float)

    start_pos  = max(0, pos - BETA_WINDOW + 1)
    window_idx = idx[start_pos : pos + 1]

    nifty_w = nifty_excess.reindex(window_idx).dropna()
    if len(nifty_w) < MIN_OBS:
        return pd.Series(dtype=float)

    betas = {}
    for sym in universe_symbols:
        col = sym_map.get(sym, sym)
        if col not in stock_excess.columns:
            continue

        stock_w = stock_excess[col].reindex(window_idx)
        common  = pd.concat([stock_w, nifty_w], axis=1).dropna()
        if len(common) < MIN_OBS:
            continue

        s_ret = common.iloc[:, 0]
        n_ret = common.iloc[:, 1]
        nv    = n_ret.var()
        if nv == 0:
            continue

        beta = s_ret.cov(n_ret) / nv

        # Exclude negative beta and extreme beta stocks
        if pd.notna(beta) and MIN_BETA <= beta <= MAX_BETA:
            betas[sym] = beta

    return pd.Series(betas)


# ---------------------------------------------------------------------------
# Single month portfolio (LIVE mode)
# ---------------------------------------------------------------------------
def compute_bab_portfolio(
    stock_excess: pd.DataFrame,
    nifty_excess: pd.Series,
    daily_prices: pd.DataFrame,
    daily_volume: pd.DataFrame,
    universe_df: pd.DataFrame,
    date: pd.Timestamp,
    sym_map: dict,
) -> dict:
    """LIVE MODE — compute signal and portfolio for a single month T."""
    universe = get_clean_universe(
        date, daily_prices, daily_volume, universe_df, sym_map
    )

    signal = compute_bab_signal(
        stock_excess, nifty_excess, universe, date, sym_map
    )

    if len(signal) < MIN_STOCKS:
        return {"signal": signal, "long_stocks": [], "short_stocks": [],
                "avg_beta_long": np.nan, "avg_beta_short": np.nan}

    long_thresh  = signal.quantile(LONG_PCTILE)
    short_thresh = signal.quantile(SHORT_PCTILE)
    long_stocks  = signal[signal <= long_thresh].index.tolist()
    short_stocks = signal[signal >= short_thresh].index.tolist()

    return {
        "signal"        : signal,
        "long_stocks"   : long_stocks,
        "short_stocks"  : short_stocks,
        "avg_beta_long" : signal[long_stocks].mean(),
        "avg_beta_short": signal[short_stocks].mean(),
    }


# ---------------------------------------------------------------------------
# Backtest loop
# ---------------------------------------------------------------------------
def run_backtest(
    stock_excess: pd.DataFrame,
    nifty_excess: pd.Series,
    daily_prices: pd.DataFrame,
    daily_volume: pd.DataFrame,
    universe_df: pd.DataFrame,
    sym_map: dict,
    start: str = BACKTEST_START,
    end: str   = BACKTEST_END,
) -> pd.DataFrame:
    """BACKTEST MODE — loop over all months, compute BAB factor return series."""
    periods = pd.period_range(start=start, end=end, freq="M")
    idx     = stock_excess.index

    records = []
    signal_records = []
    for i, period in enumerate(periods):
        date = period.to_timestamp(how="end").normalize()

        if i + 1 >= len(periods):
            continue
        next_date = periods[i + 1].to_timestamp(how="end").normalize()

        # Align to actual index
        candidates = idx[idx <= date + pd.offsets.Day(5)]
        if candidates.empty:
            continue
        date = candidates[-1]

        candidates = idx[idx <= next_date + pd.offsets.Day(5)]
        if candidates.empty:
            continue
        next_date = candidates[-1]

        if date == next_date:
            continue

        # Get clean universe with ADTV filter
        universe = get_clean_universe(
            date, daily_prices, daily_volume, universe_df, sym_map
        )

        if not universe:
            continue

        # Compute betas — only positive beta stocks
        betas = compute_bab_signal(
            stock_excess, nifty_excess, universe, date, sym_map
        )

        if len(betas) < MIN_STOCKS:
            print(f"  SKIP {period}: only {len(betas)} stocks with valid beta")
            continue

        if SAVE_SIGNALS:
            pct = 1 - betas.rank(pct=True)  # lower beta = higher percentile
            for sym, raw in betas.items():
                signal_records.append({"nse_ticker": sym, "date": date, "signal": float(raw), "percentile": float(pct[sym])})

        # Form portfolios
        long_thresh  = betas.quantile(LONG_PCTILE)
        short_thresh = betas.quantile(SHORT_PCTILE)
        long_stocks  = betas[betas <= long_thresh].index.tolist()
        short_stocks = betas[betas >= short_thresh].index.tolist()

        if not long_stocks or not short_stocks:
            continue

        avg_beta_long  = betas[long_stocks].mean()
        avg_beta_short = betas[short_stocks].mean()

        if avg_beta_long < MIN_BETA or avg_beta_short < MIN_BETA:
            continue

        # Leverage capped at MAX_LEVERAGE
        leverage_long  = min(1.0 / avg_beta_long,  MAX_LEVERAGE)
        leverage_short = min(1.0 / avg_beta_short, MAX_LEVERAGE)

        # Next month excess returns
        long_rets, short_rets = [], []

        for sym in long_stocks:
            col = sym_map.get(sym, sym)
            if col not in stock_excess.columns:
                continue
            r = stock_excess.loc[next_date, col] if next_date in idx else np.nan
            if pd.notna(r):
                long_rets.append(r)

        for sym in short_stocks:
            col = sym_map.get(sym, sym)
            if col not in stock_excess.columns:
                continue
            r = stock_excess.loc[next_date, col] if next_date in idx else np.nan
            if pd.notna(r):
                short_rets.append(r)

        if not long_rets or not short_rets:
            continue

        raw_long  = np.mean(long_rets)
        raw_short = np.mean(short_rets)

        bab_long  = raw_long  * leverage_long
        bab_short = raw_short * leverage_short
        bab_ret   = bab_long  - bab_short

        records.append({
            "date"          : date,
            "bab_return"    : bab_ret,
            "long_return"   : bab_long,
            "short_return"  : bab_short,
            "long_count"    : len(long_rets),
            "short_count"   : len(short_rets),
            "universe_size" : len(betas),
            "avg_beta_long" : round(avg_beta_long,  3),
            "avg_beta_short": round(avg_beta_short, 3),
        })

    df = pd.DataFrame(records)
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date")
    return df, signal_records


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    FACTORS_DIR.mkdir(parents=True, exist_ok=True)

    print("Loading symbol map ...")
    sym_map = load_symbol_map(SYMBOL_MAP_FILE)
    print(f"  {len(sym_map)} mappings loaded")

    print("\nLoading Nifty 500 excess return series ...")
    nifty_excess = load_nifty_excess_returns(HMM_FILE)

    print("\nLoading daily prices and volume ...")
    daily_prices = pd.read_parquet(PRICE_FILE)
    daily_prices.index = pd.to_datetime(daily_prices.index)
    daily_volume = pd.read_parquet(VOLUME_FILE)
    daily_volume.index = pd.to_datetime(daily_volume.index)
    print(f"  Daily prices shape: {daily_prices.shape}")

    print("\nBuilding monthly stock excess returns ...")
    stock_excess = build_monthly_stock_excess(PRICE_FILE)
    print(f"  Stock excess shape: {stock_excess.shape}")

    print("\nBuilding point-in-time universe lookup ...")
    universe_df = build_universe_lookup(CONSTITUENT_CSV)
    print(f"  {len(universe_df)} rebalance snapshots loaded")

    print(f"\nRunning backtest: {BACKTEST_START} to {BACKTEST_END} ...")
    print(f"  ADTV filter     : time-varying (10/20/30cr)")
    print(f"  Beta range      : [{MIN_BETA}, {MAX_BETA}]")
    print(f"  Max leverage    : {MAX_LEVERAGE}x")

    results, signal_records = run_backtest(
        stock_excess, nifty_excess,
        daily_prices, daily_volume,
        universe_df, sym_map
    )

    if results.empty:
        print("ERROR: no results produced")
        return

    r        = results["bab_return"]
    ann_ret  = r.mean() * 12
    ann_vol  = r.std() * np.sqrt(12)
    sharpe   = ann_ret / ann_vol if ann_vol > 0 else 0
    cum      = (1 + r).cumprod()
    drawdown = (cum / cum.cummax() - 1).min()
    hit_rate = (r > 0).mean()

    print(f"\n{'='*50}")
    print(f"BAB FACTOR RESULTS ({BACKTEST_START} to {BACKTEST_END})")
    print(f"{'='*50}")
    print(f"  Months              : {len(results)}")
    print(f"  Ann. return         : {ann_ret*100:.2f}%")
    print(f"  Ann. volatility     : {ann_vol*100:.2f}%")
    print(f"  Sharpe ratio        : {sharpe:.3f}")
    print(f"  Max drawdown        : {drawdown*100:.2f}%")
    print(f"  Hit rate            : {hit_rate*100:.1f}%")
    print(f"  Avg universe        : {results['universe_size'].mean():.0f} stocks")
    print(f"  Avg long count      : {results['long_count'].mean():.0f}")
    print(f"  Avg short count     : {results['short_count'].mean():.0f}")
    print(f"  Avg beta long leg   : {results['avg_beta_long'].mean():.3f}")
    print(f"  Avg beta short leg  : {results['avg_beta_short'].mean():.3f}")

    print(f"\nFirst 5 rows:")
    print(results.head().to_string())
    print(f"\nLast 5 rows:")
    print(results.tail().to_string())

    results.to_parquet(OUTPUT_FILE)
    print(f"\nSaved -> {OUTPUT_FILE}")
    if SAVE_SIGNALS and signal_records:
        sig_df = pd.DataFrame(signal_records)
        sig_df.to_parquet(SIGNALS_FILE, index=False)
        print(f"Saved signals -> {SIGNALS_FILE}  ({len(sig_df)} rows)")
    print("Done.")


if __name__ == "__main__":
    main()
