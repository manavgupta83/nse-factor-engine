"""
Stage 3 — Assembler: Momentum Quality Signals
Imports each metric from signals/stage3/metrics/.
Inputs : data/prices.parquet
         data/universe_metadata.parquet
         signals/stage2/momentum_core_signals_{AS_OF}.parquet
Output : signals/stage3/momentum_quality_signals_{AS_OF}.parquet
         signals/final/momentum_signals_final_{AS_OF}.parquet
Columns: symbol, as_of_date, fip_score, pct_pos_days, pct_neg_days,
         smoothness, proximity_52w_high, residual_momentum, rm_r2, rm_n_obs,
         industry_cum_ret, industry_rank, weinstein_stage2,
         rs_excess_ret, rs_rank_500
"""

import sys
import os
import pandas as pd
import numpy as np

BASE = "/home/ec2-user/nse-factor-engine"
sys.path.insert(0, BASE)

from signals.stage3.metrics.fip                import compute as compute_fip
from signals.stage3.metrics.smoothness         import compute as compute_smoothness
from signals.stage3.metrics.proximity          import compute as compute_proximity
from signals.stage3.metrics.residual_momentum  import compute as compute_residual_momentum
from signals.stage3.metrics.leading_industry   import compute as compute_leading_industry
from signals.stage3.metrics.weinstein          import compute as compute_weinstein
from signals.stage3.metrics.relative_strength  import compute as compute_relative_strength

# ── Load ─────────────────────────────────────────────────────────────────────
prices = pd.read_parquet(f"{BASE}/data/prices.parquet")
meta   = pd.read_parquet(f"{BASE}/data/universe_metadata.parquet")

# ── Resolve T ────────────────────────────────────────────────────────────────
date_counts = prices.groupby('date')['symbol'].count()
T     = date_counts[date_counts >= 490].index.max()
all_dates = sorted(prices[prices['date'] <= T]['date'].unique())
T_21  = all_dates[-22]
T_252 = all_dates[-253]
AS_OF = T.strftime('%d%m%Y')

# RUN_DATE_STR: IST calendar date this script executed -- used ONLY for
# filenames (reading Stage 2's input, writing Stage 3's own outputs).
# AS_OF (above, derived from T) remains the source of truth for the
# as_of_date COLUMN and all date math (T_21, T_252) -- unchanged.
# Confirmed 2026-06-30: filenames across stages should share run-date,
# not T, since T can lag behind the actual run date.
import zoneinfo
from datetime import datetime as _dt
RUN_DATE_STR = _dt.now(zoneinfo.ZoneInfo("Asia/Kolkata")).date().strftime('%d%m%Y')

print(f"T     : {T.date()}")
print(f"T-21  : {T_21.date()}")
print(f"T-252 : {T_252.date()}")
print(f"AS_OF : {AS_OF}")

# ── Load stage 2 signals ──────────────────────────────────────────────────────
signals = pd.read_parquet(f"{BASE}/signals/stage2/momentum_core_signals_{RUN_DATE_STR}.parquet")
print(f"Stage 2 signals loaded: {signals.shape}")

# ── Formation window T-252 → T-21 ────────────────────────────────────────────
window = (
    prices[(prices['date'] >= T_252) & (prices['date'] <= T_21)]
    .copy()
    .sort_values(['symbol', 'date'])
    .reset_index(drop=True)
)

# ── Compute metrics ───────────────────────────────────────────────────────────
print("Computing FIP...")
fip_df    = compute_fip(window, signals)

print("Computing smoothness...")
smooth_df = compute_smoothness(window)

print("Computing 52w proximity...")
prox_df   = compute_proximity(prices, T, T_252)

print("Computing residual momentum...")
rm_df     = compute_residual_momentum(window, meta)

print("Computing leading industry...")
li_df     = compute_leading_industry(window, meta)

print("Computing Weinstein stage...")
ws_df     = compute_weinstein(prices, T)

print("Computing relative strength...")
rs_df     = compute_relative_strength(window)

# ── Assemble ─────────────────────────────────────────────────────────────────
result = signals[['symbol']].copy()
result = result.merge(fip_df,    on='symbol', how='left')
result = result.merge(smooth_df, on='symbol', how='left')
result = result.merge(prox_df,   on='symbol', how='left')
result = result.merge(rm_df,     on='symbol', how='left')
result = result.merge(li_df,     on='symbol', how='left')
result = result.merge(ws_df,     on='symbol', how='left')
result = result.merge(rs_df,     on='symbol', how='left')
result.insert(1, 'as_of_date', T)

# ── Final checks ─────────────────────────────────────────────────────────────
print("\n--- Shape ---")
print(result.shape)

print("\n--- Columns ---")
print(list(result.columns))

print("\n--- Null counts ---")
print(result.isnull().sum().to_string())

print("\n--- Distributions ---")
for col in ['fip_score', 'pct_pos_days', 'pct_neg_days', 'smoothness',
            'proximity_52w_high', 'residual_momentum',
            'industry_cum_ret', 'industry_rank', 'rs_excess_ret', 'rs_rank_500']:
    print(f"\n{col}:")
    print(result[col].describe(percentiles=[.05, .25, .5, .75, .95]).to_string())

print(f"\nweinstein_stage2 value counts:")
print(result['weinstein_stage2'].value_counts().to_string())

print(f"\nTotal symbols : {result['symbol'].nunique()}")
print(f"Total rows    : {len(result)}")

# ── Save stage3 parquet ───────────────────────────────────────────────────────
out_path = f"{BASE}/signals/stage3/momentum_quality_signals_{RUN_DATE_STR}.parquet"
result.to_parquet(out_path, index=False)
print(f"\nSaved stage3: {out_path}")
print(f"Shape: {result.shape}")

# ── Merge stage2 + stage3 and save to final ───────────────────────────────────
os.makedirs(f"{BASE}/signals/final", exist_ok=True)
final = signals.merge(result.drop(columns=['as_of_date']), on='symbol', how='left')
final_path = f"{BASE}/signals/final/momentum_signals_final_{RUN_DATE_STR}.parquet"
final.to_parquet(final_path, index=False)
print(f"Saved final : {final_path}")
print(f"Shape: {final.shape}")
print("\nASSEMBLER COMPLETE")
