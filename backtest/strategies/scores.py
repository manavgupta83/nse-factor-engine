"""
Backtest Strategy Engine — Score Variants

apply_score(score_id, survivors_df, n, tiebreaker, tiebreaker_ascending, incumbent_symbols)
    → top-N DataFrame

C6:    average of (rank_ret_12m1m, rank_rs_excess_ret_mkt) with 1.2x multiplier on incumbents.
C6RSI: average of (rank_ret_12m1m, rank_rs_excess_ret_mkt, rank_rsi_14) with 1.2x multiplier.
       rank_rsi_14 ranked ascending=False (higher RSI = rank 1) within in_universe==True.

Multiplier applied to composite score before ranking — incumbents get score boosted,
lowering their effective rank number, making them harder to displace.
Week 1: no incumbents → no multiplier.
"""

import pandas as pd
import numpy as np
from backtest.strategies.config import (
    SCORE_DEFINITIONS, MOMENTUM_RANK_COLS,
    N, TIEBREAKER, TIEBREAKER_ASCENDING
)


def _rerank(df: pd.DataFrame, col: str, ascending: bool) -> pd.Series:
    return df[col].rank(method='min', ascending=ascending)


def apply_score(
    score_id:          str,
    survivors:         pd.DataFrame,
    n:                 int = N,
    tiebreaker:        str = TIEBREAKER,
    tiebreaker_ascending: bool = TIEBREAKER_ASCENDING,
    incumbent_symbols: set = None,
) -> pd.DataFrame:
    """
    score_id          : 'C1', 'C3', 'C6', 'C6RSI', 'C7'
    survivors         : gate-filtered DataFrame
    incumbent_symbols : set of symbols held from prior week (C6/C6RSI only)
    """
    assert score_id in SCORE_DEFINITIONS, f"Unknown score_id: {score_id}"

    if incumbent_symbols is None:
        incumbent_symbols = set()

    if len(survivors) == 0:
        print(f'  [{score_id}] WARNING: 0 survivors — returning empty')
        return pd.DataFrame()

    defn = SCORE_DEFINITIONS[score_id]
    df   = survivors.copy()

    # ── C1: single rank ───────────────────────────────────────────────────────
    if defn['type'] == 'single':
        col = defn['column']
        assert col in df.columns, f"Score column '{col}' not in survivors"
        df['composite_score'] = df[col]
        df = df[df['composite_score'].notna()]

    # ── C3, C6, C6RSI, C7: average of ranks ──────────────────────────────────
    elif defn['type'] == 'average_ranks':
        cols = defn['columns']
        for c in cols:
            assert c in df.columns, f"Score column '{c}' not in survivors"
        df['composite_score'] = df[cols].mean(axis=1, skipna=False)
        df = df[df['composite_score'].notna()]

        # incumbent multiplier — divide composite score (lower = better rank)
        # dividing by 1.2 makes incumbents appear to have better (lower) score
        if defn.get('incumbent_multiplier') and len(incumbent_symbols) > 0:
            multiplier = defn['incumbent_multiplier']
            is_incumbent = df['symbol'].isin(incumbent_symbols)
            df.loc[is_incumbent, 'composite_score'] = \
                df.loc[is_incumbent, 'composite_score'] / multiplier
            n_incumbents = is_incumbent.sum()
            print(f'  [{score_id}] incumbent multiplier {multiplier}x applied to {n_incumbents} stocks')

    # ── weighted composite (future use) ──────────────────────────────────────
    elif defn['type'] == 'weighted_composite':
        df['momentum_avg_rank'] = df[MOMENTUM_RANK_COLS].mean(axis=1, skipna=False)
        weighted_sum = pd.Series(0.0, index=df.index)
        total_weight = 0.0
        for (col, weight, ascending) in defn['components']:
            assert col in df.columns
            weighted_sum += weight * _rerank(df, col, ascending=ascending)
            total_weight += weight
        df['composite_score'] = weighted_sum / total_weight
        df = df[df['composite_score'].notna()]

        if defn.get('incumbent_multiplier') and len(incumbent_symbols) > 0:
            multiplier = defn['incumbent_multiplier']
            is_incumbent = df['symbol'].isin(incumbent_symbols)
            df.loc[is_incumbent, 'composite_score'] = df.loc[is_incumbent, 'composite_score'] / multiplier
            print(f'  [{score_id}] incumbent multiplier {multiplier}x applied to {is_incumbent.sum()} stocks')

    else:
        raise ValueError(f"Unknown score type: {defn['type']}")

    if len(df) == 0:
        print(f'  [{score_id}] WARNING: 0 valid scores — returning empty')
        return pd.DataFrame()

    # ── Select top-N with tiebreaker ──────────────────────────────────────────
    df = df.sort_values(
        ['composite_score', tiebreaker],
        ascending=[True, tiebreaker_ascending]
    ).reset_index(drop=True)

    df['final_rank'] = range(1, len(df) + 1)
    top_n = df.head(n).copy()

    print(f'  [{score_id}] survivors={len(survivors)} → valid={len(df)} → selected={len(top_n)}')
    return top_n
