"""
Backtest Strategy Engine — Gate Variants

apply_gate(gate_id, signals_df) → filtered DataFrame

Precondition for ALL gates: in_universe == True
Additional filters stacked per gate definition in config.py.
"""

import pandas as pd
from backtest.strategies.config import GATE_DEFINITIONS


def apply_gate(gate_id: str, signals: pd.DataFrame) -> pd.DataFrame:
    """
    gate_id  : 'G1' through 'G5'
    signals  : full signals DataFrame for one Friday

    Returns  : filtered DataFrame — in_universe=True + gate conditions
    """
    assert gate_id in GATE_DEFINITIONS, f"Unknown gate_id: {gate_id}"

    # precondition — all gates
    df = signals[signals['in_universe'] == True].copy()
    n_start = len(df)

    # additional gate conditions
    for (col, op, val) in GATE_DEFINITIONS[gate_id]:
        assert col in df.columns, f"Gate column '{col}' not in signals"
        before = len(df)

        if op == 'eq':
            df = df[df[col] == val]
        elif op == 'gt':
            df = df[df[col] > val]
        elif op == 'gte':
            df = df[df[col] >= val]
        elif op == 'lt':
            df = df[df[col] < val]
        elif op == 'lte':
            df = df[df[col] <= val]
        elif op == 'not_in':
            df = df[~df[col].isin(val)]
        else:
            raise ValueError(f"Unknown operator: {op}")

        after = len(df)
        print(f"  [{gate_id}] {col} {op} {val}: {before} → {after}")

    print(f"  [{gate_id}] survivors: {n_start} → {len(df)}")
    return df.reset_index(drop=True)
