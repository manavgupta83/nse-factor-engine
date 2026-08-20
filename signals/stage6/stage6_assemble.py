"""
Stage 6 (production) — Portfolio Selection (G6_MR)

Reads latest Stage 5 output, applies MR scoring (with G6 gate applied
internally), applies buffer zone reconstitution, produces
BUY/HOLD/SELL/WATCHLIST recommendations, and updates portfolio state.

Scoring engine: Momentum Ratio (mr_score.py + mr_reconstitute.py)
Replaces: C6 rank-sum scoring (c6_score.py)

portfolio_state.parquet tracks SYMBOL MEMBERSHIP ONLY (for hysteresis).
Assumes full compliance with prior week's TOP_25 recommendation.
No execution, shares, or price tracking.

last_rebalance_date stores the Stage 5 as_of_date (T) the state was
computed for. Same-cycle rerun guard: if as_of_date matches
last_rebalance_date already in portfolio_state.parquet, Stage 6 skips
entirely — no files written, no state changed.

Output:
  signals/stage6/portfolio_recommendations_{DDMMYYYY}.parquet
  portfolio/portfolio_state.parquet
  portfolio/portfolio_history/portfolio_{DDMMYYYY}.parquet
"""
import glob
import re
import sys
from pathlib import Path
import pandas as pd

BASE = "/home/ec2-user/nse-factor-engine/"
sys.path.insert(0, BASE + "signals/stage6/metrics")
from mr_score import apply_mr_score, USE_G6_GATE
from mr_reconstitute import apply_reconstitution, PORTFOLIO_N, BUFFER_ZONE, FORCED_IN_N

PORTFOLIO_STATE_PATH  = Path(BASE + "portfolio/portfolio_state.parquet")
PORTFOLIO_HISTORY_DIR = Path(BASE + "portfolio/portfolio_history/")
STAGE6_OUTPUT_DIR     = Path(BASE + "signals/stage6/")

# ── Step 1: Resolve latest Stage 5 output ──
signals_files = glob.glob(BASE + "signals/final/momentum_signals_final_*.parquet")
signals_files = [f for f in signals_files if "_pre_stage" not in f]
date_re = re.compile(r"momentum_signals_final_(\d{8})\.parquet$")
dated = []
for f in signals_files:
    m = date_re.search(f)
    if m:
        dated.append((m.group(1), f))
assert dated, "No signals files found in signals/final/"
dated.sort(key=lambda x: pd.Timestamp(day=int(x[0][:2]), month=int(x[0][2:4]), year=int(x[0][4:])))
run_date_str, SIGNALS_PATH = dated[-1]

print("=" * 70)
print("STAGE 6 — Portfolio Selection (G6_MR)")
print(f"USE_G6_GATE      : {USE_G6_GATE}")
print(f"PORTFOLIO_N      : {PORTFOLIO_N}")
print(f"BUFFER_ZONE      : {BUFFER_ZONE}")
print(f"FORCED_IN_N      : {FORCED_IN_N}")
print(f"Signals run_date : {run_date_str}")
print(f"Signals path     : {SIGNALS_PATH}")
print("=" * 70)

signals    = pd.read_parquet(SIGNALS_PATH)
as_of_date = pd.Timestamp(signals['as_of_date'].iloc[0])
print(f"Input signals shape : {signals.shape}")
print(f"as_of_date (T)      : {as_of_date.date()}")

# ── Step 2: Read current portfolio state ──
PORTFOLIO_HISTORY_DIR.mkdir(parents=True, exist_ok=True)

if PORTFOLIO_STATE_PATH.exists():
    portfolio_state = pd.read_parquet(PORTFOLIO_STATE_PATH)
    current_holdings = set(portfolio_state['symbol'])
    stored_last_rebalance_date = (
        pd.Timestamp(portfolio_state['last_rebalance_date'].iloc[0])
        if len(portfolio_state) > 0 else None
    )
    print(f"\nExisting portfolio  : {len(current_holdings)} holdings, "
          f"last_rebalance_date={stored_last_rebalance_date}")

    if stored_last_rebalance_date is not None and as_of_date == stored_last_rebalance_date:
        print(f"\nSame-cycle rerun detected: as_of_date ({as_of_date.date()}) "
              f"== last_rebalance_date ({stored_last_rebalance_date.date()}).")
        print("No new signal date. Skipping — no files written, no state changed.")
        sys.exit(0)
else:
    current_holdings = set()
    print("\nNo existing portfolio state — first run, empty holdings.")

# ── Step 3: Score with MR (G6 gate applied internally) ──
ranked_df = apply_mr_score(signals)

# ── Step 4: Reconstitute portfolio with buffer zone logic ──
ranked, top25_symbols = apply_reconstitution(ranked_df, current_holdings)

# ── Step 5: Merge back remaining Stage 5 columns not carried by mr_score ──
# mr_score returns the full signals row for each scored symbol, so most
# columns are already present. We merge in the technical indicator columns
# that mr_score does not use and therefore does not guarantee are present.
extra_cols = [
    'symbol',
    'rsi_14', 'rsi_7',
    'dist_ema_20', 'dist_ema_50',
    'mfi_14',
    'stoch_rsi_k', 'stoch_rsi_d',
    'bb_pct_b',
    'bb_bandwidth_curr_wk', 'bb_bandwidth_prev_wk',
    'bb_bandwidth_prev_2wk', 'bb_bandwidth_prev_3wk', 'bb_squeeze',
]
# Only merge columns not already on ranked (avoid _dup collisions)
cols_to_merge = [c for c in extra_cols if c == 'symbol' or c not in ranked.columns]
if len(cols_to_merge) > 1:
    ranked = ranked.merge(
        signals[cols_to_merge],
        on='symbol', how='left',
        suffixes=('', '_dup')
    )
    ranked = ranked[[c for c in ranked.columns if not c.endswith('_dup')]]

# ── Step 6: Run date (IST) ──
run_date          = pd.Timestamp.now(tz='Asia/Kolkata').normalize().tz_localize(None)
run_date_ddmmyyyy = run_date.strftime('%d%m%Y')
ranked['run_date']   = run_date
ranked['as_of_date'] = as_of_date

# ── Step 7: Append fully-exited SELL rows (symbols in holdings but not
#    scored at all — e.g. dropped from universe between runs) ──
# Note: mr_reconstitute handles unscored holdings internally, but those
# rows may lack the extra_cols from Step 5. Ensure they are present.
already_in_output = set(ranked['symbol'])
fully_missing = current_holdings - already_in_output
if fully_missing:
    print(f"\nFully missing from scored output: {sorted(fully_missing)}")
    missing_metrics = signals[signals['symbol'].isin(fully_missing)][extra_cols].copy()
    missing_metrics['tier']        = 'SELL'
    missing_metrics['action']      = 'SELL'
    missing_metrics['run_date']    = run_date
    missing_metrics['as_of_date']  = as_of_date
    for col in ranked.columns:
        if col not in missing_metrics.columns:
            missing_metrics[col] = pd.NA
    missing_metrics = missing_metrics[ranked.columns]
    ranked = pd.concat([ranked, missing_metrics], ignore_index=True)

sell_count = (ranked['action'] == 'SELL').sum()
print(f"\nSELL total : {sell_count} symbols")
print(f"Output rows: {len(ranked)}")

# ── Step 8: Write recommendations output ──
output_path = STAGE6_OUTPUT_DIR / f"portfolio_recommendations_{run_date_ddmmyyyy}.parquet"
ranked.to_parquet(output_path, index=False)
print(f"\nRecommendations written : {output_path}")
print(f"Shape                   : {ranked.shape}")
print(f"Columns                 : {sorted(ranked.columns.tolist())}")

# ── Step 9: Update portfolio_state.parquet ──
new_portfolio_state = pd.DataFrame({
    'symbol':              sorted(top25_symbols),
    'last_rebalance_date': as_of_date,
})
new_portfolio_state.to_parquet(PORTFOLIO_STATE_PATH, index=False)
print(f"\nPortfolio state updated : {len(new_portfolio_state)} holdings, "
      f"last_rebalance_date={as_of_date.date()}")

# ── Step 10: Write history snapshot ──
history_path = PORTFOLIO_HISTORY_DIR / f"portfolio_{run_date_ddmmyyyy}.parquet"
new_portfolio_state.to_parquet(history_path, index=False)
print(f"History snapshot written: {history_path}")

# ── Step 11: Print summary ──
top25_out = ranked[ranked['tier'] == 'TOP_25'].copy()
if 'mr_rank' in top25_out.columns:
    top25_out = top25_out.sort_values('mr_rank')
print(f"\n{'='*70}")
print(f"Final TOP_25 ({len(top25_out)} symbols):")
display_cols = [c for c in ['mr_rank', 'symbol', 'action', 'norm_momentum_score',
                             'weighted_z', 'ret_12m1m', 'ret_6m1m', 'vol_252']
                if c in top25_out.columns]
print(top25_out[display_cols].to_string(index=False))

watchlist = ranked[ranked['action'] == 'WATCHLIST']
if len(watchlist) > 0:
    print(f"\nWATCHLIST (rank <= {BUFFER_ZONE}, {len(watchlist)} symbols):")
    wl_cols = [c for c in ['mr_rank', 'symbol', 'norm_momentum_score']
               if c in watchlist.columns]
    print(watchlist[wl_cols].to_string(index=False))

sells = ranked[ranked['action'] == 'SELL']
if len(sells) > 0:
    print(f"\nSELL ({len(sells)} symbols): {sorted(sells['symbol'].tolist())}")

print(f"\nStage 6 complete.")
print("=" * 70)
