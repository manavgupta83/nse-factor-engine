"""
Stage 7 — Momentum Ratio Scoring

Formula (per spec):
  MR12 = ret_12m1m / vol_252
  MR6  = ret_6m1m  / vol_252

  Z12  = (MR12 - mean(MR12)) / std(MR12)   cross-sectional over scoring universe
  Z6   = (MR6  - mean(MR6))  / std(MR6)    cross-sectional over scoring universe

  Weighted_Z = 0.5 * Z12 + 0.5 * Z6

  Normalized_Momentum_Score =
      1 + Weighted_Z           if Weighted_Z >= 0
      1 / (1 - Weighted_Z)     if Weighted_Z <  0

USE_G6_GATE flag:
  0 (default) — score all in_universe stocks, no gate filtering
  1           — apply G6 gate before scoring (weinstein_stage2,
                lottery_class, alpha_12m1m_ew > 0). Flip when needed.

Vol note:
  Uses vol_252 (T-252 -> T, full 1-year window, no skip-month exclusion).
  vol_231 (skip-month) is used by sharpe_style/simple_vol_adj — not here.

Input : Stage 5 signals dataframe
Output: full ranked dataframe (ALL in-universe stocks that have valid inputs),
        with columns: mr_12, mr_6, z_12, z_6, weighted_z,
                      norm_momentum_score, mr_rank
        No tier assignment here — that is done in mr_reconstitute.py
        after applying the buffer zone + reconstitution logic.
"""

import numpy as np
import pandas as pd

USE_G6_GATE = 1   # 0 = all in_universe; 1 = apply G6 gate before scoring

G6_EXCLUDED_LOTTERY_CLASSES = ['LOTTERY', 'BORDER_LOTTERY', 'EXTREME_LOTTERY']


def _apply_g6_gate(df: pd.DataFrame) -> pd.DataFrame:
    mask = (
        (df['weinstein_stage2'] == True) &
        (~df['lottery_class'].isin(G6_EXCLUDED_LOTTERY_CLASSES)) &
        (df['alpha_12m1m_ew'] > 0)
    )
    out = df[mask].copy()
    print(f"G6 gate applied: {len(df)} -> {len(out)} pass "
          f"({len(df) - len(out)} dropped)")
    return out


def apply_mr_score(signals_df: pd.DataFrame) -> pd.DataFrame:
    required = ['symbol', 'in_universe', 'ret_12m1m', 'ret_6m1m', 'vol_252']
    missing = [c for c in required if c not in signals_df.columns]
    assert not missing, f"mr_score: missing required columns: {missing}"

    # Step 1: restrict to in_universe always
    df = signals_df[signals_df['in_universe'] == True].copy()
    print(f"in_universe stocks: {len(df)}")

    # Step 2: optionally apply G6 gate
    if USE_G6_GATE == 1:
        print("USE_G6_GATE=1 — applying G6 gate before scoring")
        df = _apply_g6_gate(df)
    else:
        print("USE_G6_GATE=0 — scoring all in_universe stocks, no gate")

    # Step 3: drop rows with missing inputs
    n_before = len(df)
    bad = (
        df['vol_252'].isna() | (df['vol_252'] == 0) |
        df['ret_12m1m'].isna() |
        df['ret_6m1m'].isna()
    )
    if bad.sum() > 0:
        print(f"WARNING: dropping {bad.sum()} rows — NaN/zero in vol_252, "
              f"ret_12m1m, or ret_6m1m: "
              f"{sorted(df.loc[bad, 'symbol'].tolist())}")
    df = df[~bad].copy()
    print(f"Scoring universe: {len(df)} symbols "
          f"({n_before - len(df)} dropped for NaN/zero)")

    assert len(df) >= 10, \
        f"Too few symbols ({len(df)}) for meaningful cross-sectional Z scores"

    # Step 4: Momentum Ratios
    df['mr_12'] = df['ret_12m1m'] / df['vol_252']
    df['mr_6']  = df['ret_6m1m']  / df['vol_252']

    # Step 5: Cross-sectional Z scores
    df['z_12'] = (df['mr_12'] - df['mr_12'].mean()) / df['mr_12'].std(ddof=1)
    df['z_6']  = (df['mr_6']  - df['mr_6'].mean())  / df['mr_6'].std(ddof=1)

    # Step 6: Weighted Z
    df['weighted_z'] = 0.5 * df['z_12'] + 0.5 * df['z_6']

    # Step 7: Normalized Momentum Score
    def norm_score(wz):
        return 1 + wz if wz >= 0 else 1.0 / (1.0 - wz)

    df['norm_momentum_score'] = df['weighted_z'].apply(norm_score)

    # Step 8: Rank descending — rank 1 = highest score = best
    df['mr_rank'] = (
        df['norm_momentum_score']
        .rank(method='min', ascending=False)
        .astype('Int64')
    )

    df = df.sort_values('mr_rank').reset_index(drop=True)

    print(f"\nScoring complete:")
    print(f"  Symbols scored       : {len(df)}")
    print(f"  norm_score range     : "
          f"{df['norm_momentum_score'].min():.4f} — "
          f"{df['norm_momentum_score'].max():.4f}")
    print(f"  weighted_z range     : "
          f"{df['weighted_z'].min():.4f} — "
          f"{df['weighted_z'].max():.4f}")

    return df
