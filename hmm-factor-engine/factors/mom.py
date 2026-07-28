"""
mom.py
======
MOM — Momentum Factor Construction

Signal   : cumulative return months t-12 to t-2
           (skip t-1 to avoid short-term reversal)
Long leg : top decile by signal (highest momentum)
Short leg: bottom decile by signal (lowest momentum)
Return   : equal-weight long leg minus equal-weight short leg (next month)
Min stocks: 20 in universe after ADTV + NaN filter

Two modes:
  BACKTEST — loops over all months, produces return series
  LIVE     — single month signal + portfolio for production use

Output
------
hmm-factor-engine/factors/data/mom_returns.parquet
  Columns: date, mom_return, long_return, short_return,
           long_count, short_count, universe_size

Usage
-----
  python3 hmm-factor-engine/factors/mom.py
"""

import csv
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

# Add factors dir to path for universe import
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
OUTPUT_FILE     = FACTORS_DIR / "mom_returns.parquet"

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
BACKTEST_START  = "2014-01"
BACKTEST_END    = "2026-06"
MIN_STOCKS      = 20
LONG_PCTILE     = 0.90
SHORT_PCTILE    = 0.10



# ---------------------------------------------------------------------------
# Load helpers
# ---------------------------------------------------------------------------
def load_symbol_map(map_file: Path) -> dict:
    sym_map = {}
    with open(map_file) as f:
        for row in csv.DictReader(f):
            sym_map[row["csv_symbol"]] = row["parquet_col"]
    return sym_map


def load_monthly_prices(price_file: Path) -> pd.DataFrame:
    daily = pd.read_parquet(price_file)
    daily.index = pd.to_datetime(daily.index)
    monthly = daily.resample("ME").last()
    return monthly


# ---------------------------------------------------------------------------
# Core signal function (BACKTEST + LIVE)
# ---------------------------------------------------------------------------
def compute_mom_signal(
    monthly_prices: pd.DataFrame,
    universe_symbols: list,
    date: pd.Timestamp,
    sym_map: dict,
) -> pd.Series:
    """
    Compute momentum signal for each stock in universe at date T.
    Signal = (price[T-2] / price[T-12]) - 1
    """
    resolved = {}
    for sym in universe_symbols:
        col = sym_map.get(sym, sym)
        if col in monthly_prices.columns:
            resolved[sym] = col

    if not resolved:
        return pd.Series(dtype=float)

    idx = monthly_prices.index
    if date not in idx:
        return pd.Series(dtype=float)

    pos = idx.get_loc(date)
    if pos < 12:
        return pd.Series(dtype=float)

    t_minus_2  = idx[pos - 2]
    t_minus_12 = idx[pos - 12]

    signals = {}
    for sym, col in resolved.items():
        p_t2  = monthly_prices.loc[t_minus_2,  col]
        p_t12 = monthly_prices.loc[t_minus_12, col]
        if pd.notna(p_t2) and pd.notna(p_t12) and p_t12 > 0:
            signals[sym] = (p_t2 / p_t12) - 1

    return pd.Series(signals)


def compute_next_month_returns(
    monthly_prices: pd.DataFrame,
    universe_symbols: list,
    date: pd.Timestamp,
    sym_map: dict,
) -> pd.Series:
    idx = monthly_prices.index
    if date not in idx:
        return pd.Series(dtype=float)

    pos = idx.get_loc(date)
    if pos >= len(idx) - 1:
        return pd.Series(dtype=float)

    t_end  = idx[pos]
    t1_end = idx[pos + 1]

    returns = {}
    for sym in universe_symbols:
        col = sym_map.get(sym, sym)
        if col not in monthly_prices.columns:
            continue
        p_t  = monthly_prices.loc[t_end,  col]
        p_t1 = monthly_prices.loc[t1_end, col]
        if pd.notna(p_t) and pd.notna(p_t1) and p_t > 0:
            returns[sym] = (p_t1 / p_t) - 1

    return pd.Series(returns)


# ---------------------------------------------------------------------------
# Single month portfolio (LIVE mode)
# ---------------------------------------------------------------------------
def compute_mom_portfolio(
    daily_prices: pd.DataFrame,
    daily_volume: pd.DataFrame,
    universe_df: pd.DataFrame,
    date: pd.Timestamp,
    sym_map: dict,
) -> dict:
    """LIVE MODE — compute signal and portfolio for a single month T."""
    monthly_prices = daily_prices.resample("ME").last()

    universe = get_clean_universe(
        date, daily_prices, daily_volume, universe_df, sym_map
    )

    signal = compute_mom_signal(monthly_prices, universe, date, sym_map)

    if len(signal) < MIN_STOCKS:
        return {"signal": signal, "long_stocks": [], "short_stocks": [],
                "long_weight": 0, "short_weight": 0}

    long_thresh  = signal.quantile(LONG_PCTILE)
    short_thresh = signal.quantile(SHORT_PCTILE)
    long_stocks  = signal[signal >= long_thresh].index.tolist()
    short_stocks = signal[signal <= short_thresh].index.tolist()

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
    monthly_prices: pd.DataFrame,
    daily_prices: pd.DataFrame,
    daily_volume: pd.DataFrame,
    universe_df: pd.DataFrame,
    sym_map: dict,
    start: str = BACKTEST_START,
    end: str   = BACKTEST_END,
) -> pd.DataFrame:
    """BACKTEST MODE — loop over all months, compute MOM factor return series."""
    periods     = pd.period_range(start=start, end=end, freq="M")
    dates       = [p.to_timestamp(how="end").normalize() for p in periods]
    valid_dates = [d for d in dates if d in monthly_prices.index]

    records = []
    for date in valid_dates:
        # Get clean universe with ADTV filter
        universe = get_clean_universe(
            date, daily_prices, daily_volume, universe_df, sym_map
        )

        if not universe:
            continue

        # Compute signal
        signal = compute_mom_signal(monthly_prices, universe, date, sym_map)

        if len(signal) < MIN_STOCKS:
            print(f"  SKIP {date.strftime('%Y-%m')}: only {len(signal)} stocks")
            continue

        long_thresh  = signal.quantile(LONG_PCTILE)
        short_thresh = signal.quantile(SHORT_PCTILE)
        long_stocks  = signal[signal >= long_thresh].index.tolist()
        short_stocks = signal[signal <= short_thresh].index.tolist()

        if not long_stocks or not short_stocks:
            continue

        next_returns = compute_next_month_returns(
            monthly_prices, universe, date, sym_map
        )

        if next_returns.empty:
            continue

        long_rets  = [next_returns[s] for s in long_stocks  if s in next_returns]
        short_rets = [next_returns[s] for s in short_stocks if s in next_returns]

        if not long_rets or not short_rets:
            continue

        long_ret  = np.mean(long_rets)
        short_ret = np.mean(short_rets)
        mom_ret   = long_ret - short_ret

        records.append({
            "date"         : date,
            "mom_return"   : mom_ret,
            "long_return"  : long_ret,
            "short_return" : short_ret,
            "long_count"   : len(long_rets),
            "short_count"  : len(short_rets),
            "universe_size": len(signal),
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
    print(f"  Daily prices shape : {daily_prices.shape}")

    print("Building monthly prices ...")
    monthly_prices = daily_prices.resample("ME").last()
    print(f"  Monthly prices shape: {monthly_prices.shape}")

    print("Building point-in-time universe lookup ...")
    universe_df = build_universe_lookup(CONSTITUENT_CSV)
    print(f"  {len(universe_df)} rebalance snapshots loaded")

    print(f"\nRunning backtest: {BACKTEST_START} to {BACKTEST_END} ...")
    print(f"  ADTV filter: time-varying (10/20/30cr)")
    results = run_backtest(
        monthly_prices, daily_prices, daily_volume, universe_df, sym_map
    )

    if results.empty:
        print("ERROR: no results produced")
        return

    r        = results["mom_return"]
    ann_ret  = r.mean() * 12
    ann_vol  = r.std() * np.sqrt(12)
    sharpe   = ann_ret / ann_vol if ann_vol > 0 else 0
    cum      = (1 + r).cumprod()
    drawdown = (cum / cum.cummax() - 1).min()
    hit_rate = (r > 0).mean()

    print(f"\n{'='*50}")
    print(f"MOM FACTOR RESULTS ({BACKTEST_START} to {BACKTEST_END})")
    print(f"{'='*50}")
    print(f"  Months           : {len(results)}")
    print(f"  Ann. return      : {ann_ret*100:.2f}%")
    print(f"  Ann. volatility  : {ann_vol*100:.2f}%")
    print(f"  Sharpe ratio     : {sharpe:.3f}")
    print(f"  Max drawdown     : {drawdown*100:.2f}%")
    print(f"  Hit rate         : {hit_rate*100:.1f}%")
    print(f"  Avg universe     : {results['universe_size'].mean():.0f} stocks")
    print(f"  Avg long count   : {results['long_count'].mean():.0f}")
    print(f"  Avg short count  : {results['short_count'].mean():.0f}")

    print(f"\nFirst 5 rows:")
    print(results.head().to_string())
    print(f"\nLast 5 rows:")
    print(results.tail().to_string())

    results.to_parquet(OUTPUT_FILE)
    print(f"\nSaved -> {OUTPUT_FILE}")
    print("Done.")


if __name__ == "__main__":
    main()
