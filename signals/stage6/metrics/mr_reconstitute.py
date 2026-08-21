"""
Stage 6 — Hybrid Buffer Zone Reconstitution

Weinstein applied in reconstitution, NOT as a pre-scoring gate.

Rules:
  PORTFOLIO_N  = 25   (target portfolio size)
  BUFFER_ZONE  = 38   (retention threshold)
  FORCED_IN_N  = 12   (top N non-holders always enter, bypass Weinstein)

Hybrid Weinstein logic:
  - Forced-in (rank 1-FORCED_IN_N non-holders): enter regardless of Weinstein
  - Retained holdings (rank <= BUFFER_ZONE): MUST pass weinstein_stage2 == True
    else treated as forced_out
  - Fill slots (rank 13-38 non-holders): MUST pass weinstein_stage2 == True
  - Emergency fill: if Weinstein filter leaves portfolio short, fill without Weinstein

Weinstein reject tracker:
  Tracks two categories:
  1. Holdings forced out due to Weinstein failure (not just rank)
  2. Non-holders ranked 13-BUFFER_ZONE that failed Weinstein in fill pool
     (stocks that WOULD have entered but trend reversal blocked them)

Returns:
  output_df        — dataframe with tier and action columns
  top25_symbols    — set of symbols in final TOP_25
  weinstein_rejects — dataframe of Weinstein-rejected stocks with rejection_reason
"""

import pandas as pd

PORTFOLIO_N  = 25
BUFFER_ZONE  = 38
FORCED_IN_N  = 12


def apply_reconstitution(
    ranked_df: pd.DataFrame,
    current_holdings: set
) -> tuple:

    df = ranked_df.copy()
    all_symbols = set(df['symbol'])

    wein_lookup = dict(zip(df['symbol'], df['weinstein_stage2'])) \
                  if 'weinstein_stage2' in df.columns else {}

    unscored = current_holdings - all_symbols
    if unscored:
        print(f"WARNING: {len(unscored)} current holdings not in scored universe: "
              f"{sorted(unscored)}")
    scoreable_holdings = current_holdings & all_symbols

    is_first_run = len(current_holdings) == 0

    # ── Step 1: Split holdings by rank and Weinstein ──
    holdings_df = df[df['symbol'].isin(scoreable_holdings)].copy()

    retained = set(
        holdings_df[
            (holdings_df['mr_rank'] <= BUFFER_ZONE) &
            (holdings_df['weinstein_stage2'] == True)
        ]['symbol']
    )
    # Holdings forced out by rank
    rank_forced_out = set(
        holdings_df[holdings_df['mr_rank'] > BUFFER_ZONE]['symbol']
    )
    # Holdings forced out by Weinstein failure (rank was fine)
    weinstein_forced_out_holdings = set(
        holdings_df[
            (holdings_df['mr_rank'] <= BUFFER_ZONE) &
            (holdings_df['weinstein_stage2'] != True)
        ]['symbol']
    )
    forced_out = rank_forced_out | weinstein_forced_out_holdings | unscored

    print(f"\nReconstitution inputs:")
    print(f"  Current holdings     : {len(current_holdings)}"
          f"{' (first run — empty)' if is_first_run else ''}")
    print(f"  Scoreable holdings   : {len(scoreable_holdings)}")
    print(f"  Retained (rank<={BUFFER_ZONE}, W=True)  : "
          f"{len(retained)}  {sorted(retained)}")
    print(f"  Forced out (rank)    : {len(rank_forced_out)} {sorted(rank_forced_out)}")
    print(f"  Forced out (Weinstein): {len(weinstein_forced_out_holdings)} "
          f"{sorted(weinstein_forced_out_holdings)}")

    # ── Step 2: Forced-in — top FORCED_IN_N non-holders, bypass Weinstein ──
    non_holders_df = df[~df['symbol'].isin(scoreable_holdings)].copy()
    forced_in = set(
        non_holders_df.sort_values('mr_rank')
        .head(FORCED_IN_N)['symbol']
    )
    print(f"  Forced in (top {FORCED_IN_N}, W bypassed): "
          f"{len(forced_in)}  {sorted(forced_in)}")

    # ── Step 3: Edge case ──
    combined = retained | forced_in
    if len(combined) > PORTFOLIO_N:
        excess = len(combined) - PORTFOLIO_N
        retained_ranked = (
            df[df['symbol'].isin(retained)]
            .sort_values('mr_rank', ascending=False)
        )
        drop_symbols = set(retained_ranked.head(excess)['symbol'])
        print(f"  Edge case: dropping {excess} lowest-ranked retained: "
              f"{sorted(drop_symbols)}")
        retained   -= drop_symbols
        forced_out |= drop_symbols

    # ── Step 4: Fill slots — must pass Weinstein ──
    slots_filled    = retained | forced_in
    slots_remaining = PORTFOLIO_N - len(slots_filled)
    print(f"  Slots after forced_in + retained: {len(slots_filled)}, "
          f"remaining to fill: {slots_remaining}")

    # Candidates: non-holders, not already placed, rank <= BUFFER_ZONE
    fill_candidates = df[
        ~df['symbol'].isin(slots_filled) &
        ~df['symbol'].isin(scoreable_holdings) &
        (df['mr_rank'] <= BUFFER_ZONE)
    ].sort_values('mr_rank').copy()

    # Track Weinstein rejects in fill pool (rank 13-BUFFER_ZONE, fail Weinstein)
    weinstein_fill_rejects = fill_candidates[
        fill_candidates['weinstein_stage2'] != True
    ][['symbol', 'mr_rank', 'norm_momentum_score',
       'weighted_z', 'ret_12m1m', 'weinstein_stage2']].copy()

    # Actual fill: must pass Weinstein
    fill_pool = fill_candidates[
        fill_candidates['weinstein_stage2'] == True
    ]
    fill_symbols = set(fill_pool.head(slots_remaining)['symbol'])

    # Emergency fill if short
    still_needed = slots_remaining - len(fill_symbols)
    if still_needed > 0:
        print(f"  WARNING: Weinstein fill short by {still_needed} — "
              f"emergency fill without Weinstein")
        emergency_pool = df[
            ~df['symbol'].isin(slots_filled | fill_symbols) &
            ~df['symbol'].isin(scoreable_holdings)
        ].sort_values('mr_rank')
        emergency = set(emergency_pool.head(still_needed)['symbol'])
        fill_symbols |= emergency

    print(f"  Fill symbols (W=True): {len(fill_symbols)}  {sorted(fill_symbols)}")
    print(f"  Weinstein fill rejects: {len(weinstein_fill_rejects)}")

    # ── Step 5: Final TOP_25 ──
    top25_symbols = retained | forced_in | fill_symbols
    assert len(top25_symbols) == PORTFOLIO_N, \
        (f"TOP_25 assembly error: got {len(top25_symbols)}, "
         f"expected {PORTFOLIO_N}.")

    # ── Step 6: Assign actions ──
    def get_action(row):
        sym = row['symbol']
        in_top25   = sym in top25_symbols
        was_holder = sym in current_holdings
        if in_top25:
            return 'HOLD' if was_holder else 'BUY'
        if was_holder:
            return 'SELL'
        if int(row['mr_rank']) <= BUFFER_ZONE:
            return 'WATCHLIST'
        return None

    df['action'] = df.apply(get_action, axis=1)

    # ── Step 7: Tier ──
    def get_tier(row):
        sym = row['symbol']
        if sym in top25_symbols:
            return 'TOP_25'
        if sym in (forced_out | unscored):
            return 'SELL'
        if row['action'] == 'WATCHLIST':
            return 'WATCHLIST'
        return None

    df['tier'] = df.apply(get_tier, axis=1)

    # ── Step 8: Build output ──
    output_df = df[df['action'].notna()].copy()

    if unscored:
        unscored_rows = pd.DataFrame({
            'symbol': sorted(unscored),
            'tier':   'SELL',
            'action': 'SELL',
        })
        output_df = pd.concat([output_df, unscored_rows], ignore_index=True)

    print(f"\nReconstitution output:")
    print(f"  TOP_25  : {(output_df['tier'] == 'TOP_25').sum()}")
    print(f"    BUY   : {(output_df['action'] == 'BUY').sum()}")
    print(f"    HOLD  : {(output_df['action'] == 'HOLD').sum()}")
    print(f"  SELL    : {(output_df['action'] == 'SELL').sum()}")
    print(f"  WATCHLIST:{(output_df['action'] == 'WATCHLIST').sum()}")

    # ── Step 9: Build Weinstein reject log ──
    weinstein_rejects = pd.DataFrame()

    # Category 1: holdings forced out by Weinstein
    if weinstein_forced_out_holdings:
        holding_rejects = df[
            df['symbol'].isin(weinstein_forced_out_holdings)
        ][['symbol', 'mr_rank', 'norm_momentum_score',
           'weighted_z', 'ret_12m1m', 'weinstein_stage2']].copy()
        holding_rejects['rejection_stage']  = 'RECONSTITUTION_HOLDING'
        holding_rejects['rejection_reason'] = (
            'Current holding: rank within buffer but weinstein_stage2=False '
            '(trend reversal — forced out)'
        )
        weinstein_rejects = pd.concat(
            [weinstein_rejects, holding_rejects], ignore_index=True
        )

    # Category 2: fill pool rejects (rank 13-BUFFER_ZONE, fail Weinstein)
    if len(weinstein_fill_rejects) > 0:
        weinstein_fill_rejects['rejection_stage']  = 'RECONSTITUTION_FILL'
        weinstein_fill_rejects['rejection_reason'] = (
            'Non-holder: ranked within fill zone but weinstein_stage2=False '
            '(trend reversal — blocked from entry)'
        )
        weinstein_rejects = pd.concat(
            [weinstein_rejects, weinstein_fill_rejects], ignore_index=True
        )

    if len(weinstein_rejects) > 0:
        weinstein_rejects = weinstein_rejects.sort_values(
            ['rejection_stage', 'mr_rank']
        ).reset_index(drop=True)

    return output_df, top25_symbols, weinstein_rejects
