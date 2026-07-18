"""
Backtest Strategy Engine — Configuration

Single source of truth for gate/score definitions, N, tiebreaker, RF.
"""

# ── Portfolio ─────────────────────────────────────────────────────────────────
N            = 25
RF           = 0.07
TIEBREAKER   = 'proximity_52w_high'
TIEBREAKER_ASCENDING = False

# ── Gate Variants ─────────────────────────────────────────────────────────────
# Precondition for ALL gates: in_universe == True
# Operators: 'eq', 'gt', 'gte', 'lt', 'lte', 'not_in'

GATE_DEFINITIONS = {

    'G2': [
        ('weinstein_stage2', 'eq', True),
    ],

    'G3': [
        ('stpb_ret_21d',       'gt',  -0.05),
        ('proximity_52w_high', 'gte',  0.80),
    ],

    'G4': [
        ('lottery_class', 'not_in', {'LOTTERY', 'BORDER_LOTTERY', 'EXTREME LOTTERY'}),
    ],

    'G5': [
        ('weinstein_stage2',   'eq',     True),
        ('stpb_ret_21d',       'gt',    -0.05),
        ('proximity_52w_high', 'gte',    0.80),
        ('lottery_class',      'not_in', {'LOTTERY', 'BORDER_LOTTERY', 'EXTREME LOTTERY'}),
    ],

    'G6': [
        ('weinstein_stage2',   'eq',     True),
        ('lottery_class',      'not_in', {'LOTTERY', 'BORDER_LOTTERY', 'EXTREME LOTTERY'}),
        ('rs_excess_ret_mkt',  'gt',     0),
    ],

    'G7': [
        ('weinstein_stage2',        'eq',     True),
        ('lottery_class',           'not_in', {'LOTTERY', 'BORDER_LOTTERY', 'EXTREME LOTTERY'}),
        ('rs_excess_ret_mkt',       'gt',     0),
        ('vol_weinstein_asymmetry', 'eq',     True),
    ],
}

# ── Score Variants ────────────────────────────────────────────────────────────

SCORE_DEFINITIONS = {

    'C1': {
        'type'     : 'single',
        'column'   : 'rank_ret_12m1m',
        'ascending': True,
    },

    'C3': {
        'type'     : 'average_ranks',
        'columns'  : ['rank_sharpe_style_momentum', 'rank_sortino_style_momentum'],
        'ascending': True,
    },

    'C6': {
        # (rank_ret_12m1m + rank_rs_excess_ret_mkt) with 1.2x multiplier on incumbents
        # incumbent multiplier applied in engine.py before scoring
        'type'     : 'average_ranks',
        'columns'  : ['rank_ret_12m1m', 'rank_rs_excess_ret_mkt'],
        'ascending': True,
        'incumbent_multiplier': 1.2,   # applied to composite score of held stocks
    },

    'C6RSI': {
        # C6 + RSI as third scoring component
        # rank_rsi_14: ranked ascending=False within in_universe==True (higher RSI = rank 1)
        # Same 1.2x incumbent boost as C6
        'type'     : 'average_ranks',
        'columns'  : ['rank_ret_12m1m', 'rank_rs_excess_ret_mkt', 'rank_rsi_14'],
        'ascending': True,
        'incumbent_multiplier': 1.2,
    },

    'C7': {
        # 0.5 x rank_ret_12m1m + 0.5 x rank_rs_excess_ret_mkt — no hysteresis
        'type'     : 'average_ranks',
        'columns'  : ['rank_ret_12m1m', 'rank_rs_excess_ret_mkt'],
        'ascending': True,
    },
}

# ── Momentum average input columns ────────────────────────────────────────────
MOMENTUM_RANK_COLS = [
    'rank_ret_12m1m',
    'rank_simple_vol_adj_momentum',
    'rank_sharpe_style_momentum',
]

# ── Full grid ─────────────────────────────────────────────────────────────────
GATE_IDS  = ['G2', 'G3', 'G4', 'G5', 'G6', 'G7']
SCORE_IDS = ['C1', 'C3', 'C6', 'C6RSI', 'C7']
CELLS     = [('G2', 'C3'), ('G2', 'C6'), ('G4', 'C3'), ('G4', 'C6'), ('G4', 'C7'), ('G5', 'C1'), ('G6', 'C1'), ('G6', 'C6'), ('G6', 'C6RSI'), ('G6', 'C7'), ('G7', 'C6')]
