"""
Stage 6 (production) — Gate: G6

Selection criteria (ALL must be True):
  1. in_universe == True
  2. weinstein_stage2 == True
  3. lottery_class NOT IN {LOTTERY, BORDER_LOTTERY, EXTREME_LOTTERY}
  4. alpha_12m1m_ew > 0
  5. lower_circuit_hits_63d < 2

Input : Stage 5 output dataframe (momentum_signals_final_{DDMMYYYY}.parquet)
Output: filtered dataframe — only rows passing all 5 conditions

Side effect: writes g6_gate_rejects_{DDMMYYYY}.csv to signals/stage6/
  with one row per in_universe stock that failed, listing all failed conditions.
"""

import pandas as pd
from pathlib import Path
from datetime import datetime
import zoneinfo

EXCLUDED_LOTTERY_CLASSES = ['LOTTERY', 'BORDER_LOTTERY', 'EXTREME_LOTTERY']

BASE = "/home/ec2-user/nse-factor-engine/"
STAGE6_OUTPUT_DIR = Path(BASE + "signals/stage6/")


def apply_g6_gate(signals_df: pd.DataFrame) -> pd.DataFrame:

    # Restrict to in_universe only for gate evaluation
    df = signals_df[signals_df['in_universe'] == True].copy()

    # Evaluate each condition independently
    cond_weinstein  = df['weinstein_stage2'] == True
    cond_lottery    = ~df['lottery_class'].isin(EXCLUDED_LOTTERY_CLASSES)
    cond_alpha      = df['alpha_12m1m_ew'] > 0
    cond_circuit    = df['lower_circuit_hits_63d'] < 2

    passes_all = cond_weinstein & cond_lottery & cond_alpha & cond_circuit

    # ── Build reject log ──
    rejects = df[~passes_all].copy()
    if len(rejects) > 0:
        def get_failed_conditions(row):
            reasons = []
            if not (row['weinstein_stage2'] == True):
                reasons.append('weinstein_stage2=False')
            if row['lottery_class'] in EXCLUDED_LOTTERY_CLASSES:
                reasons.append(f'lottery_class={row["lottery_class"]}')
            if not (row['alpha_12m1m_ew'] > 0):
                reasons.append(f'alpha_12m1m_ew={row["alpha_12m1m_ew"]:.4f}')
            if not (row['lower_circuit_hits_63d'] < 2):
                reasons.append(f'lower_circuit_hits_63d={int(row["lower_circuit_hits_63d"])}')
            return ' | '.join(reasons)

        rejects['failed_conditions'] = rejects.apply(get_failed_conditions, axis=1)

        reject_log = rejects[['symbol', 'lottery_class', 'weinstein_stage2',
                               'alpha_12m1m_ew', 'lower_circuit_hits_63d',
                               'failed_conditions']].copy()
        reject_log = reject_log.sort_values('symbol').reset_index(drop=True)

        run_date_ddmmyyyy = datetime.now(
            zoneinfo.ZoneInfo("Asia/Kolkata")
        ).strftime('%d%m%Y')
        out_path = STAGE6_OUTPUT_DIR / f"g6_gate_rejects_{run_date_ddmmyyyy}.csv"
        reject_log.to_csv(out_path, index=False)
        print(f"G6 gate rejects    : {len(rejects)} stocks -> {out_path}")
    else:
        print("G6 gate rejects    : 0 stocks failed")

    # ── Return passing rows (from original signals_df to preserve all columns) ──
    passing_symbols = set(df[passes_all]['symbol'])
    return signals_df[signals_df['symbol'].isin(passing_symbols)].copy()
