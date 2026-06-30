import pandas as pd
import sys
sys.path.insert(0, "/home/ec2-user/nse-factor-engine/signals/stage4/metrics")
from daily_return_magnitude import compute

BASE = "/home/ec2-user/nse-factor-engine/"
prices = pd.read_parquet(BASE + "data/prices.parquet")

date_counts = prices.groupby('date')['symbol'].count()
T = date_counts[date_counts >= 490].index.max()
all_dates = sorted(prices[prices['date'] <= T]['date'].unique())

result = compute(prices, T, all_dates)

print("shape:", result.shape)
print("nulls:\n", result.isnull().sum())
print()
print("class distribution:\n", result['lottery_class'].value_counts())
print()
print("VEDL row (expect contamination from split-day return, per KI-001/KI-002):")
print(result[result['symbol'] == 'VEDL'])
print()
print("Sample:\n", result.head(10))
print()
print("All EXTREME LOTTERY symbols:\n", result[result['lottery_class'] == 'EXTREME LOTTERY'])
