import pandas as pd
import sys
sys.path.insert(0, "/home/ec2-user/nse-factor-engine/signals/stage4/metrics")
from stpb import compute

BASE = "/home/ec2-user/nse-factor-engine/"
prices = pd.read_parquet(BASE + "data/prices.parquet")
signals = pd.read_parquet(BASE + "signals/final/momentum_signals_final_25062026.parquet")

date_counts = prices.groupby('date')['symbol'].count()
T = date_counts[date_counts >= 490].index.max()
all_dates = sorted(prices[prices['date'] <= T]['date'].unique())

result = compute(prices, signals, T, all_dates)

print("shape:", result.shape)
print("nulls:\n", result.isnull().sum())
print()
print(result.describe())
print()
print("VEDL row (watch for split distortion, KI-001):")
print(result[result['symbol'] == 'VEDL'])
print()
print("Sample:\n", result.head(10))
