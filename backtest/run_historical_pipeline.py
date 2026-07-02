import sys, os, warnings, time, gc
import pandas as pd
warnings.filterwarnings('ignore')
sys.path.insert(0, '/home/ec2-user/nse-factor-engine')
from backtest.pipeline.compute_signals import compute_signals

BASE    = '/home/ec2-user/nse-factor-engine/backtest'
OUT_DIR = f'{BASE}/signals/historical'
os.makedirs(OUT_DIR, exist_ok=True)

print('Loading prices ...')
prices = pd.read_parquet(f'{BASE}/data/prices_backtest.parquet')
meta   = pd.read_parquet(f'{BASE}/data/universe_metadata_backtest.parquet')

all_dates = pd.DatetimeIndex(sorted(prices['date'].unique()))
fridays   = all_dates[all_dates.dayofweek == 4]
valid     = [f for f in fridays if len(all_dates[all_dates < f]) >= 252]

print(f'Valid Fridays : {len(valid)}')
print(f'Already done  : {len(os.listdir(OUT_DIR))}')
print()

total, done, skipped, failed = len(valid), 0, 0, []
t_start = time.time()

for i, T in enumerate(valid):
    date_str = pd.Timestamp(T).strftime('%d%m%Y')
    out_path = f'{OUT_DIR}/signals_{date_str}.parquet'

    if os.path.exists(out_path):
        skipped += 1
        continue

    t0 = time.time()
    try:
        # pre-slice: only pass 300 trading days ending at T
        t_pos      = all_dates.get_loc(T)
        start_idx  = max(0, t_pos - 300)
        start_date = all_dates[start_idx]
        px_window  = prices[(prices['date'] >= start_date) & (prices['date'] <= T)]

        df = compute_signals(px_window, meta, pd.Timestamp(T))
        df.to_parquet(out_path, index=False)
        info = f'rows={len(df)} in_universe={df["in_universe"].sum()}'

        del df, px_window
        gc.collect()

        done += 1
        elapsed       = time.time() - t0
        total_elapsed = time.time() - t_start
        avg           = total_elapsed / done
        remaining     = (total - done - skipped) * avg
        print(f'[{i+1:03d}/{total}] T={T.date()} | {info} | {elapsed:.1f}s | ETA {remaining/60:.1f}min', flush=True)

    except Exception as e:
        failed.append((T.date(), str(e)))
        print(f'[{i+1:03d}/{total}] T={T.date()} FAILED: {e}', flush=True)
        gc.collect()

print()
print('=' * 60)
print(f'Total    : {total}')
print(f'Computed : {done}')
print(f'Skipped  : {skipped}')
print(f'Failed   : {len(failed)}')
if failed:
    for d, e in failed:
        print(f'  {d} : {e}')
print(f'Files    : {len(os.listdir(OUT_DIR))}')
print(f'Time     : {(time.time()-t_start)/60:.1f} min')
print('=' * 60)
