"""
MR_M V3_RSI_SIM — Full End-to-End Simulation
=============================================
Single unified simulation combining:
  1. Monthly momentum reconstitution at start of month (identical to v3_backtest.py)
  2. Day-15 RSI mid-month check: exit weak holdings, replace with strong watchlist stocks

This is the live simulation equivalent of running v3_backtest.py followed by
v3_rsi_replacement_overlay.py, but executed as one coherent portfolio simulation
using PortfolioState throughout — no post-processing.

Start-of-month (SOM):
  Identical to v3_backtest.py: 60/40 momentum scoring, buffer zone=38,
  forced-in top-12, fill to 25. Execute at Monday open.

Mid-month (MID):
  On 15th trading day of holding period, for every held stock:
    - Compute RSI using closes up to that day
    - If RSI < RSI_EXIT_THRESH → exit at day-16 open
    - Replace with best watchlist stock (ranked <= BUFFER_ZONE, not in portfolio)
      that has RSI > RSI_ENTRY_THRESH at same day-15 date
    - If no eligible replacement → freed slot goes to cash until next SOM

Configurable:
  RSI_EXIT_THRESH  = 50
  RSI_ENTRY_THRESH = 50
  CHECK_DAY        = 10   # ~15 calendar days = ~10 trading days

Benchmarks:
  V3 Monthly gross             : CAGR 34.12% | Sharpe 1.197 | MaxDD -35.08%
  V3 overlay to cash (net)     : CAGR 37.83% | Sharpe 1.484 | MaxDD -19.82%
  V3 replacement overlay (net) : CAGR 38.02% | Sharpe 1.454 | MaxDD -19.55%

Run from repo root:
  cd /home/ec2-user/nse-factor-engine
  python3 backtest/v3_rsi_replacement_sim.py
"""

import sys, os, warnings, gc, time
import pandas as pd
import numpy as np
warnings.filterwarnings('ignore')
sys.path.insert(0, '/home/ec2-user/nse-factor-engine')

from backtest.simulation.portfolio import PortfolioState

# ── Constants ─────────────────────────────────────────────────────────────────
PORTFOLIO_N      = 25
FORCED_IN_N      = 12
BUFFER_ZONE      = 38
INITIAL_CAPITAL  = 10_000_000.0
W_12M, W_6M      = 0.60, 0.40
CHECK_DAY        = 10   # ~15 calendar days = ~10 trading days
RSI_EXIT_THRESH  = 50    # exit held stock if day-15 RSI < this
RSI_ENTRY_THRESH = 50    # replacement must have day-15 RSI > this
VARIANT_LABEL    = 'MR_M_V3_RSI_SIM'

BASE        = 'backtest'
FRI_SIG_DIR = f'{BASE}/signals/historical'
PRICES_PATH = f'{BASE}/data/prices_backtest.parquet'
BENCH_PATH  = f'{BASE}/data/benchmark/nifty500_weekly.parquet'
RESULTS_DIR = f'{BASE}/results'
RUN_DATE    = time.strftime('%d%m%Y')

# ── Load prices ───────────────────────────────────────────────────────────────
print('Loading prices ...')
prices = pd.read_parquet(PRICES_PATH)
prices['date'] = pd.to_datetime(prices['date'])

open_by_date = {
    pd.Timestamp(date): grp.set_index('symbol')['open'].to_dict()
    for date, grp in prices.groupby('date')
}
all_trading_days = sorted(open_by_date.keys())
print(f'  {len(all_trading_days)} trading days  '
      f'({prices["date"].min().date()} -> {prices["date"].max().date()})')

print('Building prices_by_sym for RSI ...')
prices_by_sym = {
    sym: grp[['date', 'close']].sort_values('date').reset_index(drop=True)
    for sym, grp in prices.groupby('symbol')
}
print(f'  {len(prices_by_sym)} symbols loaded')
del prices; gc.collect()

print('Loading benchmark ...')
bench = pd.read_parquet(BENCH_PATH).set_index('date')['close']
bench.index = pd.DatetimeIndex(bench.index)

# ── RSI (identical to v3_rsi50_overlay.py) ───────────────────────────────────
def wilder_rsi(closes):
    if len(closes) < 15:
        return np.nan
    deltas   = np.diff(closes[-15:])
    gains    = np.where(deltas > 0, deltas, 0.0)
    losses   = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = gains.mean()
    avg_loss = losses.mean()
    if avg_loss == 0:
        return 100.0
    return 100 - (100 / (1 + avg_gain / avg_loss))

def compute_rsi(sym, up_to_date):
    sym_df = prices_by_sym.get(sym)
    if sym_df is None:
        return np.nan
    closes = sym_df[sym_df['date'] <= up_to_date]['close'].values
    return wilder_rsi(closes)

# ── Scoring (identical to v3_backtest.py) ────────────────────────────────────
def compute_mr_scores(signals_df):
    df = signals_df[signals_df['in_universe'] == True].copy()
    bad = (
        df['vol_252'].isna()   | (df['vol_252'] == 0) |
        df['ret_12m1m'].isna() |
        df['ret_6m1m'].isna()
    )
    df = df[~bad].copy()
    if len(df) < 10:
        return pd.DataFrame()
    df['mr_12'] = df['ret_12m1m'] / df['vol_252']
    df['mr_6']  = df['ret_6m1m']  / df['vol_252']
    df['z_12']  = (df['mr_12'] - df['mr_12'].mean()) / df['mr_12'].std(ddof=1)
    df['z_6']   = (df['mr_6']  - df['mr_6'].mean())  / df['mr_6'].std(ddof=1)
    df['weighted_z'] = W_12M * df['z_12'] + W_6M * df['z_6']
    df['norm_momentum_score'] = df['weighted_z'].apply(
        lambda wz: 1 + wz if wz >= 0 else 1.0 / (1.0 - wz)
    )
    df['mr_rank'] = (
        df['norm_momentum_score']
        .rank(method='min', ascending=False)
        .astype('Int64')
    )
    return df.sort_values('mr_rank').reset_index(drop=True)

# ── Reconstitution (identical to v3_backtest.py + returns watchlist) ──────────
def reconstitute(ranked_df, current_holdings):
    """
    Returns: (portfolio set, action_map dict, watchlist_df)

    watchlist_df : stocks ranked <= BUFFER_ZONE not in portfolio, sorted by
                   mr_rank. This is the replacement pool for mid-month RSI exits.
    """
    if ranked_df.empty:
        return set(), {}, pd.DataFrame()

    all_scored        = set(ranked_df['symbol'])
    rank_lookup       = dict(zip(ranked_df['symbol'], ranked_df['mr_rank'].astype(int)))
    score_lookup      = dict(zip(ranked_df['symbol'], ranked_df['norm_momentum_score']))
    unscored_holdings = current_holdings - all_scored

    portfolio  = {s for s in current_holdings
                  if s in all_scored and rank_lookup[s] <= BUFFER_ZONE}
    forced_out = (current_holdings - portfolio) | unscored_holdings

    forced_in_candidates = ranked_df[
        (ranked_df['mr_rank'] <= FORCED_IN_N) &
        (~ranked_df['symbol'].isin(current_holdings))
    ].sort_values('mr_rank')

    for _, row in forced_in_candidates.iterrows():
        new_stock        = row['symbol']
        holdings_in_port = portfolio & current_holdings
        if holdings_in_port:
            weakest = min(holdings_in_port, key=lambda s: score_lookup.get(s, 0))
            portfolio.discard(weakest)
            forced_out.add(weakest)
        portfolio.add(new_stock)

    slots_remaining = PORTFOLIO_N - len(portfolio)
    if slots_remaining > 0:
        fill_pool = ranked_df[
            ~ranked_df['symbol'].isin(portfolio) &
            ~ranked_df['symbol'].isin(current_holdings)
        ].sort_values('mr_rank')
        for _, row in fill_pool.iterrows():
            if slots_remaining == 0:
                break
            portfolio.add(row['symbol'])
            slots_remaining -= 1

    action_map = {}
    for s in portfolio:
        action_map[s] = 'HOLD' if s in current_holdings else 'BUY'
    for s in forced_out:
        if s not in portfolio:
            action_map[s] = 'SELL'
    for s in all_scored:
        if s not in action_map and rank_lookup[s] <= BUFFER_ZONE:
            action_map[s] = 'WATCHLIST'

    # Replacement pool: ranked <= BUFFER_ZONE, not in final portfolio
    watchlist_df = ranked_df[
        (ranked_df['mr_rank'] <= BUFFER_ZONE) &
        (~ranked_df['symbol'].isin(portfolio))
    ].sort_values('mr_rank').reset_index(drop=True)

    return portfolio, action_map, watchlist_df

# ── Activity enrichment ───────────────────────────────────────────────────────
def enrich(activity, sig_date, action_map, mr_meta, rebal_type):
    for row in activity:
        sym = row['symbol']
        row['signal_date']         = sig_date
        row['rebal_type']          = rebal_type   # 'SOM' or 'MID'
        row['mr_action']           = action_map.get(sym, row['action'])
        meta = mr_meta.get(sym, {})
        row['mr_rank']             = meta.get('mr_rank', np.nan)
        row['norm_momentum_score'] = meta.get('norm_momentum_score', np.nan)
        row['weighted_z']          = meta.get('weighted_z', np.nan)
    return activity

# ── Helpers ───────────────────────────────────────────────────────────────────
def parse_date(fname):
    d = fname.replace('signals_', '').replace('.parquet', '')
    return pd.Timestamp(f'{d[4:8]}-{d[2:4]}-{d[0:2]}')

def next_trading_day(dt):
    for td in all_trading_days:
        if td > dt:
            return td
    return None

def to_monthly_pairs(pairs):
    monthly = {}
    for sig_date, exec_date, path in pairs:
        key = (exec_date.year, exec_date.month)
        monthly[key] = (sig_date, exec_date, path)
    return sorted(monthly.values(), key=lambda x: x[1])

# ── Build monthly pairs ───────────────────────────────────────────────────────
print('Building monthly pairs ...')
fri_files = sorted(
    f for f in os.listdir(FRI_SIG_DIR)
    if f.startswith('signals_') and f.endswith('.parquet')
    and os.path.isfile(os.path.join(FRI_SIG_DIR, f))
)
all_pairs = []
for fname in fri_files:
    try:
        sig_date  = parse_date(fname)
        exec_date = next_trading_day(sig_date)
        if exec_date and open_by_date.get(exec_date):
            all_pairs.append((sig_date, exec_date,
                              os.path.join(FRI_SIG_DIR, fname)))
    except:
        continue
all_pairs.sort(key=lambda x: x[1])
monthly_pairs = to_monthly_pairs(all_pairs)
print(f'  {len(monthly_pairs)} monthly periods  '
      f'({monthly_pairs[0][1].date()} -> {monthly_pairs[-1][1].date()})')

# ── Run simulation ────────────────────────────────────────────────────────────
print(f'\nRunning {VARIANT_LABEL} '
      f'(RSI_EXIT={RSI_EXIT_THRESH}, RSI_ENTRY={RSI_ENTRY_THRESH}) ...')
state           = PortfolioState(initial_capital=INITIAL_CAPITAL)
som_nav_path    = []    # month-start NAVs only — used for metrics
all_activity    = []
total_rsi_exits = 0
total_replaced  = 0
t0              = time.time()

for i, (sig_date, exec_date, sig_path) in enumerate(monthly_pairs):

    # ══ START OF MONTH REBALANCE ══════════════════════════════════════════════
    signals          = pd.read_parquet(sig_path)
    exec_px          = open_by_date[exec_date]
    ranked_df        = compute_mr_scores(signals)
    current_holdings = set(state.holdings.keys())

    top25, action_map, watchlist_df = reconstitute(ranked_df, current_holdings)

    nav_post, nav_pre, activity = state.rebalance(
        list(top25), exec_px, exec_date, VARIANT_LABEL
    )

    mr_meta = {}
    if not ranked_df.empty:
        for _, row in ranked_df.iterrows():
            mr_meta[row['symbol']] = {
                'mr_rank'            : row['mr_rank'],
                'norm_momentum_score': row['norm_momentum_score'],
                'weighted_z'         : row['weighted_z'],
            }

    all_activity.extend(enrich(activity, sig_date, action_map, mr_meta, 'SOM'))
    som_nav_path.append((sig_date, exec_date, nav_pre, nav_post))

    # ══ MID-MONTH RSI CHECK ═══════════════════════════════════════════════════
    # Skip for last period (no next exec date to define the holding window)
    if i >= len(monthly_pairs) - 1:
        gc.collect()
        continue

    next_exec  = monthly_pairs[i + 1][1]
    intra_days = [td for td in all_trading_days if exec_date <= td < next_exec]

    if len(intra_days) < CHECK_DAY:
        gc.collect()
        continue

    d15_date = intra_days[CHECK_DAY - 1]
    d16_date = intra_days[CHECK_DAY] if len(intra_days) > CHECK_DAY else intra_days[-1]
    d16_px   = open_by_date.get(d16_date, {})

    # Compute RSI at d15_date for all current holdings
    current_mid = set(state.holdings.keys())
    rsi_exits   = set()
    for sym in current_mid:
        rsi = compute_rsi(sym, d15_date)
        if pd.notna(rsi) and rsi < RSI_EXIT_THRESH:
            rsi_exits.add(sym)

    if not rsi_exits:
        gc.collect()
        continue

    # Find replacements from watchlist (RSI > entry thresh at same d15_date)
    replacements = []
    for _, wrow in watchlist_df.iterrows():
        if len(replacements) >= len(rsi_exits):
            break
        sym = wrow['symbol']
        if sym in current_mid:
            continue
        rsi = compute_rsi(sym, d15_date)
        if pd.isna(rsi) or rsi <= RSI_ENTRY_THRESH:
            continue
        if d16_px.get(sym, 0) <= 0:
            continue
        replacements.append(sym)

    # Build mid-month portfolio and action map
    mid_portfolio  = (current_mid - rsi_exits) | set(replacements)
    mid_action_map = {}
    for s in mid_portfolio:
        mid_action_map[s] = 'MID_HOLD' if s in current_mid else 'MID_BUY'
    for s in rsi_exits:
        mid_action_map[s] = 'MID_SELL_RSI'

    # Execute mid-month rebalance via PortfolioState
    # Value is conserved: sell proceeds fund replacement buys
    _, _, mid_activity = state.rebalance(
        list(mid_portfolio), d16_px, d16_date, VARIANT_LABEL
    )
    all_activity.extend(enrich(mid_activity, sig_date, mid_action_map, mr_meta, 'MID'))

    total_rsi_exits += len(rsi_exits)
    total_replaced  += len(replacements)

    if (i+1) % 20 == 0 or i == 0 or (i+1) == len(monthly_pairs):
        elapsed   = time.time() - t0
        remaining = elapsed / (i+1) * (len(monthly_pairs) - i - 1)
        print(f'  [{i+1:03d}/{len(monthly_pairs)}] '
              f'sig={sig_date.date()} exec={exec_date.date()} '
              f'NAV=Rs{nav_post/1e6:.3f}M | '
              f'RSI exits={total_rsi_exits} replaced={total_replaced} | '
              f'{elapsed:.0f}s elapsed {remaining:.0f}s ETA', flush=True)
    gc.collect()

# ── NAV series (month-start only → clean monthly period returns) ───────────────
exec_dates = [d for _, d, _, _ in som_nav_path]
navs       = [v for _, _, _, v in som_nav_path]
nav_s      = pd.Series(navs)
wrets      = nav_s.pct_change().values
wrets[0]   = 0.0
cum_ret    = (1 + pd.Series(wrets)).cumprod() - 1

bm_vals = []
for dt in exec_dates:
    bv = bench.get(dt, np.nan)
    if pd.isna(bv):
        prior = bench[:dt]
        bv    = prior.iloc[-1] if len(prior) else np.nan
    bm_vals.append(bv)
bm_ret         = pd.Series(bm_vals).pct_change()
bm_ret.iloc[0] = 0.0

returns_df = pd.DataFrame({
    'signal_friday' : [d for d, _, _, _ in som_nav_path],
    'exec_date'     : pd.to_datetime(exec_dates),
    'nav'           : navs,
    'period_ret'    : wrets,
    'cum_ret'       : cum_ret.values,
    'benchmark_ret' : bm_ret.values,
})
activity_df = pd.DataFrame(all_activity)

# ── Gross metrics ─────────────────────────────────────────────────────────────
rets         = returns_df['period_ret'].iloc[1:].dropna()
days         = (returns_df['exec_date'].iloc[-1] - returns_df['exec_date'].iloc[0]).days
ny           = days / 365.25
periods_py   = len(rets) / ny
cagr_gross   = (navs[-1] / navs[0]) ** (1/ny) - 1
sharpe_gross = rets.mean() / rets.std() * np.sqrt(periods_py)
running_max  = pd.Series(navs).cummax()
max_dd       = ((pd.Series(navs) - running_max) / running_max).min()
worst        = rets.min()
tail         = rets.nsmallest(max(1, int(len(rets)*0.10))).mean()

print(f'\n{"="*62}')
print(f'GROSS RESULTS: {VARIANT_LABEL}')
print(f'{"="*62}')
print(f'  RSI_EXIT_THRESH  : {RSI_EXIT_THRESH}')
print(f'  RSI_ENTRY_THRESH : {RSI_ENTRY_THRESH}')
print(f'  Periods          : {len(monthly_pairs)} months over {ny:.1f} years')
print(f'  Total RSI exits  : {total_rsi_exits} ({total_rsi_exits/len(monthly_pairs):.1f}/month)')
print(f'  Replaced         : {total_replaced} ({total_replaced/max(1,total_rsi_exits)*100:.1f}% of exits)')
print(f'  Went to cash     : {total_rsi_exits - total_replaced}')
print(f'  CAGR             : {cagr_gross*100:.2f}%')
print(f'  Sharpe           : {sharpe_gross:.3f}')
print(f'  Max DD           : {max_dd*100:.2f}%')
print(f'  Worst month      : {worst*100:.2f}%')
print(f'  Mean tail        : {tail*100:.2f}%')
print(f'{"="*62}')
print(f'  vs V3 Monthly gross       : CAGR 34.12% | Sharpe 1.197 | MaxDD -35.08%')
print(f'  vs V3 overlay cash (net)  : CAGR 37.83% | Sharpe 1.484 | MaxDD -19.82%')
print(f'  vs V3 replacement (net)   : CAGR 38.02% | Sharpe 1.454 | MaxDD -19.55%')
print(f'  CAGR delta vs Mo          : {(cagr_gross*100 - 34.12):+.2f}%')
print(f'  CAGR delta vs replacement : {(cagr_gross*100 - 38.02):+.2f}%')
print(f'{"="*62}')
print(f'  NOTE: Gross simulation. Overlay benchmarks above are net of costs.')
print(f'{"="*62}')

# ── Save ──────────────────────────────────────────────────────────────────────
os.makedirs(RESULTS_DIR, exist_ok=True)
wr_path  = f'{RESULTS_DIR}/{VARIANT_LABEL}_returns_{RUN_DATE}.csv'
act_path = f'{RESULTS_DIR}/{VARIANT_LABEL}_activity_{RUN_DATE}.csv'
returns_df.to_csv(wr_path,   index=False)
activity_df.to_csv(act_path, index=False)
print(f'\n  Saved: {wr_path}')
print(f'  Saved: {act_path}')
print(f'  Time : {(time.time()-t0)/60:.1f} min')
