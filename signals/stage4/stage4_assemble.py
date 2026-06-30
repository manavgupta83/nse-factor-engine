"""
Stage 4 — Assembler
Imports all Stage 4 metric modules, merges their output onto the existing
Stage 2+3 signals file, and overwrites the final output in place.

Per architecture convention: each metric lives in
signals/stage4/metrics/<metric>.py exposing compute(). This script is the
only place that combines them.

T and the signals filename are both derived dynamically — nothing
hardcoded — so this script is safe to re-run on any future date without
manual edits.
"""
import pandas as pd
import sys

sys.path.insert(0, "/home/ec2-user/nse-factor-engine/signals/stage4/metrics")
from stpb import compute as compute_stpb
from volume_confirmation import compute as compute_volconf
from daily_return_magnitude import compute as compute_lottery

BASE = "/home/ec2-user/nse-factor-engine/"

prices = pd.read_parquet(BASE + "data/prices.parquet")

# T resolution FIRST — from prices.parquet alone, no dependency on the
# signals filename. This is the only thing we can determine without
# already knowing which file to read.
date_counts = prices.groupby('date')['symbol'].count()
T = date_counts[date_counts >= 490].index.max()
all_dates = sorted(prices[prices['date'] <= T]['date'].unique())
T_str = pd.Timestamp(T).strftime('%d%m%Y')

# Input/output path both derived from T via glob, not hardcoded — this
# script can be re-run on any future date without manual edits, as long
# as Stage 2+3 has already produced a signals file matching that T.
import glob
SIGNALS_PATH = BASE + f"signals/final/momentum_signals_final_{T_str}.parquet"
matches = glob.glob(SIGNALS_PATH)
assert len(matches) == 1, (
    f"Expected exactly one signals file matching T={T} ({T_str}) at "
    f"{SIGNALS_PATH}, found {len(matches)}. Has Stage 2+3 been run for "
    f"this T yet? STOPPING rather than guessing which file to use."
)

signals = pd.read_parquet(SIGNALS_PATH)

n_rows_before = len(signals)
symbols_before = set(signals['symbol'])
cols_before = set(signals.columns)

print(f"T resolved as: {T}")
print(f"Input signals shape: {signals.shape}")

# --- Step 1: Short-Term Price Behaviour ---
stpb_result = compute_stpb(prices, signals, T, all_dates)
print(f"stpb_result shape: {stpb_result.shape}")

# --- Step 2: Volume Confirmation (depends on stpb_result for stpb_ret_21d) ---
volconf_result = compute_volconf(prices, stpb_result, T, all_dates)
print(f"volconf_result shape: {volconf_result.shape}")
# stpb_ret_21d already present via stpb_result merge below — drop the
# duplicate carried inside volconf_result before merging, keep only the
# two columns unique to this metric
volconf_cols = volconf_result[['symbol', 'vol_ratio_21_252', 'volume_price_pos_move_confirmed']]

# --- Step 3: Absolute Daily Return Magnitude (Lottery Classifier) ---
lottery_result = compute_lottery(prices, T, all_dates)
print(f"lottery_result shape: {lottery_result.shape}")

# --- Merge all onto existing signals file ---
merged = signals.merge(stpb_result, on='symbol', how='left')
merged = merged.merge(volconf_cols, on='symbol', how='left')
merged = merged.merge(lottery_result, on='symbol', how='left')

# --- Sanity checks before writing ---
assert len(merged) == n_rows_before, f"Row count changed: {n_rows_before} -> {len(merged)}"
assert set(merged['symbol']) == symbols_before, "Symbol set changed during merge"

new_cols = set(merged.columns) - cols_before
expected_new_cols = {
    'stpb_ret_21d', 'stpb_ret_7d', 'stpb_zscore_21d', 'stpb_zscore_7d', 'stpb_ma_distance_21d',
    'vol_ratio_21_252', 'volume_price_pos_move_confirmed',
    'days_bw_15_20perc', 'days_bw_10_15perc', 'days_bw_5_10perc', 'days_bw_2_5perc', 'lottery_class',
}
assert new_cols == expected_new_cols, f"Unexpected new columns: {new_cols ^ expected_new_cols}"

print(f"Output shape: {merged.shape}")
print(f"New columns added ({len(new_cols)}): {sorted(new_cols)}")
print()
print("Null check on new columns:")
print(merged[sorted(new_cols)].isnull().sum())

# --- Write in place (backup original first, since this overwrites the live file) ---
import shutil
backup_path = SIGNALS_PATH.replace('.parquet', '_pre_stage4_backup.parquet')
shutil.copy2(SIGNALS_PATH, backup_path)
print(f"Backup written to: {backup_path}")

merged.to_parquet(SIGNALS_PATH, index=False)
print(f"\nWritten to: {SIGNALS_PATH}")
