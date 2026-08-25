"""
Market Breadth Metrics
Run from repo root: python3 market_movement/compute_breadth_metrics.py

Reads  : data/prices.parquet
         universe/universe_{DDMMYYYY}.parquet  (latest by parsed date)
Writes : market_movement/data/breadth_metrics.parquet       (rolling)
         market_movement/data/breadth_metrics_{DDMMYYYY}.parquet (dated snapshot)

Indicators (all on in_universe=True subset only):
  1. Advance-Decline Line     -- cumulative (advancing - declining)
  2. Advance-Decline Ratio    -- advancing / declining per day
  3. New Highs / New Lows     -- 52-week window (252 trading days)
  4. McClellan Oscillator     -- EMA(19, net_adv) - EMA(39, net_adv)
  5. TRIN (Arms Index)        -- (adv_count/dec_count) / (adv_vol/dec_vol)
  6. % Above 50-day SMA       -- fraction of universe above own 50-day SMA
  7. % Above 200-day SMA      -- fraction of universe above own 200-day SMA

Advancing : close(T) > close(T-1)
Declining : close(T) < close(T-1)
Unchanged : excluded from both counts (standard convention)

Date convention:
  RUN_DATE   = IST calendar date this script ran  -> filename suffix
  as_of_date = latest trading date in prices data -> column inside file
"""

import sys
import glob
import numpy as np
import pandas as pd
from datetime import date, datetime
from pathlib import Path

# -- Repo-root guard ----------------------------------------------------------
if not Path("signals").is_dir():
    sys.exit(
        "ERROR: 'signals/' not found. "
        "Run from repo root: cd /home/ec2-user/nse-factor-engine/"
    )

# -- Config -------------------------------------------------------------------
RUN_DATE     = date.today()
RUN_DATE_STR = RUN_DATE.strftime("%d%m%Y")

PRICES_PATH  = Path("data/prices.parquet")
UNIVERSE_DIR = Path("universe")
DATA_DIR     = Path("market_movement/data")
ROLLING_OUT  = DATA_DIR / "breadth_metrics.parquet"
DATED_OUT    = DATA_DIR / f"breadth_metrics_{RUN_DATE_STR}.parquet"
LAST_RUN_PATH = DATA_DIR / "last_run_date_breadth.txt"

SMA_SHORT    = 50
SMA_LONG     = 200
NH_NL_WINDOW = 252   # 52-week in trading days
EMA_SHORT    = 19    # McClellan fast EMA
EMA_LONG     = 39    # McClellan slow EMA

# Interpretation bands (calibrate after ~4 weeks of live data)
MCCLELLAN_OB =  100
MCCLELLAN_OS = -100
TRIN_BEARISH =  1.20
TRIN_BULLISH =  0.80
ADR_BULL_ZONE = 1.50
ADR_BEAR_ZONE = 0.67

# -- Idempotency guard --------------------------------------------------------
if LAST_RUN_PATH.exists():
    last = LAST_RUN_PATH.read_text().strip()
    if last == RUN_DATE.strftime("%Y-%m-%d"):
        print(f"Already ran today ({last}). Nothing to do. Exiting.")
        sys.exit(0)
    print(f"Last run: {last} -- proceeding")
else:
    print("No last run date found -- first run")

print("=" * 60)
print("Market Breadth -- Metrics Computation")
print(f"Run date : {RUN_DATE}")
print("=" * 60)

# -- Load prices --------------------------------------------------------------
if not PRICES_PATH.exists():
    sys.exit(f"ERROR: {PRICES_PATH} not found. Run universe/run_universe.py first.")

prices = pd.read_parquet(PRICES_PATH)
prices["date"] = pd.to_datetime(prices["date"])
as_of_date = prices["date"].max()
print(f"\n[1/4] Prices: {len(prices):,} rows | "
      f"{prices['symbol'].nunique()} symbols | "
      f"{prices['date'].min().date()} -> {as_of_date.date()}")

# -- Load latest universe (parsed-date sort, not alphabetical) ----------------
def _parse_universe_date(path):
    stem = Path(path).stem
    return datetime.strptime(stem.replace("universe_", ""), "%d%m%Y")

universe_files = sorted(glob.glob(str(UNIVERSE_DIR / "universe_*.parquet")),
                        key=_parse_universe_date)
if not universe_files:
    sys.exit(f"ERROR: No universe_*.parquet found in {UNIVERSE_DIR}/")

latest_universe_path = universe_files[-1]
universe = pd.read_parquet(latest_universe_path)
in_universe_symbols = set(universe[universe["in_universe"] == True]["symbol"])
print(f"[1/4] Universe: {Path(latest_universe_path).name} | "
      f"{len(in_universe_symbols)} in_universe=True symbols")

# -- Filter to in-universe stocks only ----------------------------------------
prices = prices[prices["symbol"].isin(in_universe_symbols)].copy()
prices = prices.sort_values(["symbol", "date"]).reset_index(drop=True)
print(f"[1/4] After filter: {len(prices):,} rows | "
      f"{prices['symbol'].nunique()} symbols")

# -- Per-stock signals --------------------------------------------------------
print("\n[2/4] Computing per-stock daily signals...")

def compute_stock_signals(grp):
    grp = grp.sort_values("date").copy()
    grp["prev_close"] = grp["close"].shift(1)

    grp["advancing"] = (grp["close"] > grp["prev_close"]).astype(int)
    grp["declining"] = (grp["close"] < grp["prev_close"]).astype(int)

    grp["adv_volume"] = grp["volume"] * grp["advancing"]
    grp["dec_volume"] = grp["volume"] * grp["declining"]

    # 52-week high/low: compare today's close to max/min of prior 252 closes
    roll_high = grp["close"].shift(1).rolling(NH_NL_WINDOW, min_periods=NH_NL_WINDOW).max()
    roll_low  = grp["close"].shift(1).rolling(NH_NL_WINDOW, min_periods=NH_NL_WINDOW).min()
    grp["new_high"] = (grp["close"] >= roll_high).astype(int)
    grp["new_low"]  = (grp["close"] <= roll_low).astype(int)

    # SMA participation
    sma_50  = grp["close"].rolling(SMA_SHORT, min_periods=SMA_SHORT).mean()
    sma_200 = grp["close"].rolling(SMA_LONG,  min_periods=SMA_LONG).mean()
    grp["above_50sma"]  = np.where(sma_50.isna(), np.nan,
                                   (grp["close"] > sma_50).astype(float))
    grp["above_200sma"] = np.where(sma_200.isna(), np.nan,
                                   (grp["close"] > sma_200).astype(float))

    return grp[["date",
                "advancing", "declining",
                "adv_volume", "dec_volume",
                "new_high", "new_low",
                "above_50sma", "above_200sma"]]

stock_signals = (
    prices.groupby("symbol", group_keys=False)
          .apply(compute_stock_signals, include_groups=False)
)
stock_signals = stock_signals.reset_index(level=0)  # brings symbol back from index
print(f"[2/4] Stock signals: {len(stock_signals):,} rows")

# -- Aggregate to daily breadth -----------------------------------------------
print("\n[3/4] Aggregating to daily breadth indicators...")

daily = (
    stock_signals
    .groupby("date")
    .agg(
        adv_count        =("advancing",   "sum"),
        dec_count        =("declining",   "sum"),
        adv_volume       =("adv_volume",  "sum"),
        dec_volume       =("dec_volume",  "sum"),
        new_highs        =("new_high",    "sum"),
        new_lows         =("new_low",     "sum"),
        pct_above_50sma  =("above_50sma", "mean"),
        pct_above_200sma =("above_200sma","mean"),
    )
    .reset_index()
    .sort_values("date")
)

# Indicator 1: A/D Line
daily["net_advances"] = daily["adv_count"] - daily["dec_count"]
daily["ad_line"]      = daily["net_advances"].cumsum()

# Indicator 2: ADR
daily["adr"] = np.where(
    daily["dec_count"] > 0,
    (daily["adv_count"] / daily["dec_count"]).round(4),
    np.nan
)

# Indicator 4: McClellan Oscillator
daily["ema_19"] = daily["net_advances"].ewm(span=EMA_SHORT, adjust=False).mean().round(4)
daily["ema_39"] = daily["net_advances"].ewm(span=EMA_LONG,  adjust=False).mean().round(4)
daily["mcclellan"] = (daily["ema_19"] - daily["ema_39"]).round(4)

# Indicator 5: TRIN
daily["trin"] = np.where(
    (daily["adv_count"] > 0) & (daily["dec_count"] > 0) &
    (daily["adv_volume"] > 0) & (daily["dec_volume"] > 0),
    ((daily["adv_count"] / daily["dec_count"]) /
     (daily["adv_volume"] / daily["dec_volume"])).round(4),
    np.nan
)

# Scale SMA participation to 0-100
daily["pct_above_50sma"]  = (daily["pct_above_50sma"]  * 100).round(2)
daily["pct_above_200sma"] = (daily["pct_above_200sma"] * 100).round(2)

# -- Signal labels ------------------------------------------------------------
def _mcclellan_signal(v):
    if pd.isna(v):         return "INSUFFICIENT_DATA"
    if v >  MCCLELLAN_OB:  return "OVERBOUGHT"
    if v < MCCLELLAN_OS:   return "OVERSOLD"
    if v >  0:             return "BULLISH"
    if v <  0:             return "BEARISH"
    return "NEUTRAL"

def _trin_signal(v):
    if pd.isna(v):         return "INSUFFICIENT_DATA"
    if v > TRIN_BEARISH:   return "BEARISH"
    if v < TRIN_BULLISH:   return "BULLISH"
    return "NEUTRAL"

def _adr_signal(v):
    if pd.isna(v):         return "INSUFFICIENT_DATA"
    if v > ADR_BULL_ZONE:  return "STRONG_BREADTH"
    if v < ADR_BEAR_ZONE:  return "WEAK_BREADTH"
    return "NEUTRAL"

daily["mcclellan_signal"] = daily["mcclellan"].apply(_mcclellan_signal)
daily["trin_signal"]      = daily["trin"].apply(_trin_signal)
daily["adr_signal"]       = daily["adr"].apply(_adr_signal)

# -- Metadata + column order --------------------------------------------------
daily["as_of_date"]    = as_of_date
daily["run_date"]      = RUN_DATE
daily["universe_size"] = len(in_universe_symbols)

col_order = [
    "date", "as_of_date", "run_date", "universe_size",
    "adv_count", "dec_count", "net_advances", "ad_line",
    "adr", "adr_signal",
    "new_highs", "new_lows",
    "ema_19", "ema_39", "mcclellan", "mcclellan_signal",
    "adv_volume", "dec_volume", "trin", "trin_signal",
    "pct_above_50sma", "pct_above_200sma",
]
daily = daily[col_order]

# -- Write --------------------------------------------------------------------
DATA_DIR.mkdir(parents=True, exist_ok=True)
daily.to_parquet(ROLLING_OUT, index=False)
daily.to_parquet(DATED_OUT,   index=False)
LAST_RUN_PATH.write_text(RUN_DATE.strftime("%Y-%m-%d"))

# -- Summary ------------------------------------------------------------------
latest = daily.iloc[-1]
p200   = latest["pct_above_200sma"]
print(f"\n[4/4] Written: {ROLLING_OUT}  ({len(daily)} rows)")
print()
print("=" * 60)
print("BREADTH SUMMARY (latest trading day)")
print("=" * 60)
print(f"  as_of_date       : {latest['date'].date()}")
print(f"  Universe size    : {latest['universe_size']}")
print(f"  Advancing        : {int(latest['adv_count'])}")
print(f"  Declining        : {int(latest['dec_count'])}")
print(f"  Net advances     : {int(latest['net_advances'])}")
print(f"  A/D Line         : {latest['ad_line']:.0f}")
print(f"  ADR              : {latest['adr']:.3f}  [{latest['adr_signal']}]")
print(f"  New Highs        : {int(latest['new_highs'])}")
print(f"  New Lows         : {int(latest['new_lows'])}")
print(f"  McClellan        : {latest['mcclellan']:.2f}  [{latest['mcclellan_signal']}]")
print(f"  TRIN             : {latest['trin']:.3f}  [{latest['trin_signal']}]")
print(f"  % > 50-day SMA   : {latest['pct_above_50sma']:.1f}%")
print(f"  % > 200-day SMA  : {p200:.1f}%" if pd.notna(p200) else "  % > 200-day SMA  : NaN")
print("=" * 60)
