"""
Stage 6 — Hybrid Buffer Zone Reconstitution

SET 1 (FORCED_IN_N = 12):
  Top 12 by mr_rank OVERALL. Weinstein bypassed. Always enter portfolio.

SET 2 pool:
  Ranks 13+ → drop WS2 failures → re-rank from 13 → keep re-ranks 13-38.

SET 2 selection (SET2_TARGET = 13 slots):
  1. Current HOLDs in pool take priority (sorted by re-rank, ascending).
  2. Remaining slots filled by non-holders sorted by norm_momentum_score descending.
  3. Rest of pool → WATCHLIST.

WS2 failures (ranks 13+):
  - Current holding  → SELL (forced out, trend reversal)
  - Non-holder       → excluded, no action

Final portfolio = SET 1 (12) + SET 2 (13) = 25.
"""

import pandas as pd

PORTFOLIO_N  = 25
FORCED_IN_N  = 12
BUFFER_ZONE  = 38
SET2_TARGET  = PORTFOLIO_N - FORCED_IN_N   # 13


def apply_reconstitution(
    ranked_df: pd.DataFrame,
    current_holdings: set
) -> tuple:

    df = ranked_df.copy()

    # Holdings not in scored universe at all — force SELL
    unscored = current_holdings - set(df['symbol'])
    if unscored:
        print(f"WARNING: {len(unscored)} current holdings not in scored universe "
              f"(force SELL): {sorted(unscored)}")

    # ── SET 1: top 12 OVERALL, Weinstein bypassed ─────────────────────────
    set1_df      = df[df['mr_rank'] <= FORCED_IN_N].sort_values('mr_rank').copy()
    set1_symbols = set(set1_df['symbol'])

    print(f"\nSET 1 (top {FORCED_IN_N} overall, WS bypassed):")
    for _, r in set1_df.iterrows():
        action = 'HOLD' if r['symbol'] in current_holdings else 'BUY'
        ws     = r.get('weinstein_stage2', '?')
        print(f"  rank {int(r['mr_rank']):2d}  {r['symbol']:15s}  "
              f"WS2={ws}  score={r['norm_momentum_score']:.4f}  {action}")

    # ── SET 2 pool: ranks 13+, drop WS2 failures, re-rank ─────────────────
    set2_candidates = df[df['mr_rank'] > FORCED_IN_N].sort_values('mr_rank').copy()

    ws2_failures     = set2_candidates[
        set2_candidates['weinstein_stage2'] != True
    ].copy()
    ws2_fail_symbols = set(ws2_failures['symbol'])

    # Drop WS2 failures
    set2_pool = set2_candidates[
        set2_candidates['weinstein_stage2'] == True
    ].copy()

    # Re-rank from 13
    set2_pool = set2_pool.reset_index(drop=True)
    set2_pool['set2_rank'] = range(FORCED_IN_N + 1,
                                    FORCED_IN_N + 1 + len(set2_pool))

    # Keep re-ranks 13 to BUFFER_ZONE
    set2_pool = set2_pool[set2_pool['set2_rank'] <= BUFFER_ZONE].copy()

    print(f"\nSET 2 pool (re-ranked 13-{BUFFER_ZONE} after WS filter): "
          f"{len(set2_pool)} stocks")
    print(f"  WS2 failures dropped : {len(ws2_failures)}  "
          f"{sorted(ws2_fail_symbols)}")

    # HOLDs forced out by WS2 failure
    ws2_forced_sells = ws2_fail_symbols & current_holdings
    if ws2_forced_sells:
        print(f"  Holdings WS2-forced out (SELL): {sorted(ws2_forced_sells)}")

    # ── SET 2 selection: HOLDs first, fill with non-holders ───────────────
    set2_holds = set2_pool[
        set2_pool['symbol'].isin(current_holdings)
    ].sort_values('set2_rank')

    set2_nonholders = set2_pool[
        ~set2_pool['symbol'].isin(current_holdings)
    ].sort_values('norm_momentum_score', ascending=False)

    holds_selected  = set(set2_holds.head(SET2_TARGET)['symbol'])
    slots_remaining = SET2_TARGET - len(holds_selected)

    fill_selected = (
        set(set2_nonholders.head(slots_remaining)['symbol'])
        if slots_remaining > 0 else set()
    )

    set2_selected  = holds_selected | fill_selected
    watchlist_pool = set(set2_pool['symbol']) - set2_selected

    print(f"\nSET 2 selection ({SET2_TARGET} slots):")
    print(f"  HOLDs retained   : {len(holds_selected)}  {sorted(holds_selected)}")
    print(f"  Non-holder fills : {len(fill_selected)}  {sorted(fill_selected)}")
    print(f"  Watchlist        : {len(watchlist_pool)}  {sorted(watchlist_pool)}")

    # ── Final TOP_25 ────────────────────────────────────────────────────────
    top25_symbols = set1_symbols | set2_selected
    assert len(top25_symbols) == PORTFOLIO_N, \
        (f"TOP_25 assembly error: got {len(top25_symbols)}, "
         f"expected {PORTFOLIO_N}.")

    # ── Assign actions ──────────────────────────────────────────────────────
    def get_action(row):
        sym = row['symbol']
        if sym in top25_symbols:
            return 'HOLD' if sym in current_holdings else 'BUY'
        if sym in current_holdings:
            return 'SELL'
        if sym in watchlist_pool:
            return 'WATCHLIST'
        return None

    df['action'] = df.apply(get_action, axis=1)

    # ── Assign tiers ────────────────────────────────────────────────────────
    def get_tier(row):
        sym = row['symbol']
        if sym in top25_symbols:      return 'TOP_25'
        if sym in current_holdings:   return 'SELL'
        if sym in watchlist_pool:     return 'WATCHLIST'
        return None

    df['tier'] = df.apply(get_tier, axis=1)

    # Add unscored holdings as SELL rows
    output_df = df[df['action'].notna()].copy()

    if unscored:
        unscored_rows = pd.DataFrame({
            'symbol': sorted(unscored),
            'tier':   'SELL',
            'action': 'SELL',
        })
        output_df = pd.concat([output_df, unscored_rows], ignore_index=True)

    print(f"\nReconstitution output:")
    print(f"  TOP_25    : {(output_df['tier'] == 'TOP_25').sum()}")
    print(f"    HOLD    : {(output_df['action'] == 'HOLD').sum()}")
    print(f"    BUY     : {(output_df['action'] == 'BUY').sum()}")
    print(f"  SELL      : {(output_df['action'] == 'SELL').sum()}")
    print(f"  WATCHLIST : {(output_df['action'] == 'WATCHLIST').sum()}")

    # ── Weinstein reject log ────────────────────────────────────────────────
    weinstein_rejects = pd.DataFrame()

    if len(ws2_failures) > 0:
        rej = ws2_failures[['symbol', 'mr_rank', 'norm_momentum_score',
                             'weighted_z', 'ret_12m1m',
                             'weinstein_stage2']].copy()
        rej['rejection_stage'] = rej['symbol'].apply(
            lambda s: 'WS2_HOLDING_FORCED_OUT'
                      if s in current_holdings else 'WS2_NONHOLDER_EXCLUDED'
        )
        rej['rejection_reason'] = rej['symbol'].apply(
            lambda s: 'Current holding: WS2=False — forced out (trend reversal)'
                      if s in current_holdings
                      else 'Non-holder: WS2=False — excluded from SET 2 pool'
        )
        weinstein_rejects = rej.sort_values('mr_rank').reset_index(drop=True)

    return output_df, top25_symbols, weinstein_rejects
