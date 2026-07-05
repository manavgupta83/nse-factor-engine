"""
Stage 6 (production) — Gate: G6

Selection criteria (ALL must be True):
  1. in_universe == True
  2. weinstein_stage2 == True
  3. lottery_class NOT IN {LOTTERY, BORDER_LOTTERY, EXTREME_LOTTERY}
  4. rs_excess_ret_mkt > 0

Input : Stage 5 output dataframe (momentum_signals_final_{DDMMYYYY}.parquet)
Output: filtered dataframe — only rows passing all 4 conditions
"""

import pandas as pd

EXCLUDED_LOTTERY_CLASSES = ['LOTTERY', 'BORDER_LOTTERY', 'EXTREME_LOTTERY']


def apply_g6_gate(signals_df: pd.DataFrame) -> pd.DataFrame:
    return signals_df[
        (signals_df['in_universe'] == True) &
        (signals_df['weinstein_stage2'] == True) &
        (~signals_df['lottery_class'].isin(EXCLUDED_LOTTERY_CLASSES)) &
        (signals_df['rs_excess_ret_mkt'] > 0)
    ].copy()
