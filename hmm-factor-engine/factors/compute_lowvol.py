"""
lowvol.py
=========
LOWVOL — Low Volatility Factor Construction

Signal   : realised annualised volatility over past 60 trading days
           circuit breaker days removed (|daily return| >= 5%)
           lowvol_signal = std(filtered_daily_returns) * sqrt(252)
Long leg : LOWEST vol decile (low vol is the good side)
Short leg: HIGHEST vol decile
Return   : equal-weight long leg minus equal-weight short leg (next month)
Min stocks: 20 in universe after ADTV + NaN filter

Two modes:
  BACKTEST — loops over all months, produces return series
  LIVE     — single month signal + portfolio for production use

Output
------
hmm-factor-engine/factors/data/lowvol_returns.parquet
  Columns: date, lowvol_return, long_return, short_return,
           long_count, short_count, universe_size, avg_days_excluded

Usage
-----
  python3 hmm-factor-engine/factors/lowvol.py
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
CONSTITUENT_CSV = Path("/home/ec2-user/nse-factor-engine/nifty_constituent_history/"
                       "nifty500_2005-01-01_to_2026-06-30.csv")
SYMBOL_MAP_FILE = DATA_DIR / "symbol_map.csv"
OUTPUT_FILE     = FACTORS_DIR / "lowvol_returns.parquet"

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
BACKTEST_START       = "2014-01"
BACKTEST_END         = "2026-06"
MIN_STOCKS           = 20
LOOKBACK_DAYS        = 60
CIRCUIT_BREAKER_PCT  = 0.05
MIN_DAYS_AFTER_FILTER= 20
LONG_PCTILE          = 0.10
SHORT_PCTILE         = 0.90



# ---------------------------------------------------------------------------
# Load helpers
# ---------------------------------------------------------------------------
def load_symbol_map(map_file: Path) -> dict:
    sym_map = {}
    with open(map_file) as f:
        for row in csv.DictReader(f):
            sym_map[row["csv_symbol"]] = row["parquet_col"]
    return sym_map


# ---------------------------------------------------------------------------
# Core signal function
# ---------------------------------------------------------------------------
def compute_lowvol_signal(
    daily_prices: pd.DataFrame,
    universe_symbols: list,
    date: pd.Timestamp,
    sym_map: dict,
) -> tuple[pd.Series, float]:
    """
    Compute low-vol signal for each stock in universe at month-end date T.
    signal = std(clean_daily_returns[-60 days]) * sqrt(252)
    Circuit breaker days (|return| >= 5%) removed per stock.
    """
    idx       = daily_prices.index
    valid_idx = idx[idx <= date]

    if len(valid_idx) < LOOKBACK_DAYS + 1:
        return pd.Series(dtype=float), 0.0

    window_prices = daily_prices.loc[valid_idx[-(LOOKBACK_DAYS + 1):]]

    signals           = {}
    days_excluded_list = []

    for sym in universe_symbols:
        col = sym_map.get(sym, sym)
        if col not in daily_prices.columns:
            continue

        px = window_prices[col].dropna()
        if len(px) < 10:
            continue

        daily_ret  = px.pct_change().dropna()
        mask       = daily_ret.abs() < CIRCUIT_BREAKER_PCT
        n_excluded = (~mask).sum()
        clean_ret  = daily_ret[mask]

        days_excluded_list.append(n_excluded)

        if len(clean_ret) < MIN_DAYS_AFTER_FILTER:
            continue

        vol = clean_ret.std() * np.sqrt(252)
        if pd.notna(vol) and vol > 0:
            signals[sym] = vol

    avg_excluded = np.mean(days_excluded_list) if days_excluded_list else 0.0
    return pd.Series(signals), avg_excluded


# ---------------------------------------------------------------------------
# Next month returns
# ---------------------------------------------------------------------------
def compute_next_month_returns(
    daily_prices: pd.DataFrame,
    universe_symbols: list,
    month_end: pd.Timestamp,
    next_month_end: pd.Timestamp,
    sym_map: dict,
) -> pd.Series:
    idx       = daily_prices.index
    curr_days = idx[(idx <= month_end) &
                    (idx >= month_end - pd.offsets.MonthBegin(1))]
    next_days = idx[(idx <= next_month_end) & (idx > month_end)]

    if curr_days.empty or next_days.empty:
        return pd.Series(dtype=float)

    t_end  = curr_days[-1]
    t1_end = next_days[-1]

    returns = {}
    for sym in universe_symbols:
        col = sym_map.get(sym, sym)
        if col not in daily_prices.columns:
            continue
        p_t  = daily_prices.loc[t_end,  col]
        p_t1 = daily_prices.loc[t1_end, col]
        if pd.notna(p_t) and pd.notna(p_t1) and p_t > 0:
            returns[sym] = (p_t1 / p_t) - 1

    return pd.Series(returns)


# ---------------------------------------------------------------------------
# Single month portfolio (LIVE mode)
# ---------------------------------------------------------------------------
def compute_lowvol_portfolio(
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

    signal, _ = compute_lowvol_signal(daily_prices, universe, date, sym_map)

    if len(signal) < MIN_STOCKS:
        return {"signal": signal, "long_stocks": [], "short_stocks": [],
                "long_weight": 0, "short_weight": 0}

    long_thresh  = signal.quantile(LONG_PCTILE)
    short_thresh = signal.quantile(SHORT_PCTILE)
    long_stocks  = signal[signal <= long_thresh].index.tolist()
    short_stocks = signal[signal >= short_thresh].index.tolist()

    return {
        "signal"      : signal,
        "long_stocks" : long_stocks,
        "short_stocks": short_stocks,
        "long_weight" : 1.0 / len(long_stocks)  if long_stocks  else 0,
        "short_weight": 1.0 / len(short_stocks) if short_stocks else 0,
    }


# ---------------------------------------------------------------------------
# Backtest loop
# ---------------------------------------------------------------------------
def run_backtest(
    daily_prices: pd.DataFrame,
    daily_volume: pd.DataFrame,
    universe_df: pd.DataFrame,
    sym_map: dict,
    start: str = BACKTEST_START,
    end: str   = BACKTEST_END,
) -> pd.DataFrame:
    """BACKTEST MODE — loop over all months, compute LOWVOL factor return series."""
    periods = pd.period_range(start=start, end=end, freq="M")

    records = []
    for i, period in enumerate(periods):
        month_end = period.to_timestamp(how="end").normalize()

        if i + 1 >= len(periods):
            continue
        next_month_end = periods[i + 1].to_timestamp(how="end").normalize()

        # Get clean universe with ADTV filter
        universe = get_clean_universe(
            month_end, daily_prices, daily_volume, universe_df, sym_map
        )

        if not universe:
            continue

        # Compute signal
        signal, avg_excluded = compute_lowvol_signal(
            daily_prices, universe, month_end, sym_map
        )

        if len(signal) < MIN_STOCKS:
            print(f"  SKIP {period}: only {len(signal)} stocks with signal")
            continue

        long_thresh  = signal.quantile(LONG_PCTILE)
        short_thresh = signal.quantile(SHORT_PCTILE)
        long_stocks  = signal[signal <= long_thresh].index.tolist()
        short_stocks = signal[signal >= short_thresh].index.tolist()

        if not long_stocks or not short_stocks:
            continue

        next_returns = compute_next_month_returns(
            daily_prices, universe, month_end, next_month_end, sym_map
        )

        if next_returns.empty:
            continue

        long_rets  = [next_returns[s] for s in long_stocks  if s in next_returns]
        short_rets = [next_returns[s] for s in short_stocks if s in next_returns]

        if not long_rets or not short_rets:
            continue

        long_ret   = np.mean(long_rets)
        short_ret  = np.mean(short_rets)
        lowvol_ret = long_ret - short_ret

        records.append({
            "date"              : month_end,
            "lowvol_return"     : lowvol_ret,
            "long_return"       : long_ret,
            "short_return"      : short_ret,
            "long_count"        : len(long_rets),
            "short_count"       : len(short_rets),
            "universe_size"     : len(signal),
            "avg_days_excluded" : round(avg_excluded, 2),
        })

    df = pd.DataFrame(records)
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date")
    return df


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    FACTORS_DIR.mkdir(parents=True, exist_ok=True)

    print("Loading symbol map ...")
    sym_map = load_symbol_map(SYMBOL_MAP_FILE)
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

    print(f"\nRunning backtest: {BACKTEST_START} to {BACKTEST_END} ...")
    print(f"  ADTV filter: time-varying (10/20/30cr)")
    results = run_backtest(daily_prices, daily_volume, universe_df, sym_map)

    if results.empty:
        print("ERROR: no results produced")
        return

    r        = results["lowvol_return"]
    ann_ret  = r.mean() * 12
    ann_vol  = r.std() * np.sqrt(12)
    sharpe   = ann_ret / ann_vol if ann_vol > 0 else 0
    cum      = (1 + r).cumprod()
    drawdown = (cum / cum.cummax() - 1).min()
    hit_rate = (r > 0).mean()

    print(f"\n{'='*50}")
    print(f"LOWVOL FACTOR RESULTS ({BACKTEST_START} to {BACKTEST_END})")
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
    print(f"  Avg days excluded   : {results['avg_days_excluded'].mean():.1f} per stock/month")

    print(f"\nFirst 5 rows:")
    print(results.head().to_string())
    print(f"\nLast 5 rows:")
    print(results.tail().to_string())

    results.to_parquet(OUTPUT_FILE)
    print(f"\nSaved -> {OUTPUT_FILE}")
    print("Done.")


if __name__ == "__main__":
    main()
