"""
Stage 3 Step 5 — Step 0c: Resolve ENRIN cutoff ambiguity
"""
import pandas as pd

BASE = "/home/ec2-user/nse-factor-engine"

prices = pd.read_parquet(f"{BASE}/data/prices.parquet")
s3     = pd.read_parquet(f"{BASE}/signals/stage3/momentum_quality_signals_25062026.parquet")

date_counts = prices.groupby('date')['symbol'].count()
T     = date_counts[date_counts >= 490].index.max()
all_dates = sorted(prices[prices['date'] <= T]['date'].unique())
T_21  = all_dates[-22]
T_252 = all_dates[-253]

window = prices[(prices['date'] >= T_252) & (prices['date'] <= T_21)].copy()
sym_counts = window.groupby('symbol')['date'].count()

nan_fip   = set(s3[s3['fip_score'].isna()]['symbol'])
valid_fip = set(s3[s3['fip_score'].notna()]['symbol'])

syms_230 = sym_counts[sym_counts == 230].index.tolist()
print("=== Symbols with exactly 230 rows in window ===")
for s in sorted(syms_230):
    status = "NaN" if s in nan_fip else "VALID"
    print(f"  {s:20s}  {status}")

valid_counts = sym_counts[sym_counts.index.isin(valid_fip)].sort_values()
print(f"\n=== 5 lowest-count valid-fip symbols ===")
print(valid_counts.head(5).to_string())
