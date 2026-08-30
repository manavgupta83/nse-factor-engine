import os
"""
Stage 6 (production) — Portfolio Selection (G6_MR Hybrid)

Gate     : lower_circuit_hits_63d < 3  (g6_gate.py)
Scoring  : MR vol-adjusted Z-score composite  (mr_score.py)
Recon    : Hybrid Weinstein buffer zone  (mr_reconstitute.py)

Modes:
  REBALANCE (default) : rebalances if >= 30 days since last_rebalance_date.
                        Skips if < 30 days — no files written, no state changed.
  MONITOR  (--monitor): always runs, never writes anything, prints current
                        ranking state vs last rebalance for mid-month monitoring.

Usage:
  python3 stage6_assemble.py                      # rebalance mode
  STAGE6_MODE=monitor python3 stage6_assemble.py  # monitor mode

Reject tracker (signals/stage6/rejects_{DDMMYYYY}.csv):
  Combined log of:
  1. G6 gate rejects     — lower_circuit_hits_63d >= 3
  2. Weinstein rejects   — holdings forced out by trend reversal
                         — fill-pool candidates blocked by trend reversal

Output (REBALANCE mode only):
  signals/stage6/portfolio_recommendations_{DDMMYYYY}.parquet
  signals/stage6/rejects_{DDMMYYYY}.csv
  portfolio/portfolio_state.parquet
  portfolio/portfolio_history/portfolio_{DDMMYYYY}.parquet
"""
import glob
import re
import sys
from pathlib import Path
import pandas as pd

MONITOR_MODE = os.environ.get("STAGE6_MODE", "").lower() == "monitor"

BASE = "/home/ec2-user/nse-factor-engine/"
sys.path.insert(0, BASE + "signals/stage6/metrics")
from mr_score import apply_mr_score, USE_G6_GATE
from mr_reconstitute import apply_reconstitution, PORTFOLIO_N, BUFFER_ZONE, FORCED_IN_N
from mr_beta import compute_beta, beta_to_df, beta_for_assembly

PORTFOLIO_STATE_PATH  = Path(BASE + "portfolio/portfolio_state.parquet")
PORTFOLIO_HISTORY_DIR = Path(BASE + "portfolio/portfolio_history/")
STAGE6_OUTPUT_DIR     = Path(BASE + "signals/stage6/")

REBALANCE_DAYS = 30   # minimum days between rebalances

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
    day=int(x[0][:2]), month=int(x[0][2:4]), year=int(x[0][4:])))
run_date_str, SIGNALS_PATH = dated[-1]

print("=" * 70)
print(f"STAGE 6 — Portfolio Selection (G6_MR Hybrid | "
      f"{'MONITOR' if MONITOR_MODE else 'REBALANCE'})")
print(f"USE_G6_GATE      : {USE_G6_GATE}  (lower_circuit_hits_63d < 3)")
print(f"PORTFOLIO_N      : {PORTFOLIO_N}")
print(f"BUFFER_ZONE      : {BUFFER_ZONE}")
print(f"FORCED_IN_N      : {FORCED_IN_N}  (non-holders rank<=12 forced in)")
print(f"Rebalance guard  : {REBALANCE_DAYS} days")
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

    if not MONITOR_MODE and stored_last_rebalance_date is not None:
        days_since = (pd.Timestamp.now().normalize() - stored_last_rebalance_date).days
        print(f"Days since last rebalance: {days_since}")
        if days_since < REBALANCE_DAYS:
            print(f"\n30-day guard: only {days_since} days since last rebalance "
                  f"({stored_last_rebalance_date.date()}). "
                  f"Need {REBALANCE_DAYS - days_since} more days.")
            print("Skipping — no files written, no state changed.")
            print("Tip: run with --monitor to see current rankings without rebalancing.")
            sys.exit(0)
        else:
            print(f"\n{days_since} days since last rebalance — proceeding.")
else:
    current_holdings = set()
    print("\nNo existing portfolio state — first run, empty holdings.")

# ── Step 3: Score ──
ranked_df, gate_rejects = apply_mr_score(signals)

# ── Step 4: Reconstitute ──
ranked, top25_symbols, weinstein_rejects = apply_reconstitution(
    ranked_df, current_holdings
)

# ── Step 4b: Compute Beta ──
beta_result = compute_beta(top25_symbols, as_of_date)
beta_df     = beta_to_df(beta_result, as_of_date)
beta_fields = beta_for_assembly(beta_result)

# ── MONITOR MODE — print and exit, nothing written ──
if MONITOR_MODE:
    print(f"\n{'='*70}")
    print("MONITOR MODE — current state vs last rebalance (nothing written)")
    print(f"{'='*70}")

    # Current holdings rank status
    print(f"\nCurrent holdings rank status ({len(current_holdings)} stocks):")
    if current_holdings:
        holding_rows = ranked_df[ranked_df['symbol'].isin(current_holdings)].copy()
        holding_rows = holding_rows.sort_values('mr_rank')
        cols = [c for c in ['mr_rank', 'symbol', 'norm_momentum_score',
                             'weighted_z', 'weinstein_stage2', 'ret_12m1m']
                if c in holding_rows.columns]
        print(holding_rows[cols].to_string(index=False))

        # Holdings that would be forced out
        would_force_out = holding_rows[holding_rows['mr_rank'] > BUFFER_ZONE]
        if len(would_force_out) > 0:
            print(f"\n  ⚠ Would be forced out (rank > {BUFFER_ZONE}): "
                  f"{sorted(would_force_out['symbol'].tolist())}")

    # What the portfolio would look like if rebalanced today
    print(f"\nIf rebalanced TODAY — projected TOP_25:")
    top25_df = ranked[ranked['tier'] == 'TOP_25'].copy()
    if 'mr_rank' in top25_df.columns:
        top25_df = top25_df.sort_values('mr_rank')

    # Add beta fields for monitor display
    top25_df['beta_12m']      = top25_df['symbol'].map(beta_fields['stock_beta'])
    top25_df['stock_12m_ret'] = top25_df['symbol'].map(beta_fields['stock_return'])
    top25_df['alpha_12m']     = top25_df['symbol'].map(beta_fields['stock_alpha'])

    # Load RSI_14 at last rebalance date from latest portfolio parquet
    port_files = sorted(STAGE6_OUTPUT_DIR.glob("portfolio_recommendations_*.parquet"))
    if port_files:
        last_port_df  = pd.read_parquet(port_files[-1])
        rsi_at_rebal  = (
            last_port_df[last_port_df['tier'] == 'TOP_25']
            .set_index('symbol')['rsi_14']
            .to_dict()
        )
        rebal_date    = pd.to_datetime(last_port_df['as_of_date'].iloc[0]).date()
        top25_df['rsi_14_rebal'] = top25_df['symbol'].map(rsi_at_rebal)
        top25_df['rsi_14_today'] = top25_df['rsi_14']
        top25_df['rsi_chg']      = (top25_df['rsi_14_today'] - top25_df['rsi_14_rebal']).round(1)
        rsi_cols = ['rsi_14_rebal', 'rsi_14_today', 'rsi_chg']
        print(f"  RSI_14 at rebalance ({rebal_date}) vs today")
    else:
        rsi_cols = ['rsi_14']

    cols = [c for c in ['mr_rank', 'symbol', 'action', 'norm_momentum_score',
                         'ret_12m1m'] + rsi_cols + ['beta_12m', 'stock_12m_ret', 'alpha_12m']
            if c in top25_df.columns]
    print(top25_df[cols].to_string(index=False))
    print(f"\n  Portfolio beta (12m) : {beta_fields['portfolio_beta']:.3f}")
    print(f"  Market 12m return    : {beta_fields['market_return']:.2%}")

    # New entrants vs exits vs holds
    projected_top25 = set(top25_df['symbol'])
    would_buy  = projected_top25 - current_holdings
    would_sell = current_holdings - projected_top25
    would_hold = projected_top25 & current_holdings
    print(f"\n  Would BUY  ({len(would_buy)})  : {sorted(would_buy)}")
    print(f"  Would HOLD ({len(would_hold)}) : {sorted(would_hold)}")
    print(f"  Would SELL ({len(would_sell)}) : {sorted(would_sell)}")

    # Watchlist
    watchlist = ranked[ranked['action'] == 'WATCHLIST']
    if len(watchlist) > 0:
        print(f"\nWATCHLIST (rank <= {BUFFER_ZONE}, {len(watchlist)} symbols):")
        wl_cols = [c for c in ['mr_rank', 'symbol', 'weinstein_stage2',
                                'norm_momentum_score']
                   if c in watchlist.columns]
        print(watchlist[wl_cols].to_string(index=False))

    # Gate rejects
    if len(gate_rejects) > 0:
        print(f"\nG6 gate rejects ({len(gate_rejects)}):")
        print(gate_rejects[['symbol', 'rejection_reason']].to_string(index=False))

    # Weinstein rejects
    if len(weinstein_rejects) > 0:
        print(f"\nWeinstein rejects ({len(weinstein_rejects)}):")
        print(weinstein_rejects[['symbol', 'mr_rank', 'rejection_stage',
                                  'rejection_reason']].to_string(index=False))

    print(f"\nMONITOR complete. Next rebalance eligible after: "
          f"{(stored_last_rebalance_date + pd.Timedelta(days=REBALANCE_DAYS)).date() if stored_last_rebalance_date else 'immediately'}")
    print("=" * 70)
    sys.exit(0)

# ── REBALANCE MODE — full write ──

# ── Step 5: Merge back remaining Stage 5 columns ──
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
cols_to_merge = [c for c in extra_cols if c == 'symbol' or c not in ranked.columns]
if len(cols_to_merge) > 1:
    ranked = ranked.merge(
        signals[cols_to_merge],
        on='symbol', how='left',
        suffixes=('', '_dup')
    )
    ranked = ranked[[c for c in ranked.columns if not c.endswith('_dup')]]

# ── Step 5b: Add beta fields to ranked ──
ranked['beta_12m']       = ranked['symbol'].map(beta_fields['stock_beta'])
ranked['stock_12m_ret']  = ranked['symbol'].map(beta_fields['stock_return'])
ranked['alpha_12m']      = ranked['symbol'].map(beta_fields['stock_alpha'])
ranked['market_12m_ret'] = beta_fields['market_return']
ranked['portfolio_beta'] = beta_fields['portfolio_beta']

# ── Step 6: Run date ──
run_date          = pd.Timestamp.now(tz='Asia/Kolkata').normalize().tz_localize(None)
run_date_ddmmyyyy = run_date.strftime('%d%m%Y')
ranked['run_date']   = run_date
ranked['as_of_date'] = as_of_date

# ── Step 7: Fully missing SELL rows ──
already_in_output = set(ranked['symbol'])
fully_missing = current_holdings - already_in_output
if fully_missing:
    print(f"\nFully missing from scored output: {sorted(fully_missing)}")
    avail_cols = [c for c in extra_cols if c in signals.columns]
    missing_metrics = signals[signals['symbol'].isin(fully_missing)][avail_cols].copy()
    missing_metrics['tier']       = 'SELL'
    missing_metrics['action']     = 'SELL'
    missing_metrics['run_date']   = run_date
    missing_metrics['as_of_date'] = as_of_date
    for col in ranked.columns:
        if col not in missing_metrics.columns:
            missing_metrics[col] = pd.NA
    missing_metrics = missing_metrics[ranked.columns]
    ranked = pd.concat([ranked, missing_metrics], ignore_index=True)

sell_count = (ranked['action'] == 'SELL').sum()
print(f"\nSELL total : {sell_count} symbols")
print(f"Output rows: {len(ranked)}")

# ── Step 8: Write recommendations ──
output_path = STAGE6_OUTPUT_DIR / f"portfolio_recommendations_{run_date_ddmmyyyy}.parquet"
ranked.to_parquet(output_path, index=False)
print(f"\nRecommendations written : {output_path}")
print(f"Shape                   : {ranked.shape}")

# ── Step 9: Write combined reject tracker CSV ──
gate_rejects['as_of_date']      = as_of_date
weinstein_rejects['as_of_date'] = as_of_date

all_rejects = pd.concat([gate_rejects, weinstein_rejects], ignore_index=True)
all_rejects['run_date'] = run_date

if len(all_rejects) > 0:
    rejects_path = STAGE6_OUTPUT_DIR / f"rejects_{run_date_ddmmyyyy}.csv"
    all_rejects.to_csv(rejects_path, index=False)
    print(f"\nReject tracker written  : {rejects_path}")
    print(f"  G6 gate rejects       : {len(gate_rejects)}")
    print(f"  Weinstein rejects     : {len(weinstein_rejects)}")
    print(f"  Total rejects         : {len(all_rejects)}")
else:
    print("\nReject tracker: 0 rejects this run")

# ── Step 10: Update portfolio_state.parquet ──
new_portfolio_state = pd.DataFrame({
    'symbol':              sorted(top25_symbols),
    'last_rebalance_date': as_of_date,
})
new_portfolio_state.to_parquet(PORTFOLIO_STATE_PATH, index=False)
print(f"\nPortfolio state updated : {len(new_portfolio_state)} holdings, "
      f"last_rebalance_date={as_of_date.date()}")

# ── Step 11: Write history snapshot ──
history_path = PORTFOLIO_HISTORY_DIR / f"portfolio_{run_date_ddmmyyyy}.parquet"
new_portfolio_state.to_parquet(history_path, index=False)
print(f"History snapshot written: {history_path}")

# ── Step 12: Summary ──
top25_out = ranked[ranked['tier'] == 'TOP_25'].copy()
if 'mr_rank' in top25_out.columns:
    top25_out = top25_out.sort_values('mr_rank')
print(f"\n{'='*70}")
print(f"Final TOP_25 ({len(top25_out)} symbols):")
display_cols = [c for c in ['mr_rank', 'symbol', 'action', 'weinstein_stage2',
                             'norm_momentum_score', 'weighted_z',
                             'ret_12m1m', 'ret_6m1m', 'vol_252']
                if c in top25_out.columns]
print(top25_out[display_cols].to_string(index=False))

watchlist = ranked[ranked['action'] == 'WATCHLIST']
if len(watchlist) > 0:
    print(f"\nWATCHLIST (rank <= {BUFFER_ZONE}, {len(watchlist)} symbols):")
    wl_cols = [c for c in ['mr_rank', 'symbol', 'weinstein_stage2',
                            'norm_momentum_score']
               if c in watchlist.columns]
    print(watchlist[wl_cols].to_string(index=False))

sells = ranked[ranked['action'] == 'SELL']
if len(sells) > 0:
    print(f"\nSELL ({len(sells)} symbols): {sorted(sells['symbol'].tolist())}")

print(f"\n  Portfolio beta (12m) : {beta_fields['portfolio_beta']:.3f}")
print(f"  Market 12m return    : {beta_fields['market_return']:.2%}")
print(f"\nStage 6 complete.")
print("=" * 70)
