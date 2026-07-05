"""
Stage 6 (production) — Portfolio Selection (G6_C6)

Reads latest Stage 5 output, applies G6 gate + C6 score, produces
BUY/HOLD/SELL/WATCHLIST recommendations, and updates portfolio state.

portfolio_state.parquet tracks SYMBOL MEMBERSHIP ONLY (for hysteresis).
It assumes full compliance with prior week's TOP_25 recommendation --
no execution, shares, or price tracking. What the user does with the
recommendation is outside Stage 6's scope.

last_rebalance_date in portfolio_state.parquet stores the Stage 5 as_of_date
(T) the state was computed for -- NOT the script's wall-clock run_date.
This lets Stage 6 detect same-cycle reruns (same T, e.g. rerun after a bug
fix) and no-op entirely rather than double-processing or falsely suppressing
hysteresis.

File resolution pattern replicated exactly from stage5_assemble.py:
glob -> exclude backups -> regex DDMMYYYY -> parse to date -> sort -> latest.

Action logic:
  HOLD       = in TOP_25 AND in current_holdings
  BUY        = in TOP_25 AND NOT in current_holdings
  SELL       = in current_holdings AND NOT in TOP_25
  WATCHLIST  = in REST (rank 26-50), no action

Output:
  signals/stage6/portfolio_recommendations_{DDMMYYYY}.parquet  (50 rows: TOP_25 + REST)
  portfolio/portfolio_state.parquet                             (TOP_25 symbols only)
  portfolio/portfolio_history/portfolio_{DDMMYYYY}.parquet      (point-in-time snapshot)
"""
import glob
import re
import sys
from pathlib import Path
import pandas as pd

BASE = "/home/ec2-user/nse-factor-engine/"
sys.path.insert(0, BASE + "signals/stage6/metrics")
from g6_gate import apply_g6_gate
from c6_score import apply_c6_score, PORTFOLIO_N

PORTFOLIO_STATE_PATH = Path(BASE + "portfolio/portfolio_state.parquet")
PORTFOLIO_HISTORY_DIR = Path(BASE + "portfolio/portfolio_history/")
STAGE6_OUTPUT_DIR = Path(BASE + "signals/stage6/")

# ── Step 1: Resolve latest Stage 5 output (exact pattern from stage5_assemble.py) ──
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

print(f"Using signals file run_date: {run_date_str}")
print(f"Signals path: {SIGNALS_PATH}")

signals = pd.read_parquet(SIGNALS_PATH)
as_of_date = pd.Timestamp(signals['as_of_date'].iloc[0])
print(f"Input signals shape: {signals.shape}")
print(f"as_of_date (T) inside file: {as_of_date}")

# ── Step 2: Read current portfolio state (symbol membership only) ─────────
PORTFOLIO_HISTORY_DIR.mkdir(parents=True, exist_ok=True)

if PORTFOLIO_STATE_PATH.exists():
    portfolio_state = pd.read_parquet(PORTFOLIO_STATE_PATH)
    current_holdings = set(portfolio_state['symbol'])
    stored_last_rebalance_date = pd.Timestamp(portfolio_state['last_rebalance_date'].iloc[0]) if len(portfolio_state) > 0 else None
    print(f"Existing portfolio state: {len(current_holdings)} holdings, last_rebalance_date={stored_last_rebalance_date}")

    # ── Same-cycle rerun guard ──────────────────────────────────────────────
    if stored_last_rebalance_date is not None and as_of_date == stored_last_rebalance_date:
        print(f"\nSame-cycle rerun detected: as_of_date ({as_of_date}) == last_rebalance_date "
              f"({stored_last_rebalance_date}) already in portfolio_state.parquet.")
        print("No new signal date to act on. Skipping run -- no files written, no state changed.")
        sys.exit(0)
else:
    current_holdings = set()
    print("No existing portfolio state -- first run, empty holdings")

# ── Step 3: Apply G6 gate ─────────────────────────────────────────────────
gated = apply_g6_gate(signals)
pool_size = len(gated)
print(f"G6 gate pool size: {pool_size}")
if pool_size < PORTFOLIO_N:
    print(f"OBSERVATION: pool size {pool_size} < N={PORTFOLIO_N}. Deploying all available (Stage 8 backlog covers proportional sizing).")

# ── Step 4: Apply C6 score (top 50: TOP_25 + REST) ────────────────────────
ranked = apply_c6_score(gated, current_holdings=current_holdings, N=PORTFOLIO_N, pool_size=pool_size)
print(f"C6 scored/ranked rows: {len(ranked)}")

top25_symbols = set(ranked[ranked['tier'] == 'TOP_25']['symbol'])

# ── Step 5: Merge back other Stage 5 metrics ──────────────────────────────
merge_cols = [
    'symbol', 'ret_12m1m', 'rs_excess_ret_mkt', 'weinstein_stage2',
    'lottery_class', 'proximity_52w_high', 'as_of_date',
    'market_cap_cr', 'adtv_63_cr'
]
ranked = ranked.merge(signals[merge_cols], on='symbol', how='left', suffixes=('', '_dup'))
ranked = ranked[[c for c in ranked.columns if not c.endswith('_dup')]]

# ── Step 6: Assign action ─────────────────────────────────────────────────
def assign_action(row):
    if row['tier'] == 'REST':
        return 'WATCHLIST'
    in_holdings = row['symbol'] in current_holdings
    return 'HOLD' if in_holdings else 'BUY'

ranked['action'] = ranked.apply(assign_action, axis=1)

# SELL list: symbols in current_holdings NOT in new TOP_25
sell_symbols = current_holdings - top25_symbols
if len(sell_symbols) > 0:
    print(f"SELL list: {len(sell_symbols)} symbols -- {sorted(sell_symbols)}")
else:
    print("SELL list: empty")

# ── Step 7: Run date (IST) — used for output FILENAMES only ────────────────
run_date = pd.Timestamp.now(tz='Asia/Kolkata').normalize().tz_localize(None)
run_date_ddmmyyyy = run_date.strftime('%d%m%Y')

ranked['run_date'] = run_date

# ── Step 8: Write recommendations output (50 rows: TOP_25 + REST) ─────────
output_path = STAGE6_OUTPUT_DIR / f"portfolio_recommendations_{run_date_ddmmyyyy}.parquet"
ranked.to_parquet(output_path, index=False)
print(f"\nRecommendations written to: {output_path}")
print(f"Shape: {ranked.shape}")

# ── Step 9: Update portfolio_state.parquet (TOP_25 symbols only) ──────────
# last_rebalance_date = as_of_date (Stage 5 T), NOT run_date -- enables same-cycle detection
new_portfolio_state = pd.DataFrame({
    'symbol': sorted(top25_symbols),
    'last_rebalance_date': as_of_date
})
new_portfolio_state.to_parquet(PORTFOLIO_STATE_PATH, index=False)
print(f"Portfolio state updated: {len(new_portfolio_state)} holdings, last_rebalance_date={as_of_date}")

# ── Step 10: Write history snapshot ────────────────────────────────────────
history_path = PORTFOLIO_HISTORY_DIR / f"portfolio_{run_date_ddmmyyyy}.parquet"
new_portfolio_state.to_parquet(history_path, index=False)
print(f"History snapshot written to: {history_path}")

print("\nStage 6 complete.")
