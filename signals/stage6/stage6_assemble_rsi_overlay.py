"""
Stage 6 RSI Overlay (production) — Portfolio Selection with Mid-Month RSI Filter
=================================================================================
Drop-in replacement for stage6_assemble.py. Adds MID_MONTH mode on top of
the existing REBALANCE and MONITOR modes.

Modes:
  REBALANCE  (default)          : identical to stage6_assemble.py + writes
                                  entry_date, entry_type to portfolio_state.parquet
  MONITOR    (STAGE6_MODE=monitor)    : identical to stage6_assemble.py (read-only)
  MID_MONTH  (STAGE6_MODE=mid_month)  : NEW — executes day-15 RSI filter:
                                        exit weak holdings, replace with strong
                                        watchlist stocks, write updated state

Mid-month logic:
  1. Count trading days since last_rebalance_date
     → If < CHECK_DAY (10): print status and exit (not yet due)
  2. For each held stock, compute Wilder EMA RSI-14 using today's prices
     → If RSI < RSI_EXIT_THRESH (50): MID_SELL
  3. Load WATCHLIST from latest portfolio_recommendations_*.parquet
     → For each MID_SELL, find best-ranked watchlist stock with RSI > RSI_ENTRY_THRESH
     → MID_BUY that stock
     → If none found: slot goes to cash
  4. Write updated portfolio_state.parquet
  5. Write portfolio_recommendations_{DDMMYYYY}_mid.parquet
  6. Output JSON for Telegram bot

portfolio_state.parquet schema (extended):
  symbol | last_rebalance_date | entry_date | entry_type ('SOM' | 'MID')

Fallback: if old portfolio_state.parquet (without entry_date/entry_type) is read,
          defaults to entry_date=last_rebalance_date, entry_type='SOM'.

Configurable:
  RSI_EXIT_THRESH  = 50
  RSI_ENTRY_THRESH = 50
  CHECK_DAY        = 10   # ~15 calendar days = ~10 trading days

Usage:
  python3 stage6_assemble_rsi_overlay.py                       # rebalance
  STAGE6_MODE=monitor   python3 stage6_assemble_rsi_overlay.py # monitor
  STAGE6_MODE=mid_month python3 stage6_assemble_rsi_overlay.py # mid-month RSI
"""

import os, glob, re, sys, json
from pathlib import Path
import pandas as pd
import numpy as np

BASE = "/home/ec2-user/nse-factor-engine/"
sys.path.insert(0, BASE + "signals/stage6/metrics")
from mr_score       import apply_mr_score, USE_G6_GATE
from mr_reconstitute import apply_reconstitution, PORTFOLIO_N, BUFFER_ZONE, FORCED_IN_N
from mr_beta        import compute_beta, beta_to_df, beta_for_assembly

# ── Mode ──────────────────────────────────────────────────────────────────────
_mode = os.environ.get("STAGE6_MODE", "").lower()
REBALANCE_MODE  = _mode not in ("monitor", "mid_month")
MONITOR_MODE    = _mode == "monitor"
MID_MONTH_MODE  = _mode == "mid_month"

# ── Paths ─────────────────────────────────────────────────────────────────────
PORTFOLIO_STATE_PATH  = Path(BASE + "portfolio/portfolio_state.parquet")
PORTFOLIO_HISTORY_DIR = Path(BASE + "portfolio/portfolio_history/")
STAGE6_OUTPUT_DIR     = Path(BASE + "signals/stage6/")
PRICES_PATH           = BASE + "data/prices.parquet"
REBALANCE_DAYS        = 30

# ── Mid-month config ──────────────────────────────────────────────────────────
CHECK_DAY        = 10   # ~15 calendar days = ~10 trading days
RSI_EXIT_THRESH  = 50
RSI_ENTRY_THRESH = 50

# ── RSI helpers ───────────────────────────────────────────────────────────────
def load_prices_by_sym():
    px = pd.read_parquet(PRICES_PATH, columns=['symbol', 'date', 'close'])
    px['date'] = pd.to_datetime(px['date'])
    return {
        sym: grp[['date','close']].sort_values('date').reset_index(drop=True)
        for sym, grp in px.groupby('symbol')
    }

def compute_wilder_rsi(sym, prices_by_sym, as_of_date, window=14):
    """Wilder EMA RSI-14 — same method as production monitor mode."""
    sym_df = prices_by_sym.get(sym)
    if sym_df is None:
        return np.nan
    s = sym_df[sym_df['date'] <= pd.Timestamp(as_of_date)].tail(window * 3)
    if len(s) < window + 1:
        return np.nan
    delta = s['close'].diff()
    gain  = delta.clip(lower=0)
    loss  = (-delta).clip(lower=0)
    avg_g = gain.ewm(com=window - 1, min_periods=window).mean()
    avg_l = loss.ewm(com=window - 1, min_periods=window).mean()
    rs    = avg_g / avg_l.replace(0, float('nan'))
    rsi   = 100 - (100 / (1 + rs))
    val   = rsi.iloc[-1]
    return round(float(val), 2) if pd.notna(val) else np.nan

def trading_days_since(since_date):
    """List of trading days from since_date (exclusive) to today (inclusive)."""
    px_dates = pd.read_parquet(PRICES_PATH, columns=['date'])
    px_dates['date'] = pd.to_datetime(px_dates['date'])
    all_dates  = sorted(px_dates['date'].unique())
    since_ts   = pd.Timestamp(since_date)
    today      = pd.Timestamp.now().normalize()
    return [d for d in all_dates if since_ts < d <= today]

# ── Resolve latest Stage 5 signals ───────────────────────────────────────────
signals_files = glob.glob(BASE + "signals/final/momentum_signals_final_*.parquet")
signals_files = [f for f in signals_files if "_pre_stage" not in f]
date_re       = re.compile(r"momentum_signals_final_(\d{8})\.parquet$")
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
print(f"STAGE 6 RSI OVERLAY — Portfolio Selection | "
      f"{'REBALANCE' if REBALANCE_MODE else 'MONITOR' if MONITOR_MODE else 'MID_MONTH'}")
print(f"USE_G6_GATE      : {USE_G6_GATE}")
print(f"PORTFOLIO_N      : {PORTFOLIO_N}")
print(f"BUFFER_ZONE      : {BUFFER_ZONE}")
print(f"FORCED_IN_N      : {FORCED_IN_N}")
print(f"RSI_EXIT_THRESH  : {RSI_EXIT_THRESH}  (mid-month exit if RSI < this)")
print(f"RSI_ENTRY_THRESH : {RSI_ENTRY_THRESH}  (replacement if RSI > this)")
print(f"CHECK_DAY        : {CHECK_DAY}  (trading days into period)")
print(f"Signals path     : {SIGNALS_PATH}")
print("=" * 70)

signals    = pd.read_parquet(SIGNALS_PATH)
as_of_date = pd.Timestamp(signals['as_of_date'].iloc[0])
run_date   = pd.Timestamp.now(tz='Asia/Kolkata').normalize().tz_localize(None)
run_date_ddmmyyyy = run_date.strftime('%d%m%Y')
print(f"as_of_date : {as_of_date.date()}")
print(f"run_date   : {run_date.date()}")

# ── Load portfolio state ──────────────────────────────────────────────────────
PORTFOLIO_HISTORY_DIR.mkdir(parents=True, exist_ok=True)

if PORTFOLIO_STATE_PATH.exists():
    portfolio_state = pd.read_parquet(PORTFOLIO_STATE_PATH)
    current_holdings = set(portfolio_state['symbol'])
    stored_last_rebalance_date = (
        pd.Timestamp(portfolio_state['last_rebalance_date'].iloc[0])
        if len(portfolio_state) > 0 else None
    )
    # Graceful handling of old schema (no entry_date/entry_type)
    if 'entry_date' not in portfolio_state.columns:
        portfolio_state['entry_date'] = stored_last_rebalance_date
    if 'entry_type' not in portfolio_state.columns:
        portfolio_state['entry_type'] = 'SOM'
    print(f"\nPortfolio state : {len(current_holdings)} holdings | "
          f"last_rebalance={stored_last_rebalance_date.date() if stored_last_rebalance_date else 'None'}")
else:
    portfolio_state            = pd.DataFrame()
    current_holdings           = set()
    stored_last_rebalance_date = None
    print("\nNo existing portfolio state — first run.")

# ══════════════════════════════════════════════════════════════════════════════
# MID_MONTH MODE
# ══════════════════════════════════════════════════════════════════════════════
if MID_MONTH_MODE:
    print(f"\n{'='*70}")
    print("MID_MONTH MODE — RSI filter + replacement (writes portfolio_state)")
    print(f"{'='*70}")

    if stored_last_rebalance_date is None:
        print("No last_rebalance_date found. Run REBALANCE first.")
        sys.exit(1)

    # ── Check trading day count ───────────────────────────────────────────────
    td_since = trading_days_since(stored_last_rebalance_date)
    n_days   = len(td_since)
    print(f"\nTrading days since last rebalance ({stored_last_rebalance_date.date()}): {n_days}")

    if n_days < CHECK_DAY:
        print(f"Mid-month check not yet due. Need {CHECK_DAY} trading days, "
              f"currently at day {n_days}.")
        print(f"Next eligible: approximately {CHECK_DAY - n_days} trading day(s) away.")
        sys.exit(0)

    print(f"Day {n_days} of holding period — mid-month RSI check running.")

    # ── Load prices and compute RSI for all held stocks ───────────────────────
    print(f"\nLoading prices for RSI computation ...")
    prices_by_sym = load_prices_by_sym()
    today         = run_date

    rsi_results = {}
    for sym in current_holdings:
        rsi_results[sym] = compute_wilder_rsi(sym, prices_by_sym, today)

    print(f"\nRSI computed for {len(rsi_results)} held stocks:")
    for sym, rsi in sorted(rsi_results.items()):
        flag = ' ← EXIT' if pd.notna(rsi) and rsi < RSI_EXIT_THRESH else ''
        print(f"  {sym:20s}  RSI={rsi}{flag}")

    # ── Identify RSI exits ────────────────────────────────────────────────────
    mid_sells = {
        sym for sym, rsi in rsi_results.items()
        if pd.notna(rsi) and rsi < RSI_EXIT_THRESH
    }
    print(f"\nMID_SELL ({len(mid_sells)}): {sorted(mid_sells)}")

    if not mid_sells:
        print("\nNo RSI exits triggered. Portfolio unchanged.")
        stocks_json = []
        for sym in sorted(current_holdings):
            rsi = rsi_results.get(sym, None)
            stocks_json.append({
                'symbol': sym,
                'action': 'HOLD',
                'rsi'   : round(rsi, 1) if rsi and pd.notna(rsi) else None,
            })
        print('<<<MONITOR_JSON_START>>>')
        print(json.dumps({
            'mode'             : 'mid_month',
            'as_of'            : as_of_date.strftime('%d %b %Y'),
            'run_date'         : run_date.strftime('%d %b %Y'),
            'rebal_date'       : stored_last_rebalance_date.strftime('%d %b %Y'),
            'trading_day'      : n_days,
            'rsi_exit_thresh'  : RSI_EXIT_THRESH,
            'rsi_entry_thresh' : RSI_ENTRY_THRESH,
            'n_exits'          : 0,
            'n_replaced'       : 0,
            'n_cash'           : 0,
            'stocks'           : stocks_json,
        }))
        print('<<<MONITOR_JSON_END>>>')
        sys.exit(0)

    # ── Load watchlist from latest portfolio_recommendations ──────────────────
    port_files = sorted(STAGE6_OUTPUT_DIR.glob("portfolio_recommendations_*.parquet"))
    # Exclude _mid files
    port_files = [f for f in port_files if '_mid' not in f.name]

    if not port_files:
        print("ERROR: No portfolio_recommendations_*.parquet found. "
              "Cannot determine watchlist.")
        sys.exit(1)

    last_port_df = pd.read_parquet(port_files[-1])
    watchlist_df = (
        last_port_df[last_port_df['action'] == 'WATCHLIST']
        .sort_values('mr_rank')
        .reset_index(drop=True)
    )
    print(f"\nWatchlist loaded from {port_files[-1].name}: "
          f"{len(watchlist_df)} candidates")

    # ── Find replacements ─────────────────────────────────────────────────────
    mid_buys   = []
    cash_slots = []

    for sym in sorted(mid_sells):
        found = False
        for _, wrow in watchlist_df.iterrows():
            candidate = wrow['symbol']
            # Skip if already in portfolio or already selected as replacement
            if candidate in current_holdings:
                continue
            if candidate in [b['symbol'] for b in mid_buys]:
                continue
            rsi = compute_wilder_rsi(candidate, prices_by_sym, today)
            if pd.notna(rsi) and rsi > RSI_ENTRY_THRESH:
                mid_buys.append({
                    'symbol'    : candidate,
                    'mr_rank'   : int(wrow['mr_rank']),
                    'rsi_today' : rsi,
                    'replaces'  : sym,
                })
                print(f"  MID_BUY: {candidate} (rank={int(wrow['mr_rank'])}, "
                      f"RSI={rsi:.1f}) → replaces {sym}")
                found = True
                break
        if not found:
            cash_slots.append(sym)
            print(f"  No replacement for {sym} (RSI > {RSI_ENTRY_THRESH} "
                  f"not found in watchlist) → cash")

    print(f"\nSummary: {len(mid_sells)} exits | "
          f"{len(mid_buys)} replaced | {len(cash_slots)} to cash")

    # ── Update portfolio_state.parquet ────────────────────────────────────────
    # Remove exits, add replacements
    remaining = portfolio_state[
        ~portfolio_state['symbol'].isin(mid_sells)
    ].copy()

    new_rows = []
    for b in mid_buys:
        new_rows.append({
            'symbol'              : b['symbol'],
            'last_rebalance_date' : stored_last_rebalance_date,
            'entry_date'          : today,
            'entry_type'          : 'MID',
        })

    if new_rows:
        new_df = pd.DataFrame(new_rows)
        updated_state = pd.concat([remaining, new_df], ignore_index=True)
    else:
        updated_state = remaining

    updated_state.to_parquet(PORTFOLIO_STATE_PATH, index=False)
    print(f"\nPortfolio state updated: {len(updated_state)} holdings")
    print(f"  Removed : {sorted(mid_sells)}")
    print(f"  Added   : {[b['symbol'] for b in mid_buys]}")
    print(f"  Written : {PORTFOLIO_STATE_PATH}")

    # ── Write portfolio_recommendations_{DDMMYYYY}_mid.parquet ────────────────
    mid_rows = []

    # HOLD rows (remaining holdings)
    for sym in sorted(set(updated_state['symbol']) - {b['symbol'] for b in mid_buys}):
        rsi = rsi_results.get(sym, np.nan)
        mid_rows.append({
            'symbol'    : sym,
            'action'    : 'HOLD',
            'tier'      : 'TOP_25',
            'rsi_today' : rsi,
            'as_of_date': as_of_date,
            'run_date'  : run_date,
            'entry_type': 'HOLD',
        })

    # MID_SELL rows
    for sym in sorted(mid_sells):
        mid_rows.append({
            'symbol'    : sym,
            'action'    : 'MID_SELL',
            'tier'      : 'MID_SELL',
            'rsi_today' : rsi_results.get(sym, np.nan),
            'as_of_date': as_of_date,
            'run_date'  : run_date,
            'entry_type': 'MID_SELL',
        })

    # MID_BUY rows
    for b in mid_buys:
        mid_rows.append({
            'symbol'    : b['symbol'],
            'action'    : 'MID_BUY',
            'tier'      : 'TOP_25',
            'mr_rank'   : b['mr_rank'],
            'rsi_today' : b['rsi_today'],
            'as_of_date': as_of_date,
            'run_date'  : run_date,
            'entry_type': 'MID',
        })

    mid_df   = pd.DataFrame(mid_rows)
    mid_path = STAGE6_OUTPUT_DIR / f"portfolio_recommendations_{run_date_ddmmyyyy}_mid.parquet"
    mid_df.to_parquet(mid_path, index=False)
    print(f"\nMid-month recommendations written: {mid_path}")

    # ── JSON for Telegram ─────────────────────────────────────────────────────
    stocks_json = []

    for sym in sorted(set(updated_state['symbol']) - {b['symbol'] for b in mid_buys}):
        rsi = rsi_results.get(sym, None)
        stocks_json.append({
            'symbol': sym,
            'action': 'HOLD',
            'rsi'   : round(rsi, 1) if rsi and pd.notna(rsi) else None,
        })
    for sym in sorted(mid_sells):
        rsi = rsi_results.get(sym, None)
        stocks_json.append({
            'symbol': sym,
            'action': 'MID_SELL',
            'rsi'   : round(rsi, 1) if rsi and pd.notna(rsi) else None,
        })
    for b in mid_buys:
        stocks_json.append({
            'symbol'  : b['symbol'],
            'action'  : 'MID_BUY',
            'mr_rank' : b['mr_rank'],
            'rsi'     : round(b['rsi_today'], 1),
            'replaces': b['replaces'],
        })

    print('<<<MONITOR_JSON_START>>>')
    print(json.dumps({
        'mode'          : 'mid_month',
        'as_of'         : as_of_date.strftime('%d %b %Y'),
        'run_date'      : run_date.strftime('%d %b %Y'),
        'rebal_date'    : stored_last_rebalance_date.strftime('%d %b %Y'),
        'trading_day'   : n_days,
        'rsi_exit_thresh' : RSI_EXIT_THRESH,
        'rsi_entry_thresh': RSI_ENTRY_THRESH,
        'n_exits'       : len(mid_sells),
        'n_replaced'    : len(mid_buys),
        'n_cash'        : len(cash_slots),
        'stocks'        : stocks_json,
    }))
    print('<<<MONITOR_JSON_END>>>')

    print(f"\nMID_MONTH complete.")
    print("=" * 70)
    sys.exit(0)

# ══════════════════════════════════════════════════════════════════════════════
# REBALANCE + MONITOR — shared scoring step
# ══════════════════════════════════════════════════════════════════════════════

# 30-day guard (REBALANCE only)
if REBALANCE_MODE and stored_last_rebalance_date is not None:
    days_since = (pd.Timestamp.now().normalize() - stored_last_rebalance_date).days
    print(f"Days since last rebalance: {days_since}")
    if days_since < REBALANCE_DAYS:
        print(f"\n30-day guard: only {days_since} days since last rebalance. "
              f"Need {REBALANCE_DAYS - days_since} more days.")
        print("Skipping — no files written, no state changed.")
        print("Tip: run with STAGE6_MODE=monitor to see current rankings.")
        sys.exit(0)
    else:
        print(f"{days_since} days since last rebalance — proceeding.")

# ── Score + reconstitute ──────────────────────────────────────────────────────
ranked_df, gate_rejects            = apply_mr_score(signals)
ranked, top25_symbols, wein_rejects = apply_reconstitution(ranked_df, current_holdings)

# ── Beta ──────────────────────────────────────────────────────────────────────
if MONITOR_MODE:
    sell_symbols = current_holdings - set(top25_symbols)
    beta_symbols = set(top25_symbols) | sell_symbols
    beta_result  = compute_beta(beta_symbols, as_of_date,
                                portfolio_symbols=set(top25_symbols))
else:
    beta_result  = compute_beta(set(top25_symbols), as_of_date)

beta_df     = beta_to_df(beta_result, as_of_date)
beta_fields = beta_for_assembly(beta_result)

# ══════════════════════════════════════════════════════════════════════════════
# MONITOR MODE (read-only — identical to stage6_assemble.py)
# ══════════════════════════════════════════════════════════════════════════════
if MONITOR_MODE:
    print(f"\n{'='*70}")
    print("MONITOR MODE — current state vs last rebalance (nothing written)")
    print(f"{'='*70}")

    print(f"\nCurrent holdings rank status ({len(current_holdings)} stocks):")
    if current_holdings:
        holding_rows = ranked_df[ranked_df['symbol'].isin(current_holdings)].copy()
        holding_rows = holding_rows.sort_values('mr_rank')
        cols = [c for c in ['mr_rank','symbol','norm_momentum_score',
                             'weighted_z','weinstein_stage2','ret_12m1m']
                if c in holding_rows.columns]
        print(holding_rows[cols].to_string(index=False))
        would_force_out = holding_rows[holding_rows['mr_rank'] > BUFFER_ZONE]
        if len(would_force_out) > 0:
            print(f"\n  ⚠ Would be forced out (rank > {BUFFER_ZONE}): "
                  f"{sorted(would_force_out['symbol'].tolist())}")

    print(f"\nIf rebalanced TODAY:")
    top25_df   = ranked[ranked['tier'] == 'TOP_25'].copy()
    sell_df    = ranked[ranked['action'] == 'SELL'].copy()
    display_df = pd.concat([top25_df, sell_df], ignore_index=True)
    if 'mr_rank' in display_df.columns:
        display_df = display_df.sort_values('mr_rank')

    display_df['beta_12m']      = display_df['symbol'].map(beta_fields['stock_beta'])
    display_df['stock_12m_ret'] = display_df['symbol'].map(beta_fields['stock_return'])
    display_df['alpha_12m']     = display_df['symbol'].map(beta_fields['stock_alpha'])

    port_files = sorted(STAGE6_OUTPUT_DIR.glob("portfolio_recommendations_*.parquet"))
    port_files = [f for f in port_files if '_mid' not in f.name]
    if port_files:
        last_port_df = pd.read_parquet(port_files[-1])
        rsi_at_rebal = (
            last_port_df[last_port_df['tier'] == 'TOP_25']
            .set_index('symbol')['rsi_14']
            .to_dict()
        )
        rebal_date = pd.to_datetime(last_port_df['as_of_date'].iloc[0]).date()
        display_df['rsi_14_rebal'] = display_df['symbol'].map(rsi_at_rebal)

        buy_mask    = display_df['action'] == 'BUY'
        buy_symbols = display_df.loc[buy_mask, 'symbol'].tolist()
        if buy_symbols:
            _px       = pd.read_parquet(PRICES_PATH)
            _rebal_ts = pd.Timestamp(rebal_date)
            for _sym in buy_symbols:
                _s = _px[_px['symbol'] == _sym].sort_values('date')
                _s = _s[_s['date'] <= _rebal_ts].tail(30)
                if len(_s) < 15:
                    continue
                _delta = _s['close'].diff()
                _gain  = _delta.clip(lower=0)
                _loss  = (-_delta).clip(lower=0)
                _avg_g = _gain.ewm(com=13, min_periods=14).mean()
                _avg_l = _loss.ewm(com=13, min_periods=14).mean()
                _rs    = _avg_g / _avg_l.replace(0, float('nan'))
                _rsi   = 100 - (100 / (1 + _rs))
                display_df.loc[display_df['symbol'] == _sym,
                               'rsi_14_rebal'] = round(_rsi.iloc[-1], 4)

        display_df['rsi_14_today'] = display_df['rsi_14']
        display_df['rsi_chg']      = (
            display_df['rsi_14_today'] - display_df['rsi_14_rebal']
        ).round(1)
        rsi_cols = ['rsi_14_rebal','rsi_14_today','rsi_chg']
        print(f"  RSI_14 at rebalance ({rebal_date}) vs today")
    else:
        rsi_cols = ['rsi_14']

    cols = [c for c in ['mr_rank','symbol','action','norm_momentum_score',
                         'ret_12m1m'] + rsi_cols + ['beta_12m','stock_12m_ret','alpha_12m']
            if c in display_df.columns]
    print(display_df[cols].to_string(index=False))
    print(f"\n  Portfolio beta (12m) : {beta_fields['portfolio_beta']:.3f}")
    print(f"  Market 12m return    : {beta_fields['market_return']:.2%}")

    _monitor_stocks = []
    for _, _r in display_df.iterrows():
        _monitor_stocks.append({
            'rank'     : int(_r['mr_rank']) if pd.notna(_r.get('mr_rank')) else 999,
            'symbol'   : str(_r['symbol']),
            'action'   : str(_r['action']),
            'score'    : round(float(_r['norm_momentum_score']), 2) if pd.notna(_r.get('norm_momentum_score')) else None,
            'ret12m'   : round(float(_r['ret_12m1m']), 4)           if pd.notna(_r.get('ret_12m1m'))          else None,
            'rsi_rebal': round(float(_r['rsi_14_rebal']), 1) if 'rsi_14_rebal' in _r and pd.notna(_r['rsi_14_rebal']) else None,
            'rsi_today': round(float(_r['rsi_14_today']), 1) if 'rsi_14_today' in _r and pd.notna(_r['rsi_14_today']) else None,
            'rsi_chg'  : round(float(_r['rsi_chg']), 1)     if 'rsi_chg'      in _r and pd.notna(_r['rsi_chg'])      else None,
            'beta'     : round(float(_r['beta_12m']), 2)     if 'beta_12m'     in _r and pd.notna(_r['beta_12m'])     else None,
            'alpha'    : round(float(_r['alpha_12m']), 4)    if 'alpha_12m'    in _r and pd.notna(_r['alpha_12m'])    else None,
        })
    print('<<<MONITOR_JSON_START>>>')
    print(json.dumps({
        'as_of'      : as_of_date.strftime('%d %b %Y'),
        'rebal_date' : str(rebal_date) if port_files else None,
        'port_beta'  : round(float(beta_fields['portfolio_beta']), 2),
        'mkt_ret'    : round(float(beta_fields['market_return']), 4),
        'stocks'     : _monitor_stocks,
    }))
    print('<<<MONITOR_JSON_END>>>')

    projected_top25 = set(top25_df['symbol'])
    would_buy       = projected_top25 - current_holdings
    would_sell      = current_holdings - projected_top25
    would_hold      = projected_top25 & current_holdings
    print(f"\n  Would BUY  ({len(would_buy)})  : {sorted(would_buy)}")
    print(f"  Would HOLD ({len(would_hold)}) : {sorted(would_hold)}")
    print(f"  Would SELL ({len(would_sell)}) : {sorted(would_sell)}")

    watchlist = ranked[ranked['action'] == 'WATCHLIST']
    if len(watchlist) > 0:
        print(f"\nWATCHLIST (rank <= {BUFFER_ZONE}, {len(watchlist)} symbols):")
        wl_cols = [c for c in ['mr_rank','symbol','weinstein_stage2','norm_momentum_score']
                   if c in watchlist.columns]
        print(watchlist[wl_cols].to_string(index=False))

    if len(gate_rejects) > 0:
        print(f"\nG6 gate rejects ({len(gate_rejects)}):")
        print(gate_rejects[['symbol','rejection_reason']].to_string(index=False))

    if len(wein_rejects) > 0:
        print(f"\nWeinstein rejects ({len(wein_rejects)}):")
        print(wein_rejects[['symbol','mr_rank','rejection_stage',
                             'rejection_reason']].to_string(index=False))

    print(f"\nMONITOR complete. Next rebalance eligible after: "
          f"{(stored_last_rebalance_date + pd.Timedelta(days=REBALANCE_DAYS)).date() if stored_last_rebalance_date else 'immediately'}")
    print("=" * 70)
    sys.exit(0)

# ══════════════════════════════════════════════════════════════════════════════
# REBALANCE MODE — full write (extended portfolio_state schema)
# ══════════════════════════════════════════════════════════════════════════════

extra_cols = [
    'symbol',
    'rsi_14','rsi_7',
    'dist_ema_20','dist_ema_50',
    'mfi_14',
    'stoch_rsi_k','stoch_rsi_d',
    'bb_pct_b',
    'bb_bandwidth_curr_wk','bb_bandwidth_prev_wk',
    'bb_bandwidth_prev_2wk','bb_bandwidth_prev_3wk','bb_squeeze',
]
cols_to_merge = [c for c in extra_cols if c == 'symbol' or c not in ranked.columns]
if len(cols_to_merge) > 1:
    ranked = ranked.merge(signals[cols_to_merge], on='symbol', how='left',
                          suffixes=('','_dup'))
    ranked = ranked[[c for c in ranked.columns if not c.endswith('_dup')]]

ranked['beta_12m']       = ranked['symbol'].map(beta_fields['stock_beta'])
ranked['stock_12m_ret']  = ranked['symbol'].map(beta_fields['stock_return'])
ranked['alpha_12m']      = ranked['symbol'].map(beta_fields['stock_alpha'])
ranked['market_12m_ret'] = beta_fields['market_return']
ranked['portfolio_beta'] = beta_fields['portfolio_beta']
ranked['run_date']       = run_date
ranked['as_of_date']     = as_of_date

# Fully missing SELL rows
already_in_output = set(ranked['symbol'])
fully_missing     = current_holdings - already_in_output
if fully_missing:
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

# Write recommendations
output_path = STAGE6_OUTPUT_DIR / f"portfolio_recommendations_{run_date_ddmmyyyy}.parquet"
action_order = {'BUY': 0, 'HOLD': 1, 'WATCHLIST': 2, 'SELL': 3}
ranked['_sort_key'] = ranked['action'].map(action_order).fillna(99)
ranked = ranked.sort_values(['_sort_key','mr_rank']).drop(columns=['_sort_key'])
ranked.to_parquet(output_path, index=False)
print(f"\nRecommendations written : {output_path}")

# Write reject tracker
gate_rejects['as_of_date']  = as_of_date
wein_rejects['as_of_date']  = as_of_date
all_rejects = pd.concat([gate_rejects, wein_rejects], ignore_index=True)
all_rejects['run_date'] = run_date
if len(all_rejects) > 0:
    rejects_path = STAGE6_OUTPUT_DIR / f"rejects_{run_date_ddmmyyyy}.csv"
    all_rejects.to_csv(rejects_path, index=False)
    print(f"Reject tracker written  : {rejects_path}")

# ── Write portfolio_state.parquet (extended schema) ───────────────────────────
new_portfolio_state = pd.DataFrame({
    'symbol'              : sorted(top25_symbols),
    'last_rebalance_date' : as_of_date,
    'entry_date'          : as_of_date,
    'entry_type'          : 'SOM',
})
new_portfolio_state.to_parquet(PORTFOLIO_STATE_PATH, index=False)
print(f"Portfolio state updated : {len(new_portfolio_state)} holdings | "
      f"last_rebalance_date={as_of_date.date()} | entry_type=SOM")

# Write history snapshot
history_path = PORTFOLIO_HISTORY_DIR / f"portfolio_{run_date_ddmmyyyy}.parquet"
new_portfolio_state.to_parquet(history_path, index=False)
print(f"History snapshot written: {history_path}")

# Summary
top25_out = ranked[ranked['tier'] == 'TOP_25'].copy()
if 'mr_rank' in top25_out.columns:
    top25_out = top25_out.sort_values('mr_rank')
print(f"\n{'='*70}")
print(f"Final TOP_25 ({len(top25_out)} symbols):")
display_cols = [c for c in ['mr_rank','symbol','action','weinstein_stage2',
                             'norm_momentum_score','weighted_z',
                             'ret_12m1m','ret_6m1m','vol_252']
                if c in top25_out.columns]
print(top25_out[display_cols].to_string(index=False))

watchlist = ranked[ranked['action'] == 'WATCHLIST']
if len(watchlist) > 0:
    print(f"\nWATCHLIST (rank <= {BUFFER_ZONE}, {len(watchlist)} symbols):")
    wl_cols = [c for c in ['mr_rank','symbol','weinstein_stage2','norm_momentum_score']
               if c in watchlist.columns]
    print(watchlist[wl_cols].to_string(index=False))

sells = ranked[ranked['action'] == 'SELL']
if len(sells) > 0:
    print(f"\nSELL ({len(sells)} symbols): {sorted(sells['symbol'].tolist())}")

print(f"\n  Portfolio beta (12m) : {beta_fields['portfolio_beta']:.3f}")
print(f"  Market 12m return    : {beta_fields['market_return']:.2%}")
print(f"\nStage 6 RSI Overlay complete.")
print("=" * 70)
