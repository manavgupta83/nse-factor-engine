"""
Stage 7 — Momentum Ratio Portfolio Assembler

Reads latest Stage 5 output, applies MR scoring, applies buffer zone
reconstitution, writes recommendations and updates portfolio state.

Portfolio state:
  signals/stage7/portfolio_state_mr.parquet
  Columns: symbol, last_rebalance_date
  Tracks TOP_25 symbol membership only. No prices, no execution.

Same-cycle rerun guard: if as_of_date in the incoming signals file matches
last_rebalance_date already stored in portfolio_state_mr.parquet, Stage 7
skips the run entirely — no files written, no state changed.

Output:
  signals/stage7/portfolio_recommendations_mr_{DDMMYYYY}.parquet
"""

import glob
import re
import sys
from pathlib import Path

import pandas as pd

BASE = "/home/ec2-user/nse-factor-engine/"
sys.path.insert(0, BASE + "signals/stage7/metrics")
from mr_score import apply_mr_score, USE_G6_GATE
from mr_reconstitute import apply_reconstitution, PORTFOLIO_N, BUFFER_ZONE, FORCED_IN_N

PORTFOLIO_STATE_PATH = Path(BASE + "signals/stage7/portfolio_state_mr.parquet")
STAGE7_OUTPUT_DIR    = Path(BASE + "signals/stage7/")

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
dated.sort(key=lambda x: pd.Timestamp(
    day=int(x[0][:2]), month=int(x[0][2:4]), year=int(x[0][4:])
))
run_date_str, SIGNALS_PATH = dated[-1]

print("=" * 65)
print("STAGE 7 — Momentum Ratio Portfolio")
print(f"USE_G6_GATE      : {USE_G6_GATE}")
print(f"BUFFER_ZONE      : {BUFFER_ZONE}")
print(f"FORCED_IN_N      : {FORCED_IN_N}")
print(f"Signals run_date : {run_date_str}")
print(f"Signals path     : {SIGNALS_PATH}")
print("=" * 65)

signals    = pd.read_parquet(SIGNALS_PATH)
as_of_date = pd.Timestamp(signals['as_of_date'].iloc[0])
print(f"Input shape      : {signals.shape}")
print(f"as_of_date (T)   : {as_of_date.date()}")

# ── Step 2: Read portfolio state ──
if PORTFOLIO_STATE_PATH.exists():
    state_df = pd.read_parquet(PORTFOLIO_STATE_PATH)
    current_holdings = set(state_df['symbol'])
    stored_rebalance_date = pd.Timestamp(state_df['last_rebalance_date'].iloc[0]) \
                            if len(state_df) > 0 else None
    print(f"\nExisting state   : {len(current_holdings)} holdings, "
          f"last_rebalance_date={stored_rebalance_date}")

    # Same-cycle rerun guard
    if stored_rebalance_date is not None and as_of_date == stored_rebalance_date:
        print(f"\nSame-cycle rerun detected: as_of_date ({as_of_date.date()}) "
              f"== last_rebalance_date ({stored_rebalance_date.date()}).")
        print("No new signal date. Skipping — no files written, no state changed.")
        sys.exit(0)
else:
    current_holdings = set()
    stored_rebalance_date = None
    print("\nNo existing portfolio state — first run, empty holdings.")

# ── Step 3: Score ──
ranked_df = apply_mr_score(signals)

# ── Step 4: Reconstitute ──
output_df, top25_symbols = apply_reconstitution(ranked_df, current_holdings)

# ── Step 5: Attach run metadata ──
run_date          = pd.Timestamp.now(tz='Asia/Kolkata').normalize().tz_localize(None)
run_date_ddmmyyyy = run_date.strftime('%d%m%Y')
output_df['run_date']   = run_date
output_df['as_of_date'] = as_of_date

# ── Step 6: Write recommendations ──
output_path = STAGE7_OUTPUT_DIR / f"portfolio_recommendations_mr_{run_date_ddmmyyyy}.parquet"
output_df.to_parquet(output_path, index=False)
print(f"\nRecommendations  : {output_path}")
print(f"Output shape     : {output_df.shape}")

# ── Step 7: Update portfolio state ──
new_state = pd.DataFrame({
    'symbol':               sorted(top25_symbols),
    'last_rebalance_date':  as_of_date,
})
new_state.to_parquet(PORTFOLIO_STATE_PATH, index=False)
print(f"Portfolio state  : {PORTFOLIO_STATE_PATH} "
      f"({len(new_state)} holdings, last_rebalance_date={as_of_date.date()})")

# ── Step 8: Print summary ──
top25_out = output_df[output_df['tier'] == 'TOP_25'].sort_values('mr_rank')
print(f"\nFinal TOP_25:")
print(top25_out[[
    'mr_rank', 'symbol', 'action', 'norm_momentum_score',
    'weighted_z', 'ret_12m1m', 'ret_6m1m', 'vol_252'
]].to_string(index=False))

sells = output_df[output_df['action'] == 'SELL']
if len(sells) > 0:
    print(f"\nSELL ({len(sells)}):")
    print(sells[['symbol', 'mr_rank', 'norm_momentum_score']].to_string(index=False))

watchlist = output_df[output_df['action'] == 'WATCHLIST']
if len(watchlist) > 0:
    print(f"\nWATCHLIST — rank <= {BUFFER_ZONE}, not in TOP_25 ({len(watchlist)}):")
    print(watchlist[[
        'mr_rank', 'symbol', 'norm_momentum_score'
    ]].to_string(index=False))

print(f"\nStage 7 complete.")
print("=" * 65)
