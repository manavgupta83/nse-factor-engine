"""
Stage 3 — Step 2: % Positive Return Days
Already computed in Step 1. This step reads Step 1 output,
extracts pct_pos_days as a standalone signal, sanity checks, and saves.
Window: T-252 -> T-21 (same formation window as FIP)
All 500 symbols. NaN propagates for symbols with < 253 rows.
"""

import pandas as pd
import numpy as np

BASE = "/home/ec2-user/nse-factor-engine"

# ── Load Step 1 output ───────────────────────────────────────────────────────
fip = pd.read_parquet(f"{BASE}/signals/stage3/stage3_step1_fip.parquet")

# ── Extract pct_pos_days ─────────────────────────────────────────────────────
result = fip[['symbol', 'pct_pos_days']].copy()

# ── Sanity checks ────────────────────────────────────────────────────────────
print("--- Shape ---")
print(result.shape)

print("\n--- Null counts ---")
print(result.isnull().sum())

print("\n--- Distribution ---")
print(result['pct_pos_days'].describe())

print("\n--- Range check (should be 0 to 1) ---")
out_of_range = result[(result['pct_pos_days'] < 0) | (result['pct_pos_days'] > 1)]
print(f"Out of range: {len(out_of_range)} symbols")

print("\n--- Top 10 highest pct_pos_days (most positive days) ---")
print(result.nlargest(10, 'pct_pos_days'))

print("\n--- Top 10 lowest pct_pos_days (fewest positive days) ---")
print(result.nsmallest(10, 'pct_pos_days'))

# ── Save ─────────────────────────────────────────────────────────────────────
out_path = f"{BASE}/signals/stage3/stage3_step2_pct_pos_days.parquet"
result.to_parquet(out_path, index=False)
print(f"\nSaved: {out_path}")
print(f"Shape: {result.shape}")
print("\nSTEP 2 COMPLETE")
