import pandas as pd

BASE = "/home/ec2-user/nse-factor-engine/"
prices = pd.read_parquet(BASE + "data/prices.parquet")

vedl = prices[prices['symbol'] == 'VEDL'].sort_values('date')

print("=" * 60)
print("VEDL full volume series stats, pre vs post 2026-04-30")
print("=" * 60)

pre_split = vedl[vedl['date'] < '2026-04-30']
post_split = vedl[vedl['date'] >= '2026-04-30']

print("Pre-split (n={}):".format(len(pre_split)))
print(pre_split['volume'].describe())
print()
print("Post-split (n={}):".format(len(post_split)))
print(post_split['volume'].describe())
print()
print("Ratio of post-split mean volume / pre-split mean volume:",
      post_split['volume'].mean() / pre_split['volume'].mean())

print()
print("=" * 60)
print("Day-by-day around the split (2026-04-23 to 2026-05-07)")
print("=" * 60)
window = vedl[(vedl['date'] >= '2026-04-23') & (vedl['date'] <= '2026-05-07')]
print(window[['date', 'open', 'high', 'low', 'close', 'volume']].to_string(index=False))

print()
print("=" * 60)
print("Close price level shift check (the actual split signature)")
print("=" * 60)
print("Last close before 2026-04-30:", pre_split['close'].iloc[-1] if len(pre_split) else "n/a")
print("First close on/after 2026-04-30:", post_split['close'].iloc[0] if len(post_split) else "n/a")
