"""
Stage 6 (production) — Score: C6

c6_raw = rank_ret_12m1m + rank_rs_excess_ret_mkt
Incumbent boost: c6_score = c6_raw / 1.2 (lower = better, harder to displace)
NaN in rank_ret_12m1m or rank_rs_excess_ret_mkt -> row dropped before scoring

Output: top min(50, pool_size) rows by c6_score ascending, tiebreak proximity_52w_high desc
tier column: 'TOP_25' for rank <=25, 'REST' for rank 26-50
If pool_size < 25, ALL rows become TOP_25 (no REST) -- degrades correctly, no special-casing.
Actual portfolio/SELL/incumbent decisions use TOP_25 only -- REST is for analysis.

g6_pool_size column: raw count of rows passing G6 gate (pre-NaN-drop), passed in by caller.

Input : filtered_df (post-G6-gate), current_holdings (set of symbols, or None),
        pool_size (int, raw G6 gate pass count, pre-NaN-drop)
Output: ranked dataframe, up to 50 rows, with tier flag + g6_pool_size column
"""

import pandas as pd

PORTFOLIO_N = 25
DISPLAY_N = 50


def apply_c6_score(filtered_df: pd.DataFrame, current_holdings=None, N=PORTFOLIO_N, pool_size=None) -> pd.DataFrame:
    if current_holdings is None:
        current_holdings = set()

    if pool_size is None:
        pool_size = len(filtered_df)

    df = filtered_df.copy()

    df = df.dropna(subset=['rank_ret_12m1m', 'rank_rs_excess_ret_mkt']).copy()

    df['c6_raw'] = df['rank_ret_12m1m'] + df['rank_rs_excess_ret_mkt']

    df['incumbent_boost_applied'] = df['symbol'].isin(current_holdings)
    df['c6_score'] = df.apply(
        lambda row: row['c6_raw'] / 1.2 if row['incumbent_boost_applied'] else row['c6_raw'],
        axis=1
    )

    ranked = (
        df.sort_values(['c6_score', 'proximity_52w_high'], ascending=[True, False])
        .head(DISPLAY_N)
        .reset_index(drop=True)
    )

    ranked['tier'] = ['TOP_25' if i < N else 'REST' for i in range(len(ranked))]
    ranked['g6_pool_size'] = pool_size

    return ranked
