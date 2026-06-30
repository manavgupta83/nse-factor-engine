"""
Quick inspect/test for signals/stage5/metrics/cross_sectional_rank.py
Chains off in_universe.py output (in-universe-filtered signals).
"""
import sys
import glob
import re
import pandas as pd

BASE = "/home/ec2-user/nse-factor-engine/"
sys.path.insert(0, BASE + "signals/stage5/metrics")
from in_universe import compute as compute_in_universe
from cross_sectional_rank import compute as compute_rank, RANK_METRICS

signals_files = glob.glob(BASE + "signals/final/momentum_signals_final_*.parquet")
signals_files = [f for f in signals_files if "_pre_stage4_backup" not in f]

date_re = re.compile(r"momentum_signals_final_(\d{8})\.parquet$")
dated = []
for f in signals_files:
    m = date_re.search(f)
    if m:
        dated.append((m.group(1), f))

dated.sort(key=lambda x: pd.Timestamp(day=int(x[0][:2]), month=int(x[0][2:4]), year=int(x[0][4:])))
run_date_str, SIGNALS_PATH = dated[-1]

print(f"Using signals file run_date: {run_date_str}")
signals = pd.read_parquet(SIGNALS_PATH)
print(f"Signals shape: {signals.shape}")

universe_result = compute_in_universe(signals, run_date_str, BASE)
merged = signals.merge(universe_result, on='symbol', how='left')
in_universe_signals = merged[merged['in_universe'] == True].copy()
print(f"In-universe signals shape: {in_universe_signals.shape}")

rank_result = compute_rank(in_universe_signals)

print("\n--- Result shape ---")
print(rank_result.shape)

print("\n--- Columns ---")
print(list(rank_result.columns))

print("\n--- Null check ---")
print(rank_result.isnull().sum().to_string())

print("\n--- Rank range check (should be 1 to N for each metric) ---")
for metric in RANK_METRICS:
    col = f"rank_{metric}"
    print(f"{col}: min={rank_result[col].min()}, max={rank_result[col].max()}, n_unique={rank_result[col].nunique()}")

print("\n--- Spot check: rank_ret_12m1m == 1 ---")
check = in_universe_signals.merge(rank_result, on='symbol')
top1 = check[check['rank_ret_12m1m'] == 1][['symbol', 'ret_12m1m', 'rank_ret_12m1m']]
print(top1.to_string())
actual_max_symbol = check.loc[check['ret_12m1m'].idxmax(), 'symbol']
print(f"Actual max ret_12m1m symbol: {actual_max_symbol}")
assert actual_max_symbol in top1['symbol'].values, "MISMATCH: rank 1 does not match actual max"
print("PASS: rank 1 matches actual max value")

print("\n--- Sample rows ---")
print(rank_result.head(10).to_string())
