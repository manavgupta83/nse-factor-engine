"""
Monday-Open / Tuesday-Open Simulation — G6_C6, N=25

MON: Signal = Friday close  (signals/historical/, 511 files) -> Execute = Monday open
TUE: Signal = Monday close  (signals/historical/monday/, 513 files) -> Execute = Tuesday open

Activity log schema identical to Friday-close gold standard:
  friday_date, cell_id, symbol, action, shares, price, value, portfolio_value, cash_pool
"""

import sys, os, warnings, time, gc
import pandas as pd
import numpy as np
warnings.filterwarnings('ignore')
sys.path.insert(0, '/home/ec2-user/nse-factor-engine')

from backtest.strategies.engine    import get_portfolio
from backtest.simulation.portfolio import PortfolioState

BASE        = '/home/ec2-user/nse-factor-engine/backtest'
FRI_SIG_DIR = f'{BASE}/signals/historical'
MON_SIG_DIR = f'{BASE}/signals/historical/monday'
PRICES_PATH = f'{BASE}/data/prices_backtest.parquet'
BENCH_PATH  = f'{BASE}/data/benchmark/nifty500_weekly.parquet'
RESULTS_DIR = f'{BASE}/results'
CELL_ID     = 'G6_C6'
GATE_ID     = 'G6'
SCORE_ID    = 'C6'
INITIAL_CAPITAL = 10_000_000.0
RUN_DATE    = '10072026'

os.makedirs(RESULTS_DIR, exist_ok=True)

def parse_date(fname):
    d = fname.replace('signals_', '').replace('.parquet', '')
    return pd.Timestamp(f'{d[4:8]}-{d[2:4]}-{d[0:2]}')

print('Loading prices ...')
prices = pd.read_parquet(PRICES_PATH)
print(f'  shape      : {prices.shape}')
print(f'  date range : {prices["date"].min().date()} -> {prices["date"].max().date()}')

open_by_date = {
    pd.Timestamp(date): grp.set_index('symbol')['open'].to_dict()
    for date, grp in prices.groupby('date')
}
all_trading_days = sorted(open_by_date.keys())
print(f'  trading days indexed: {len(all_trading_days)}')
del prices
gc.collect()

bench = pd.read_parquet(BENCH_PATH).set_index('date')['close']
bench.index = pd.DatetimeIndex(bench.index)

def next_trading_day(dt):
    for td in all_trading_days:
        if td > dt:
            return td
    return None

print('\nBuilding Monday-open week pairs ...')
fri_files = sorted(
    f for f in os.listdir(FRI_SIG_DIR)
    if f.startswith('signals_') and f.endswith('.parquet')
    and os.path.isfile(os.path.join(FRI_SIG_DIR, f))
)
mon_pairs = []
for fname in fri_files:
    sig_date  = parse_date(fname)
    exec_date = next_trading_day(sig_date)
    if exec_date and open_by_date.get(exec_date):
        mon_pairs.append((sig_date, exec_date, os.path.join(FRI_SIG_DIR, fname)))
mon_pairs.sort(key=lambda x: x[1])
print(f'  pairs : {len(mon_pairs)}')
print(f'  first : sig={mon_pairs[0][0].date()} exec={mon_pairs[0][1].date()} ({mon_pairs[0][1].day_name()})')
print(f'  last  : sig={mon_pairs[-1][0].date()} exec={mon_pairs[-1][1].date()} ({mon_pairs[-1][1].day_name()})')

print('\nBuilding Tuesday-open week pairs ...')
mon_files = sorted(
    f for f in os.listdir(MON_SIG_DIR)
    if f.startswith('signals_') and f.endswith('.parquet')
)
tue_pairs = []
for fname in mon_files:
    sig_date  = parse_date(fname)
    exec_date = next_trading_day(sig_date)
    if exec_date and open_by_date.get(exec_date):
        tue_pairs.append((sig_date, exec_date, os.path.join(MON_SIG_DIR, fname)))
tue_pairs.sort(key=lambda x: x[1])
print(f'  pairs : {len(tue_pairs)}')
print(f'  first : sig={tue_pairs[0][0].date()} exec={tue_pairs[0][1].date()} ({tue_pairs[0][1].day_name()})')
print(f'  last  : sig={tue_pairs[-1][0].date()} exec={tue_pairs[-1][1].date()} ({tue_pairs[-1][1].day_name()})')

def run_sim(cadence_name, week_pairs):
    print(f'\n{"="*60}')
    print(f'RUNNING: {cadence_name}  ({len(week_pairs)} weeks)')
    print(f'{"="*60}')

    state        = PortfolioState(initial_capital=INITIAL_CAPITAL)
    nav_path     = []
    all_activity = []
    t0           = time.time()

    for i, (sig_date, exec_date, sig_path) in enumerate(week_pairs):
        signals           = pd.read_parquet(sig_path)
        incumbent_symbols = set(state.holdings.keys())
        port_df           = get_portfolio(GATE_ID, SCORE_ID, signals,
                                          verbose=False,
                                          incumbent_symbols=incumbent_symbols)
        top25 = port_df['symbol'].tolist() if not port_df.empty else []

        exec_px              = open_by_date[exec_date]
        port_value, activity = state.rebalance(top25, exec_px, exec_date, CELL_ID)

        nav_path.append((exec_date, port_value))
        all_activity.extend(activity)

        if (i + 1) % 50 == 0 or i == 0 or (i + 1) == len(week_pairs):
            elapsed   = time.time() - t0
            remaining = elapsed / (i + 1) * (len(week_pairs) - i - 1) if i else 0
            print(f'  [{i+1:03d}/{len(week_pairs)}] sig={sig_date.date()} exec={exec_date.date()} NAV=Rs{port_value/1e6:.3f}M | {elapsed:.0f}s elapsed {remaining:.0f}s ETA', flush=True)

        gc.collect()

    exec_dates = [d for d, _ in nav_path]
    navs       = [v for _, v in nav_path]
    nav_s      = pd.Series(navs)
    wrets      = nav_s.pct_change().values
    wrets[0]   = 0.0

    bm_vals = []
    for dt in exec_dates:
        bv = bench.get(dt, np.nan)
        if pd.isna(bv):
            prior = bench[:dt]
            bv    = prior.iloc[-1] if len(prior) else np.nan
        bm_vals.append(bv)
    bm_s       = pd.Series(bm_vals).pct_change()
    bm_s.iloc[0] = 0.0

    weekly_df = pd.DataFrame({
        'friday_date': exec_dates,
        CELL_ID      : wrets,
        'benchmark'  : bm_s.values,
    })
    activity_df = pd.DataFrame(all_activity)

    wr_path  = f'{RESULTS_DIR}/backtest_weekly_returns_W1_{cadence_name}_{RUN_DATE}.parquet'
    act_path = f'{RESULTS_DIR}/backtest_portfolio_activity_W1_{cadence_name}_{RUN_DATE}.parquet'
    weekly_df.to_parquet(wr_path,   index=False)
    activity_df.to_parquet(act_path, index=False)
    print(f'\n  weekly_returns -> {wr_path} ({os.path.getsize(wr_path)/1024:.0f} KB)')
    print(f'  activity_log   -> {act_path} ({os.path.getsize(act_path)/1024/1024:.2f} MB)')

    rets = weekly_df[CELL_ID].iloc[1:].dropna()
    cum  = (1 + rets).prod() - 1
    ny   = len(rets) / 52
    cagr = (1 + cum) ** (1 / ny) - 1 if ny > 0 else np.nan
    print(f'\n  SANITY [{cadence_name}]')
    print(f'  weeks            : {len(rets)}')
    print(f'  full-period CAGR : {cagr:.2%}')
    print(f'  NAV start        : Rs{navs[0]/1e6:.4f}M')
    print(f'  NAV end          : Rs{navs[-1]/1e6:.3f}M')
    print(f'  activity rows    : {len(activity_df)}')
    print(f'  activity cols    : {list(activity_df.columns)}')
    print(f'  total time       : {(time.time()-t0)/60:.1f} min')

    return weekly_df, activity_df

mon_wr, mon_act = run_sim('monday_open',  mon_pairs)
tue_wr, tue_act = run_sim('tuesday_open', tue_pairs)

print(f'\n{"="*60}')
print('SUMMARY')
print(f'{"="*60}')
for name, wr in [('monday_open', mon_wr), ('tuesday_open', tue_wr)]:
    rets = wr[CELL_ID].iloc[1:].dropna()
    cum  = (1 + rets).prod() - 1
    ny   = len(rets) / 52
    cagr = (1 + cum) ** (1 / ny) - 1
    print(f'  {name:14s}: CAGR={cagr:.2%}  weeks={len(rets)}')
print('Expected: Mon ~23.45%  Tue ~23.56%')
