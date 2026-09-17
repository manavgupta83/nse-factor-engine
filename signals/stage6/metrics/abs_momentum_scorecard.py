"""
abs_momentum_scorecard.py — Absolute Momentum Scorecard Module
===============================================================

Callable module for Stage 6 pipeline.
Exposes: score_momentum(df) -> df with 8 new columns appended.

Metrics (6 total, ROC excluded):
  1. MA_Position      — Weinstein Stage 2 + dist_ema_50
  2. RSI_Strength     — rsi_14 + pct_pos_days
  3. Acceleration     — return horizon acceleration + smoothness + stpb_zscore_21d
  4. Hi52W_Proximity  — proximity_52w_high + bb_pct_b
  5. Trend_Strength   — rm_r2 + residual_momentum + smoothness (vs universe median)
  6. Volume_Confirm   — volume_price_pos_move_confirmed + mfi_14 + vol_ratio_21_252

Output columns appended:
  MA_Position | RSI_Strength | Acceleration | Hi52W_Proximity |
  Trend_Strength | Volume_Confirm | Score | AbsMom_Tier

Tier logic (out of 6):
  6/6 → Tier1_Perfect
  4-5 → Tier2_Strong
  2-3 → Tier3_Moderate
  <2  → Skip
"""

import numpy as np
import pandas as pd

# ── Metric definitions ────────────────────────────────────────────────────────

def _metric_ma(row):
    """MA Position: Weinstein Stage 2 confirmed + price above 50 EMA."""
    stage2      = row.get('weinstein_stage2', np.nan)
    dist_ema50  = row.get('dist_ema_50', np.nan)
    stage2_pass = (stage2 == 1) if pd.notna(stage2) else False
    ema50_pass  = (dist_ema50 > 0) if pd.notna(dist_ema50) else False
    return 'PASS' if (stage2_pass and ema50_pass) else 'FAIL'


def _metric_rsi(row):
    """RSI Strength: rsi_14 > 50 AND pct_pos_days > 0.52 (scale 0-1)."""
    rsi14       = row.get('rsi_14', np.nan)
    pct_pos     = row.get('pct_pos_days', np.nan)
    rsi_pass    = (rsi14 > 50)     if pd.notna(rsi14)   else False
    pct_pass    = (pct_pos > 0.52) if pd.notna(pct_pos) else False
    return 'PASS' if (rsi_pass and pct_pass) else 'FAIL'


def _metric_acceleration(row, smoothness_median):
    """
    Acceleration: at least 1 horizon acceleration test passes
    AND smoothness > 0.5
    AND stpb_zscore_21d > 0.
    """
    r3  = row.get('ret_3m1m',  np.nan)
    r6  = row.get('ret_6m1m',  np.nan)
    r12 = row.get('ret_12m1m', np.nan)

    accel_near = (r3 > r6  / 2) if (pd.notna(r3)  and pd.notna(r6)  and r6  != 0) else False
    accel_mid  = (r6 > r12 / 2) if (pd.notna(r6)  and pd.notna(r12) and r12 != 0) else False

    smooth     = row.get('smoothness', np.nan)
    zscore_21d = row.get('stpb_zscore_21d', np.nan)

    smooth_pass = (smooth > 0.5)   if pd.notna(smooth)     else False
    zscore_pass = (zscore_21d > 0) if pd.notna(zscore_21d) else False

    return 'PASS' if ((accel_near or accel_mid) and smooth_pass and zscore_pass) else 'FAIL'


def _metric_52w(row):
    """52W High Proximity: proximity_52w_high > 0.75 AND bb_pct_b > 0.5."""
    prox    = row.get('proximity_52w_high', np.nan)
    bb_pctb = row.get('bb_pct_b', np.nan)
    prox_pass = (prox    > 0.75) if pd.notna(prox)    else False
    bb_pass   = (bb_pctb > 0.5)  if pd.notna(bb_pctb) else False
    return 'PASS' if (prox_pass and bb_pass) else 'FAIL'


def _metric_trend_strength(row, smoothness_median):
    """
    Trend Strength (ADX proxy):
      rm_r2 > 0.3 AND residual_momentum > 0 AND smoothness > universe median.
    """
    r2     = row.get('rm_r2', np.nan)
    resid  = row.get('residual_momentum', np.nan)
    smooth = row.get('smoothness', np.nan)

    r2_pass     = (r2    > 0.3)                if pd.notna(r2)    else False
    resid_pass  = (resid > 0)                  if pd.notna(resid)  else False
    smooth_pass = (smooth > smoothness_median)  if pd.notna(smooth) else False

    return 'PASS' if (r2_pass and resid_pass and smooth_pass) else 'FAIL'


def _metric_volume(row):
    """
    Volume Confirmation:
      volume_price_pos_move_confirmed == 1 AND mfi_14 > 50 AND vol_ratio_21_252 > 1.0.
    """
    vol_conf = row.get('volume_price_pos_move_confirmed', np.nan)
    mfi      = row.get('mfi_14', np.nan)
    vol_rat  = row.get('vol_ratio_21_252', np.nan)

    conf_pass = (vol_conf == 1) if pd.notna(vol_conf) else False
    mfi_pass  = (mfi > 50)      if pd.notna(mfi)      else False
    vol_pass  = (vol_rat > 1.0) if pd.notna(vol_rat)  else False

    return 'PASS' if (conf_pass and mfi_pass and vol_pass) else 'FAIL'


# ── Tier assignment ───────────────────────────────────────────────────────────

def _assign_tier(score):
    if score == 6:   return 'Tier1_Perfect'
    elif score >= 4: return 'Tier2_Strong'
    elif score >= 2: return 'Tier3_Moderate'
    else:            return 'Skip'


# ── Public API ────────────────────────────────────────────────────────────────

METRIC_COLS = [
    'MA_Position',
    'RSI_Strength',
    'Acceleration',
    'Hi52W_Proximity',
    'Trend_Strength',
    'Volume_Confirm',
]

def score_momentum(df: pd.DataFrame) -> pd.DataFrame:
    """
    Accept a dataframe (ranked/mid recommendations) and append 8 columns:
      MA_Position | RSI_Strength | Acceleration | Hi52W_Proximity |
      Trend_Strength | Volume_Confirm | Score | AbsMom_Tier

    Returns the dataframe with those columns added.
    Missing input columns are handled gracefully (metric returns FAIL).
    """
    if df.empty:
        for col in METRIC_COLS + ['Score', 'AbsMom_Tier']:
            df[col] = pd.NA
        return df

    # Universe-level stat needed by two metrics
    smoothness_median = (
        df['smoothness'].median()
        if 'smoothness' in df.columns and df['smoothness'].notna().any()
        else 0.5   # safe fallback
    )

    df = df.copy()
    df['MA_Position']     = df.apply(_metric_ma,  axis=1)
    df['RSI_Strength']    = df.apply(_metric_rsi, axis=1)
    df['Acceleration']    = df.apply(
        lambda r: _metric_acceleration(r, smoothness_median), axis=1
    )
    df['Hi52W_Proximity'] = df.apply(_metric_52w, axis=1)
    df['Trend_Strength']  = df.apply(
        lambda r: _metric_trend_strength(r, smoothness_median), axis=1
    )
    df['Volume_Confirm']  = df.apply(_metric_volume, axis=1)

    df['Score']       = df[METRIC_COLS].apply(lambda r: (r == 'PASS').sum(), axis=1)
    df['AbsMom_Tier'] = df['Score'].apply(_assign_tier)

    return df
