import pandas as pd
import numpy as np

BASE = "/home/ec2-user/nse-factor-engine"
px = pd.read_parquet(f"{BASE}/data/prices.parquet")
px["date"] = pd.to_datetime(px["date"])
px = px.sort_values(["symbol", "date"]).reset_index(drop=True)

# ffill null close
px["close"] = px.groupby("symbol")["close"].ffill()

date_counts = px.groupby("date")["symbol"].count()
T = date_counts[date_counts >= 490].index.max()

# --- compute returns ---
records = []
for sym, grp in px.groupby("symbol"):
    dates = grp["date"].sort_values().reset_index(drop=True)
    closes = grp.set_index("date")["close"].sort_index()
    n = len(dates)

    T_pos = dates[dates == T].index
    T_pos_i = T_pos[0] if len(T_pos) > 0 else n - 1

    def get_close(offset):
        idx = T_pos_i - offset
        if idx < 0:
            return np.nan
        d = dates.iloc[idx]
        return closes.loc[d]

    c_t21  = get_close(21)
    c_t63  = get_close(63)
    c_t126 = get_close(126)
    c_t252 = get_close(252)

    def ret(end, start):
        if pd.isna(end) or pd.isna(start) or start == 0:
            return np.nan
        return (end - start) / start

    records.append({
        "symbol":     sym,
        "ret_12m1m":  ret(c_t21, c_t252),
        "ret_6m1m":   ret(c_t21, c_t126),
        "ret_3m1m":   ret(c_t21, c_t63),
    })

df = pd.DataFrame(records)

# --- sanity checks ---
print(f"Rows: {len(df)}  |  Columns: {list(df.columns)}")
print(f"\nNaN counts:")
print(df[["ret_12m1m","ret_6m1m","ret_3m1m"]].isna().sum().to_string())

print(f"\nDescriptive stats:")
print(df[["ret_12m1m","ret_6m1m","ret_3m1m"]].describe().round(4).to_string())

print(f"\nSample — 5 symbols with all returns valid:")
print(df.dropna().head(5).to_string(index=False))

print(f"\nSample — symbols with partial NaN:")
partial = df[df.isna().any(axis=1)]
print(f"  count: {len(partial)}")
print(partial.head(5).to_string(index=False))
