"""
Stage 5 — Assembler
Imports all Stage 5 metric modules, merges their output onto the existing
Stage 2+3+4 signals file, and overwrites the final output in place.

All 500 original rows are retained -- non-investable symbols (in_universe
== False) are kept with NaN rank/FIP columns rather than dropped, so the
file stays auditable. Ranking itself is still computed only on the
in-universe subset (ranking against non-investable symbols would distort
percentile cutoffs).

Filenames are keyed to RUN_DATE (IST run date), not T -- per locked
convention (2026-06-30). universe_{run_date}.parquet must strictly match
the signals file's run_date (taken from its own filename).
"""
import glob
import re
import shutil
import sys
import pandas as pd

BASE = "/home/ec2-user/nse-factor-engine/"
sys.path.insert(0, BASE + "signals/stage5/metrics")
from in_universe import compute as compute_in_universe
from cross_sectional_rank import compute as compute_rank, RANK_METRICS
from fip_rerank import compute as compute_fip

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
n_rows_before = len(signals)
cols_before = set(signals.columns)
print(f"Input signals shape: {signals.shape}")
print(f"as_of_date (T) inside file: {signals['as_of_date'].iloc[0]}")

universe_result = compute_in_universe(signals, run_date_str, BASE)
merged_full = signals.merge(universe_result, on='symbol', how='left')
assert merged_full['in_universe'].notnull().all(), "Nulls in in_universe after merge -- universe file missing symbols"

n_full = len(merged_full)
in_universe_subset = merged_full[merged_full['in_universe'] == True].copy()
n_in_universe = len(in_universe_subset)
print(f"in_universe split: {n_full} total, {n_in_universe} in-universe (ranked), "
      f"{n_full - n_in_universe} excluded (kept in output, unranked, NaN rank columns)")

rank_result = compute_rank(in_universe_subset)

fip_input = in_universe_subset.merge(rank_result, on='symbol', how='left')
fip_result = compute_fip(fip_input)

merged = merged_full.merge(rank_result, on='symbol', how='left')
merged = merged.merge(fip_result, on='symbol', how='left')

new_cols = set(merged.columns) - cols_before
expected_new_cols = (
    {'in_universe', 'passes_mktcap', 'passes_adtv'}
    | {f'rank_{m}' for m in RANK_METRICS}
    | {f'rank_fip_{m}' for m in RANK_METRICS}
)
assert new_cols == expected_new_cols, f"Unexpected new columns: {new_cols ^ expected_new_cols}"
assert len(merged) == n_full, f"Row count changed: expected {n_full} (full set retained), got {len(merged)}"
assert set(merged['symbol']) == set(signals['symbol']), "Symbol set changed -- rows were dropped"

n_excluded = (merged['in_universe'] == False).sum()
excluded_check = merged[merged['in_universe'] == False]
rank_cols_check = [f'rank_{m}' for m in RANK_METRICS] + [f'rank_fip_{m}' for m in RANK_METRICS]
assert excluded_check[rank_cols_check].isnull().all().all(), \
    "Excluded (non-investable) symbols unexpectedly have non-null rank values"
print(f"Confirmed: {n_excluded} excluded symbols retained in output with all rank columns NaN")

print(f"\nOutput shape: {merged.shape}")
print(f"New columns added ({len(new_cols)}): {sorted(new_cols)}")
print()
print("Null check on new columns:")
print(merged[sorted(new_cols)].isnull().sum().to_string())

backup_path = SIGNALS_PATH.replace('.parquet', '_pre_stage5_backup.parquet')
shutil.copy2(SIGNALS_PATH, backup_path)
print(f"\nBackup written to: {backup_path}")

merged.to_parquet(SIGNALS_PATH, index=False)
print(f"Written to: {SIGNALS_PATH}")
print(f"Row count unchanged: {len(merged)} (all original symbols retained, "
      f"{n_in_universe} ranked, {n_full - n_in_universe} marked in_universe=False with NaN ranks)")
