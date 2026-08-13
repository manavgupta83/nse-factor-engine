"""
Stage 7 — Buffer Zone Reconstitution

Rules:
  PORTFOLIO_N  = 25   (target portfolio size)
  BUFFER_ZONE  = 38   (retention threshold)
  FORCED_IN_N  = 12   (top N non-holders always enter)

Assembly logic (in order):
  1. forced_out  : current holdings with mr_rank > BUFFER_ZONE  → always exit
  2. retained    : current holdings with mr_rank <= BUFFER_ZONE → candidate for retention
  3. forced_in   : top FORCED_IN_N non-holders by mr_rank       → always enter
  4. Edge case   : if len(retained) + len(forced_in) > PORTFOLIO_N,
                   drop lowest-ranked retained until total == PORTFOLIO_N
  5. Fill        : remaining slots filled by highest-ranked non-holders
                   not already in forced_in

Actions assigned:
  BUY       : in final TOP_25, was NOT in current_holdings
  HOLD      : in final TOP_25, was in current_holdings
  SELL      : was in current_holdings, NOT in final TOP_25
  WATCHLIST : rank <= BUFFER_ZONE, not a current holder, not in TOP_25

Input:
  ranked_df        — full scored df from apply_mr_score (all in-universe stocks)
  current_holdings — set of symbols currently in portfolio (empty set = first run)

Output:
  final_df — dataframe with tier and action columns, containing:
               TOP_25 rows + SELL rows + WATCHLIST rows
"""

import pandas as pd

PORTFOLIO_N   = 25
BUFFER_ZONE   = 38
FORCED_IN_N   = 12


def apply_reconstitution(
    ranked_df: pd.DataFrame,
    current_holdings: set
) -> pd.DataFrame:

    df = ranked_df.copy()
    all_symbols = set(df['symbol'])

    # ── Validate current_holdings against scored universe ──
    unscored = current_holdings - all_symbols
    if unscored:
        print(f"WARNING: {len(unscored)} current holdings not in scored universe "
              f"(NaN inputs, dropped from scoring): {sorted(unscored)}")
        print(f"  These will be treated as forced_out (cannot retain unscored stocks)")
    # Only holdings that appear in scored universe can be retained
    scoreable_holdings = current_holdings & all_symbols

    is_first_run = len(current_holdings) == 0

    # ── Step 1: Split current holdings ──
    holdings_df = df[df['symbol'].isin(scoreable_holdings)].copy()
    forced_out  = set(holdings_df[holdings_df['mr_rank'] > BUFFER_ZONE]['symbol'])
    retained    = set(holdings_df[holdings_df['mr_rank'] <= BUFFER_ZONE]['symbol'])

    print(f"\nReconstitution inputs:")
    print(f"  Current holdings     : {len(current_holdings)}"
          f"{' (first run — empty)' if is_first_run else ''}")
    print(f"  Scoreable holdings   : {len(scoreable_holdings)}")
    print(f"  Retained (rank<=38)  : {len(retained)}  {sorted(retained)}")
    print(f"  Forced out (rank>38) : {len(forced_out)} {sorted(forced_out)}")

    # ── Step 2: Forced-in — top FORCED_IN_N non-holders ──
    non_holders_df = df[~df['symbol'].isin(scoreable_holdings)].copy()
    forced_in = set(
        non_holders_df.sort_values('mr_rank')
        .head(FORCED_IN_N)['symbol']
    )
    print(f"  Forced in (top {FORCED_IN_N} non-holders): "
          f"{len(forced_in)}  {sorted(forced_in)}")

    # ── Step 3: Edge case — retained + forced_in > PORTFOLIO_N ──
    combined = retained | forced_in
    if len(combined) > PORTFOLIO_N:
        excess = len(combined) - PORTFOLIO_N
        # Drop lowest-ranked (highest mr_rank number) from retained
        retained_ranked = (
            df[df['symbol'].isin(retained)]
            .sort_values('mr_rank', ascending=False)  # worst rank first
        )
        drop_symbols = set(retained_ranked.head(excess)['symbol'])
        print(f"  Edge case: retained+forced_in={len(combined)} > {PORTFOLIO_N}. "
              f"Dropping {excess} lowest-ranked retained: {sorted(drop_symbols)}")
        retained = retained - drop_symbols
        forced_out = forced_out | drop_symbols

    # ── Step 4: Fill remaining slots ──
    slots_filled = retained | forced_in
    slots_remaining = PORTFOLIO_N - len(slots_filled)
    print(f"  Slots after forced_in + retained: {len(slots_filled)}, "
          f"remaining to fill: {slots_remaining}")

    if slots_remaining > 0:
        fill_pool = df[
            ~df['symbol'].isin(slots_filled) &
            ~df['symbol'].isin(scoreable_holdings)
        ].sort_values('mr_rank')
        fill_symbols = set(fill_pool.head(slots_remaining)['symbol'])
    else:
        fill_symbols = set()

    print(f"  Fill symbols         : {len(fill_symbols)}  {sorted(fill_symbols)}")

    # ── Step 5: Final TOP_25 ──
    top25_symbols = retained | forced_in | fill_symbols
    assert len(top25_symbols) == PORTFOLIO_N, \
        (f"TOP_25 assembly error: got {len(top25_symbols)} symbols, "
         f"expected {PORTFOLIO_N}. Check scoring universe size.")

    # ── Step 6: Assign actions ──
    def get_action(row):
        sym  = row['symbol']
        rank = row['mr_rank']
        in_top25   = sym in top25_symbols
        was_holder = sym in current_holdings

        if in_top25:
            return 'HOLD' if was_holder else 'BUY'
        if was_holder:
            return 'SELL'
        if rank <= BUFFER_ZONE:
            return 'WATCHLIST'
        return None   # outside buffer zone, not a holder — excluded from output

    df['action'] = df.apply(get_action, axis=1)

    # ── Step 7: Tier column ──
    df['tier'] = df['symbol'].apply(
        lambda s: 'TOP_25' if s in top25_symbols else (
            'SELL' if s in (forced_out | unscored) else (
                'WATCHLIST' if (
                    int(df.loc[df['symbol'] == s, 'mr_rank'].iloc[0]) <= BUFFER_ZONE
                    and s not in top25_symbols
                ) else None
            )
        )
    )

    # ── Step 8: Build output — TOP_25 + SELL + WATCHLIST rows only ──
    output_df = df[df['action'].notna()].copy()

    # Attach SELL rows for unscored holdings (no rank info, fill with NA)
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

    return output_df, top25_symbols
