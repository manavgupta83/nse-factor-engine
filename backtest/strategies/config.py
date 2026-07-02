"""
Backtest Strategy Engine — Configuration

All locked design decisions from handover doc.
Single source of truth for gate/score definitions, N, tiebreaker, RF.
"""

# ── Portfolio ─────────────────────────────────────────────────────────────────
N            = 25       # stocks per cell
RF           = 0.07     # risk-free rate (annualised)
TIEBREAKER   = 'proximity_52w_high'   # descending — config param, not hardcoded
TIEBREAKER_ASCENDING = False

# ── Gate Variants ─────────────────────────────────────────────────────────────
# Precondition for ALL gates: in_universe == True
# Each gate definition is a list of (column, operator, value) tuples
# Operators: 'eq', 'gt', 'gte', 'lt', 'lte', 'not_in'

GATE_DEFINITIONS = {
    'G1': [],   # in_universe only — no additional gates

    'G2': [
        ('weinstein_stage2', 'eq', True),
    ],

    'G3': [
        ('stpb_ret_21d',      'gt',  -0.05),
        ('proximity_52w_high','gte',  0.80),
    ],

    'G4': [
        ('lottery_class', 'not_in', {'LOTTERY', 'BORDER_LOTTERY', 'EXTREME LOTTERY'}),
    ],

    'G5': [
        ('weinstein_stage2',  'eq',    True),
        ('stpb_ret_21d',      'gt',   -0.05),
        ('proximity_52w_high','gte',   0.80),
        ('lottery_class',     'not_in',{'LOTTERY', 'BORDER_LOTTERY', 'EXTREME LOTTERY'}),
    ],
}

# ── Score Variants ────────────────────────────────────────────────────────────
# Defines which columns feed each score variant.
# Weights applied within survivor pool after re-ranking.

SCORE_DEFINITIONS = {
    'C1': {
        'type'   : 'single',
        'column' : 'rank_ret_12m1m',
        'ascending': True,   # rank 1 = best
    },

    'C2': {
        'type'   : 'single',
        'column' : 'rank_fip_ret_12m1m',
        'ascending': True,   # rank 1 = best, NaN outside top-100 pool
    },

    'C3': {
        'type'   : 'average_ranks',
        'columns': ['rank_sharpe_style_momentum', 'rank_sortino_style_momentum'],
        'ascending': True,
    },

    'C4': {
        'type'   : 'weighted_composite',
        'components': [
            # (column, weight, re_rank_ascending)
            # re_rank_ascending=True  → lower raw value = better rank
            # re_rank_ascending=False → higher raw value = better rank
            ('momentum_avg_rank',  0.20, True),   # avg of 3 momentum ranks — re-ranked
            ('rank_fip_ret_12m1m', 0.20, True),   # FIP rank — already ranked
            ('rs_rank_500',        0.20, False),  # RS percentile — re-ranked descending
            ('industry_rank',      0.20, False),  # industry percentile — re-ranked descending
            ('proximity_52w_high', 0.20, False),  # proximity raw — re-ranked descending
        ],
    },

    'C5': {
        'type'   : 'weighted_composite',
        'components': [
            ('momentum_avg_rank',  0.30, True),
            ('rank_fip_ret_12m1m', 0.20, True),
            ('rs_rank_500',        0.20, False),
            ('industry_rank',      0.15, False),
            ('proximity_52w_high', 0.15, False),
        ],
    },
}

# ── Momentum average input columns (used for C4/C5 momentum_avg_rank) ────────
MOMENTUM_RANK_COLS = [
    'rank_ret_12m1m',
    'rank_simple_vol_adj_momentum',
    'rank_sharpe_style_momentum',
]

# ── Full grid ─────────────────────────────────────────────────────────────────
GATE_IDS  = ['G1', 'G2', 'G3', 'G4', 'G5']
SCORE_IDS = ['C1', 'C2', 'C3', 'C4', 'C5']
CELLS     = [(g, c) for g in GATE_IDS for c in SCORE_IDS]  # 25 cells
