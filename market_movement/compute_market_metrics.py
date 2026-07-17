"""
Market Movement -- Metrics Computation
Separate from main NSE Factor Engine pipeline (Stage 8 scope).

Reads   : market_movement/data/index_prices.parquet
Output  : market_movement/data/market_movement_metrics.parquet          (rolling, all history)
          market_movement/data/market_movement_metrics_{RUN_DATE}.parquet (dated snapshot)

── Date convention (locked 2026-06-30, METHODOLOGY.md) ──
RUN_DATE   = IST calendar date this script executed -> filename
as_of_date = T, latest trading day reflected in the data -> column, computed from data

── Scope (locked with user, Stage 8) ──
1. Index metrics -- all 9 tickers, full set:
   - weekly close, 30W SMA slope, 20W support/resistance -> Weinstein 5-stage
     (Stage 1 Accumulation, Stage 2 Advancing, Stage 3 Distribution,
     Stage 4 Transition Breakdown, Stage 4 Declining -- volume-free,
     locked with user 2026-07-06). Computed for all tickers except ^INDIAVIX.
   - trailing 21D % return
   - 52-week high proximity
   - drawdown from rolling peak
   - Wilder RSI-14 (computed for all tickers including ^INDIAVIX)
2. VIX standalone (^INDIAVIX) -- 5-tier, 21D % change
3. 2x2 combo grid -- VIX (collapsed 3-tier) x Market Direction (Nifty 50 only, 3-tier)
   - Market direction threshold: +/-1 sigma on Nifty 50 21D returns = +/-3.58%
     (derived from 15mo real history, locked with user -- see chat)
   - VIX drives combo per India VIX being Nifty-50-options-based
   - 4 of 9 combo cells have narrative labels; 5 ship as raw tags (locked with user)

This is a SIGNAL-ONLY script. No deployment logic, no wiring into stage6_assemble.py
portfolio construction. Output is a column set to be joined in later, per user decision.
"""

import sys
import numpy as np
import pandas as pd
from datetime import date
from pathlib import Path

# ── Repo-root guard ──────────────────
if not Path("signals").is_dir():
    sys.exit(
        "ERROR: 'signals/' not found in current directory.\n"
        "This script must be run from the repo root (nse-factor-engine/), e.g.:\n"
        "    cd /home/ec2-user/nse-factor-engine/\n"
        "    python market_movement/compute_market_metrics.py"
    )

# ── Config ───────────────────────────
END_DATE = date.today()
RUN_DATE = END_DATE.strftime("%d%m%Y")

DATA_DIR    = Path("market_movement/data")
INPUT_PATH  = DATA_DIR / "index_prices.parquet"
ROLLING_OUT = DATA_DIR / "market_movement_metrics.parquet"
DATED_OUT   = DATA_DIR / "market_movement_metrics_{}.parquet".format(RUN_DATE)
LAST_RUN_PATH = DATA_DIR / "last_run_date_metrics.txt"

RET_WINDOW_DAYS  = 21     # trading days, ~1 month -- primary window, locked with user
WEEK_MA_WINDOW   = 30     # weeks, per weinstein.py structure
SMA_SHORT        = 150    # calendar days
SMA_LONG         = 200    # calendar days
LOOKBACK_52W_DAYS = 252   # ~52 weeks of trading days
RSI_PERIOD       = 14     # Wilder standard

VIX_SYMBOL     = "^INDIAVIX"
PRIMARY_MARKET = "^NSEI"   # Nifty 50 -- sole driver of combo's market-direction axis (locked with user)

# VIX 5-tier thresholds (21D % change) -- from user-provided cheat sheet
VIX_STABLE_BAND      = 7.0    # -7% to +7%
VIX_CONFIRMED_BAND   = 15.0   # beyond +/-15% = confirmed

# Market direction threshold (21D % change, Nifty 50 only) -- derived from real history, +/-1 sigma
MARKET_DIRECTION_THRESHOLD_PCT = 3.58   # locked with user (see chat derivation)


# ── Helpers ───────────────────────────

def _weekly_close(df_sym: pd.DataFrame) -> pd.Series:
    """Resample daily close to weekly (Friday), for weekly MA logic."""
    s = df_sym.set_index("date")["close"]
    return s.resample("W-FRI").last().dropna()


def _wilder_rsi(close: pd.Series, n: int = RSI_PERIOD) -> float:
    """
    Wilder RSI for a single price series. Returns latest RSI value or NaN.
    Uses Wilder smoothing (alpha = 1/N), matching TA-Lib / TradingView.
    """
    close = close.dropna().reset_index(drop=True)
    if len(close) < n + 1:
        return np.nan

    delta = close.diff().dropna()
    gain  = delta.clip(lower=0)
    loss  = (-delta).clip(lower=0)

    # Seed: simple mean of first N periods
    avg_gain = gain.iloc[:n].mean()
    avg_loss = loss.iloc[:n].mean()

    # Wilder smoothing
    for i in range(n, len(gain)):
        avg_gain = (avg_gain * (n - 1) + gain.iloc[i]) / n
        avg_loss = (avg_loss * (n - 1) + loss.iloc[i]) / n

    if avg_loss == 0:
        return 100.0

    rs  = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 4)


def compute_index_metrics(df_sym: pd.DataFrame) -> dict:
    """
    Compute the full index metric set for one symbol.
    df_sym : daily OHLCV for a single symbol, sorted by date ascending.
    Returns a dict of the latest available values (as of the last row).
    """
    df_sym = df_sym.sort_values("date").reset_index(drop=True)
    close  = df_sym["close"]
    symbol = df_sym["symbol"].iloc[0]
    latest_close = close.iloc[-1]
    latest_date  = df_sym["date"].iloc[-1]
    is_vix = (symbol == VIX_SYMBOL)

    if is_vix:
        # Weinstein is a price-trend model for equity/index instruments -- not applicable
        # to VIX (a volatility index), per user instruction. Skip entirely, don't compute.
        weinstein_state = "NOT_APPLICABLE"
        close_above_30w_ma    = np.nan
        ma_30w_slope_positive = np.nan
    else:
        # -- Weinstein 5-stage matrix (volume-free, locked with user 2026-07-06) --
        # Weekly close, 30W SMA, 30W SMA slope (3-week ROC), 20W rolling support/resistance
        # (shifted 1 week to avoid lookahead), and a 4-week whipsaw check for Stage 3.
        weekly = _weekly_close(df_sym)
        sma_30w   = weekly.rolling(WEEK_MA_WINDOW, min_periods=WEEK_MA_WINDOW).mean()
        slope_30w = ((sma_30w - sma_30w.shift(3)) / sma_30w.shift(3)) * 100
        resistance_20w = weekly.shift(1).rolling(20, min_periods=20).max()
        support_20w    = weekly.shift(1).rolling(20, min_periods=20).min()
        above_sma = weekly > sma_30w
        # interpretation (a), locked with user: both True and False present in trailing 4wk window
        whipsaw_4w = above_sma.rolling(4, min_periods=4).apply(lambda x: bool(x.any() and not x.all()), raw=True)

        latest_vals = {
            "close":      weekly.iloc[-1]           if len(weekly) >= 1 else np.nan,
            "sma_30w":    sma_30w.iloc[-1]           if len(sma_30w.dropna()) >= 1 else np.nan,
            "slope_30w":  slope_30w.iloc[-1]         if len(slope_30w.dropna()) >= 1 else np.nan,
            "resistance": resistance_20w.iloc[-1]    if len(resistance_20w.dropna()) >= 1 else np.nan,
            "support":    support_20w.iloc[-1]       if len(support_20w.dropna()) >= 1 else np.nan,
            "whipsaw":    whipsaw_4w.iloc[-1]        if len(whipsaw_4w.dropna()) >= 1 else np.nan,
        }
        v = latest_vals
        if any(pd.isna(v[k]) for k in ("close", "sma_30w", "slope_30w", "resistance", "support")):
            weinstein_state = "INSUFFICIENT_DATA"
        elif v["support"] <= v["close"] <= v["resistance"] and -1.0 <= v["slope_30w"] <= 1.0:
            weinstein_state = "STAGE_1_ACCUMULATION"
        elif v["close"] > v["sma_30w"] and v["slope_30w"] > 1.0:
            weinstein_state = "STAGE_2_ADVANCING"
        elif pd.notna(v["whipsaw"]) and bool(v["whipsaw"]) and -1.0 <= v["slope_30w"] <= 1.0:
            weinstein_state = "STAGE_3_DISTRIBUTION"
        elif v["close"] < v["support"] and v["slope_30w"] < -1.0:
            weinstein_state = "STAGE_4_TRANSITION_BREAKDOWN"
        elif v["close"] < v["sma_30w"] and v["slope_30w"] < -1.0:
            weinstein_state = "STAGE_4_DECLINING"
        else:
            weinstein_state = "TRANSITION_ZONE"   # no rule matched -- genuine fallback, not an error

        close_above_30w_ma    = bool(v["close"] > v["sma_30w"]) if pd.notna(v["sma_30w"]) else np.nan
        ma_30w_slope_positive = bool(v["slope_30w"] > 0) if pd.notna(v["slope_30w"]) else np.nan

    # -- 150D SMA vs 200D SMA -- kept as a separate descriptive column
    sma_150 = close.rolling(SMA_SHORT, min_periods=SMA_SHORT).mean()
    sma_200 = close.rolling(SMA_LONG,  min_periods=SMA_LONG).mean()
    sma_150_above_200 = np.nan
    if pd.notna(sma_150.iloc[-1]) and pd.notna(sma_200.iloc[-1]):
        sma_150_above_200 = bool(sma_150.iloc[-1] > sma_200.iloc[-1])

    # -- Trailing 21D % return --
    ret_21d = np.nan
    if len(close) > RET_WINDOW_DAYS:
        past_close = close.iloc[-(RET_WINDOW_DAYS + 1)]
        if pd.notna(past_close) and past_close != 0:
            ret_21d = round((latest_close / past_close - 1) * 100, 4)

    # -- 52-week high proximity (current close / 52w high) --
    window_252 = close.tail(LOOKBACK_52W_DAYS)
    high_52w = window_252.max()
    proximity_52w_high = round(latest_close / high_52w, 4) if pd.notna(high_52w) and high_52w != 0 else np.nan

    # -- Drawdown from rolling peak (current, not historical max) --
    cummax = close.cummax()
    drawdown_series = (close - cummax) / cummax
    current_drawdown = round(drawdown_series.iloc[-1] * 100, 4)   # negative or 0, in %

    # -- Wilder RSI-14 (all tickers including VIX) --
    rsi_14 = _wilder_rsi(close, RSI_PERIOD)

    return {
        "symbol": df_sym["symbol"].iloc[0],
        "as_of_date": latest_date,
        "close": round(latest_close, 4),
        "close_above_30w_ma": close_above_30w_ma,
        "ma_30w_slope_positive": ma_30w_slope_positive,
        "sma_150_above_200": sma_150_above_200,
        "weinstein_state": weinstein_state,
        "ret_21d_pct": ret_21d,
        "proximity_52w_high": proximity_52w_high,
        "current_drawdown_pct": current_drawdown,
        "rsi_14": rsi_14,
    }


def classify_vix_5tier(ret_21d_pct: float) -> str:
    """VIX 5-tier classification from 21D % change. Matches user-provided cheat sheet exactly."""
    if pd.isna(ret_21d_pct):
        return "INSUFFICIENT_DATA"
    if ret_21d_pct > VIX_CONFIRMED_BAND:
        return "GOING_UP_CONFIRMED"
    if ret_21d_pct > VIX_STABLE_BAND:
        return "DEVELOPING_UPTREND"
    if ret_21d_pct < -VIX_CONFIRMED_BAND:
        return "GOING_DOWN_CONFIRMED"
    if ret_21d_pct < -VIX_STABLE_BAND:
        return "DEVELOPING_DOWNTREND"
    return "STABLE"


def collapse_vix_to_3tier(vix_5tier: str) -> str:
    """Collapse 5-tier VIX state to 3-tier for the 2x2 combo grid (locked with user)."""
    if vix_5tier in ("DEVELOPING_UPTREND", "GOING_UP_CONFIRMED"):
        return "VIX_UP"
    if vix_5tier in ("DEVELOPING_DOWNTREND", "GOING_DOWN_CONFIRMED"):
        return "VIX_DOWN"
    if vix_5tier == "STABLE":
        return "VIX_STABLE"
    return "INSUFFICIENT_DATA"


def classify_market_direction(ret_21d_pct: float) -> str:
    """Market direction 3-tier, Nifty 50 only, +/-1 sigma threshold (locked with user)."""
    if pd.isna(ret_21d_pct):
        return "INSUFFICIENT_DATA"
    if ret_21d_pct > MARKET_DIRECTION_THRESHOLD_PCT:
        return "MARKET_UP"
    if ret_21d_pct < -MARKET_DIRECTION_THRESHOLD_PCT:
        return "MARKET_DOWN"
    return "MARKET_STABLE"


# 4 labeled cells (from user-provided reference table) + 5 raw tags (locked with user: ship as-is)
COMBO_LABELS = {
    ("VIX_UP",     "MARKET_UP"):     "FOMO_PRE_EVENT_ANXIETY",
    ("VIX_UP",     "MARKET_DOWN"):   "PANIC_REAL_CRASH",
    ("VIX_DOWN",   "MARKET_UP"):     "CONFIDENCE_HEALTHY_RALLY",
    ("VIX_STABLE", "MARKET_STABLE"): "COMPLACENCY_BOREDOM",
}


def classify_combo(vix_3tier: str, market_3tier: str) -> str:
    """9-cell combo grid. 4 labeled, 5 raw tags (locked with user)."""
    if vix_3tier == "INSUFFICIENT_DATA" or market_3tier == "INSUFFICIENT_DATA":
        return "INSUFFICIENT_DATA"
    label = COMBO_LABELS.get((vix_3tier, market_3tier))
    if label:
        return label
    return "{}_{}".format(vix_3tier, market_3tier)


# ── Main ───────────────────────────
print("=" * 60)
print("Market Movement -- Metrics Computation")
print("Run Date : {}".format(END_DATE))
print("=" * 60)

# ── Idempotency guard ──────────────────
if LAST_RUN_PATH.exists():
    last_run = LAST_RUN_PATH.read_text().strip()
    if last_run == END_DATE.strftime("%Y-%m-%d"):
        print("\n      Run already completed today (last_run_date = {}). Nothing to do. Exiting.".format(last_run))
        sys.exit(0)
    else:
        print("      Last run date : {} -- proceeding".format(last_run))
else:
    print("      No last run date found -- first run")

if not INPUT_PATH.exists():
    sys.exit("ERROR: {} not found. Run fetch_index_data.py first.".format(INPUT_PATH))

prices = pd.read_parquet(INPUT_PATH)
prices["date"] = pd.to_datetime(prices["date"])

print("\n[1/3] Loaded {} rows | {} symbols".format(prices.shape[0], prices["symbol"].nunique()))

# -- Compute index metrics per symbol --
print("\n[2/3] Computing index metrics...")
rows = []
for symbol, grp in prices.groupby("symbol"):
    m = compute_index_metrics(grp)
    rows.append(m)
    print("  {:22s} state={:9s} ret_21d={:+7.2f}%  drawdown={:+7.2f}%  rsi_14={:.1f}".format(
        symbol, m["weinstein_state"],
        m["ret_21d_pct"] if pd.notna(m["ret_21d_pct"]) else float("nan"),
        m["current_drawdown_pct"] if pd.notna(m["current_drawdown_pct"]) else float("nan"),
        m["rsi_14"] if pd.notna(m["rsi_14"]) else float("nan"),
    ))

metrics_df = pd.DataFrame(rows)

# -- VIX standalone 5-tier --
vix_row = metrics_df[metrics_df["symbol"] == VIX_SYMBOL]
if vix_row.empty:
    sys.exit("ERROR: {} not found in fetched data -- cannot compute VIX/combo metrics.".format(VIX_SYMBOL))
vix_ret_21d = vix_row["ret_21d_pct"].iloc[0]
vix_5tier   = classify_vix_5tier(vix_ret_21d)
vix_3tier   = collapse_vix_to_3tier(vix_5tier)

# -- Market direction (Nifty 50 only) --
market_row = metrics_df[metrics_df["symbol"] == PRIMARY_MARKET]
if market_row.empty:
    sys.exit("ERROR: {} not found in fetched data -- cannot compute combo metrics.".format(PRIMARY_MARKET))
market_ret_21d = market_row["ret_21d_pct"].iloc[0]
market_3tier   = classify_market_direction(market_ret_21d)

# -- Combo --
combo_state = classify_combo(vix_3tier, market_3tier)

print("\n[3/3] VIX & combo classification...")
print("  VIX (^INDIAVIX)  21D ret = {:+.2f}%  -> 5-tier: {}  -> 3-tier: {}".format(
    vix_ret_21d if pd.notna(vix_ret_21d) else float("nan"), vix_5tier, vix_3tier))
print("  Market (^NSEI)   21D ret = {:+.2f}%  -> 3-tier: {}".format(
    market_ret_21d if pd.notna(market_ret_21d) else float("nan"), market_3tier))
print("  Combo state      : {}".format(combo_state))

# Stamp combo/VIX classification onto every row (applies to all 9 tickers, same value repeated)
as_of_date = metrics_df["as_of_date"].max()
metrics_df["vix_ret_21d_pct"]    = vix_ret_21d
metrics_df["vix_5tier"]          = vix_5tier
metrics_df["vix_3tier"]          = vix_3tier
metrics_df["market_ret_21d_pct"] = market_ret_21d
metrics_df["market_3tier"]       = market_3tier
metrics_df["combo_state"]        = combo_state
metrics_df["as_of_date"]         = as_of_date
metrics_df["run_date"]           = END_DATE

DATA_DIR.mkdir(parents=True, exist_ok=True)
metrics_df.to_parquet(ROLLING_OUT, index=False)
metrics_df.to_parquet(DATED_OUT, index=False)

# Update last run date -- only after successful save
LAST_RUN_PATH.write_text(END_DATE.strftime("%Y-%m-%d"))

print("\n" + "=" * 60)
print("SUMMARY")
print("  {} : {} rows".format(ROLLING_OUT.name, metrics_df.shape[0]))
print("  {} : {} rows".format(DATED_OUT.name, metrics_df.shape[0]))
print("  as_of_date (T)      : {}".format(as_of_date))
print("  RUN_DATE (filename) : {}".format(RUN_DATE))
print("  Combo state         : {}".format(combo_state))
print("=" * 60)
