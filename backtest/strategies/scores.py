"""
Backtest Strategy Engine — Score Variants

apply_score(score_id, survivors_df, n, tiebreaker, tiebreaker_ascending)
    → top-N DataFrame

All re-ranking done within survivor pool only (post-gate filtered).
Consistent with production testing_shortlist.py approach.
"""

import pandas as pd
import numpy as np
from backtest.strategies.config import (
    SCORE_DEFINITIONS, MOMENTUM_RANK_COLS,
    N, TIEBREAKER, TIEBREAKER_ASCENDING
)


def _rerank(df: pd.DataFrame, col: str, ascending: bool) -> pd.Series:
    """Rank a column within survivor pool. NaN → NaN rank."""
    return df[col].rank(method='min', ascending=ascending)


def apply_score(
    score_id: str,
    survivors: pd.DataFrame,
    n: int = N,
    tiebreaker: str = TIEBREAKER,
    tiebreaker_ascending: bool = TIEBREAKER_ASCENDING,
) -> pd.DataFrame:
    """
    score_id  : 'C1' through 'C5'
    survivors : gate-filtered DataFrame
    n         : number of stocks to select
    tiebreaker: column name for tiebreaker (descending by default)

    Returns   : top-N DataFrame with composite_score and final_rank columns
    """
    assert score_id in SCORE_DEFINITIONS, f"Unknown score_id: {score_id}"

    if len(survivors) == 0:
        print(f"  [{score_id}] WARNING: 0 survivors — returning empty")
        return pd.DataFrame()

    defn = SCORE_DEFINITIONS[score_id]
    df   = survivors.copy()

    # ── C1: single rank — rank_ret_12m1m ─────────────────────────────────────
    if defn['type'] == 'single':
        col = defn['column']
        assert col in df.columns, f"Score column '{col}' not in survivors"
        df['composite_score'] = df[col]
        df = df[df['composite_score'].notna()]

    # ── C3: average of two ranks ──────────────────────────────────────────────
    elif defn['type'] == 'average_ranks':
        cols = defn['columns']
        for c in cols:
            assert c in df.columns, f"Score column '{c}' not in survivors"
        df['composite_score'] = df[cols].mean(axis=1, skipna=False)
        df = df[df['composite_score'].notna()]

    # ── C4/C5: weighted composite ─────────────────────────────────────────────
    elif defn['type'] == 'weighted_composite':

        # compute momentum_avg_rank within survivors
        for c in MOMENTUM_RANK_COLS:
            assert c in df.columns, f"Momentum col '{c}' not in survivors"
        df['momentum_avg_rank'] = df[MOMENTUM_RANK_COLS].mean(axis=1, skipna=False)

        weighted_sum = pd.Series(0.0, index=df.index)
        total_weight = 0.0

        for (col, weight, ascending) in defn['components']:
            assert col in df.columns, f"Component column '{col}' not in survivors"
            reranked = _rerank(df, col, ascending=ascending)
            weighted_sum += weight * reranked
            total_weight += weight

        df['composite_score'] = weighted_sum / total_weight
        df = df[df['composite_score'].notna()]

    else:
        raise ValueError(f"Unknown score type: {defn['type']}")

    if len(df) == 0:
        print(f"  [{score_id}] WARNING: 0 valid composite scores — returning empty")
        return pd.DataFrame()

    # ── Select top-N with tiebreaker ──────────────────────────────────────────
    df = df.sort_values(
        ['composite_score', tiebreaker],
        ascending=[True, tiebreaker_ascending]
    ).reset_index(drop=True)

    df['final_rank'] = range(1, len(df) + 1)
    top_n = df.head(n).copy()

    print(f"  [{score_id}] survivors={len(survivors)} → valid={len(df)} → selected={len(top_n)}")
    return top_n
