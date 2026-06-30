"""
Stage 3 — Metric: Momentum Smoothness

Inputs : window (prices T-252 → T-21)
Returns: dataframe with columns [symbol, smoothness]
"""

import pandas as pd
import numpy as np


def compute(window: pd.DataFrame) -> pd.DataFrame:
    def compute_smoothness(group):
        group = group.reset_index(drop=True)
        n = len(group)
        complete_weeks = n // 5
        if complete_weeks == 0:
            return pd.Series({'smoothness': np.nan})
        pos_weeks = 0
        for i in range(complete_weeks):
            start = i * 5
            end   = start + 4
            if group.loc[end, 'close'] > group.loc[start, 'open']:
                pos_weeks += 1
        return pd.Series({'smoothness': pos_weeks / complete_weeks})

    return window.groupby('symbol', group_keys=False).apply(
        compute_smoothness, include_groups=False
    ).reset_index()
