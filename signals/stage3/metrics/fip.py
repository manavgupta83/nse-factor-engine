"""
Stage 3 — Metric: FIP Score, % Positive Days, % Negative Days

Inputs : window (prices T-252 → T-21), signals (stage2, for ret_12m1m)
Returns: dataframe with columns [symbol, fip_score, pct_pos_days, pct_neg_days]
"""

import pandas as pd
import numpy as np


def compute(window: pd.DataFrame, signals: pd.DataFrame) -> pd.DataFrame:
    log_rets = window.copy()
    log_rets['log_ret'] = log_rets.groupby('symbol')['close'].transform(
        lambda x: np.log(x / x.shift(1))
    )
    log_rets = log_rets.dropna(subset=['log_ret'])

    def fip_components(group):
        total = len(group)
        if total == 0:
            return pd.Series({'pct_pos_days': np.nan, 'pct_neg_days': np.nan})
        pos = (group['log_ret'] > 0).sum()
        neg = (group['log_ret'] < 0).sum()
        return pd.Series({
            'pct_pos_days': pos / total,
            'pct_neg_days': neg / total,
        })

    fip_df = log_rets.groupby('symbol', group_keys=False).apply(
        fip_components, include_groups=False
    ).reset_index()

    fip_df = fip_df.merge(signals[['symbol', 'ret_12m1m']], on='symbol', how='left')
    fip_df['fip_score'] = (
        np.sign(fip_df['ret_12m1m']) *
        (fip_df['pct_neg_days'] - fip_df['pct_pos_days'])
    )
    return fip_df[['symbol', 'fip_score', 'pct_pos_days', 'pct_neg_days']]
