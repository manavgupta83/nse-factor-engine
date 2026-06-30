import pandas as pd
import sys
sys.path.insert(0, "/home/ec2-user/nse-factor-engine/signals/stage4/metrics")
from stpb import compute as compute_stpb
from volume_confirmation import compute as compute_volconf

BASE = "/home/ec2-user/nse-factor-engine/"
prices = pd.read_parquet(BASE + "data/prices.parquet")
signals = pd.read_parquet(BASE + "signals/final/momentum_signals_final_25062026.parquet")

date_counts = prices.groupby('date')['symbol'].count()
T = date_counts[date_counts >= 490].index.max()
all_dates = sorted(prices[prices['date'] <= T]['date'].unique())

stpb_result = compute_stpb(prices, signals, T, all_dates)
volconf_result = compute_volconf(prices, stpb_result, T, all_dates)

print("shape:", volconf_result.shape)
print("nulls:\n", volconf_result.isnull().sum())
print()
print(volconf_result['vol_ratio_21_252'].describe())
print()
print("flag True count:", volconf_result['volume_price_pos_move_confirmed'].sum())
print("flag False count:", (~volconf_result['volume_price_pos_move_confirmed']).sum())
print()
print("VEDL row:")
print(volconf_result[volconf_result['symbol'] == 'VEDL'])
print()
print("AARTIIND row (sanity check - high vol_ratio, negative return -> False expected):")
print(volconf_result[volconf_result['symbol'] == 'AARTIIND'])
print()
print("Sample:\n", volconf_result.head(10))
print()
print("Top 5 by vol_ratio_21_252:\n", volconf_result.nlargest(5, 'vol_ratio_21_252'))
