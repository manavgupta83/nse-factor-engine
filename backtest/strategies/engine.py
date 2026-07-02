"""
Backtest Strategy Engine — Master Orchestrator

get_portfolio(gate_id, score_id, signals_df) → top-25 DataFrame
run_all_cells(signals_df)                    → long-format DataFrame (25 cells × 25 stocks)
"""

import pandas as pd
from backtest.strategies.gates  import apply_gate
from backtest.strategies.scores import apply_score
from backtest.strategies.config import CELLS, N, TIEBREAKER, TIEBREAKER_ASCENDING

# columns carried through to output for audit trail
AUDIT_COLS = [
    'symbol', 'as_of_date',
    'ret_12m1m', 'fip_score', 'proximity_52w_high',
    'weinstein_stage2', 'lottery_class', 'in_universe',
    'rank_ret_12m1m', 'rank_fip_ret_12m1m',
    'industry_rank', 'rs_rank_500',
    'adtv_63_cr',
]


def get_portfolio(
    gate_id:  str,
    score_id: str,
    signals:  pd.DataFrame,
    n:        int = N,
    verbose:  bool = False,
) -> pd.DataFrame:
    """
    gate_id  : 'G1'–'G5'
    score_id : 'C1'–'C5'
    signals  : signals DataFrame for one Friday

    Returns  : top-N DataFrame with cell metadata attached
    """
    if not verbose:
        import io, contextlib
        f = io.StringIO()
        with contextlib.redirect_stdout(f):
            survivors = apply_gate(gate_id, signals)
            top_n     = apply_score(score_id, survivors, n=n)
    else:
        survivors = apply_gate(gate_id, signals)
        top_n     = apply_score(score_id, survivors, n=n)

    if top_n.empty:
        return pd.DataFrame()

    # attach cell metadata
    top_n['gate_id']  = gate_id
    top_n['score_id'] = score_id
    top_n['cell_id']  = f'{gate_id}_{score_id}'

    # keep audit cols that exist
    keep = ['gate_id', 'score_id', 'cell_id', 'final_rank', 'composite_score'] + \
           [c for c in AUDIT_COLS if c in top_n.columns]
    keep = list(dict.fromkeys(keep))  # deduplicate, preserve order

    return top_n[keep].reset_index(drop=True)


def run_all_cells(
    signals: pd.DataFrame,
    n:       int = N,
    verbose: bool = False,
) -> pd.DataFrame:
    """
    Runs all 25 cells (G1-G5 × C1-C5) on a single Friday signals DataFrame.

    Returns long-format DataFrame:
        25 cells × up to 25 stocks = up to 625 rows
        Columns: cell_id, gate_id, score_id, final_rank, symbol, as_of_date, ...audit cols
    """
    results = []

    for (gate_id, score_id) in CELLS:
        df = get_portfolio(gate_id, score_id, signals, n=n, verbose=verbose)
        if not df.empty:
            results.append(df)
        else:
            print(f'WARNING: {gate_id}_{score_id} returned empty portfolio')

    if not results:
        print('ERROR: all cells returned empty')
        return pd.DataFrame()

    combined = pd.concat(results, ignore_index=True)
    return combined
