import pandas as pd
import numpy as np

BASE = "/home/ec2-user/nse-factor-engine"
px = pd.read_parquet(f"{BASE}/data/prices.parquet")
px["date"] = pd.to_datetime(px["date"])
px = px.sort_values(["symbol", "date"]).reset_index(drop=True)
px["close"] = px.groupby("symbol")["close"].ffill()

T = px["date"].max()

records = []
for sym, grp in px.groupby("symbol"):
    dates  = grp["date"].sort_values().reset_index(drop=True)
    closes = grp.sort_values("date")["close"].reset_index(drop=True)
    n      = len(dates)

    T_pos   = dates[dates == T].index
    T_pos_i = T_pos[0] if len(T_pos) > 0 else n - 1

    start_252 = T_pos_i - 252
    if start_252 < 0:
        continue

    sl_252  = closes.iloc[start_252 : T_pos_i + 1]
    lr_252  = np.log(sl_252.values[1:] / sl_252.values[:-1])
    neg_252 = lr_252[lr_252 < 0]

    vol_252    = np.std(lr_252, ddof=1) * np.sqrt(252)
    dv_252     = np.std(neg_252, ddof=1) * np.sqrt(252) if len(neg_252) >= 2 else np.nan
    n_neg      = len(neg_252)
    n_total    = len(lr_252)

    records.append({
        "symbol":     sym,
        "vol_252":    round(vol_252, 4),
        "dv_252":     round(dv_252, 4) if not np.isnan(dv_252) else np.nan,
        "violation":  dv_252 > vol_252 if not np.isnan(dv_252) else False,
        "n_total":    n_total,
        "n_neg":      n_neg,
        "pct_neg":    round(n_neg / n_total, 3),
        "std_all_lr": round(np.std(lr_252, ddof=1), 6),
        "std_neg_lr": round(np.std(neg_252, ddof=1), 6) if len(neg_252) >= 2 else np.nan,
        "mean_lr":    round(np.mean(lr_252), 6),
        "mean_neg_lr":round(np.mean(neg_252), 6) if n_neg > 0 else np.nan,
    })

df = pd.DataFrame(records)
viols = df[df["violation"]].sort_values("dv_252", ascending=False)

print(f"Total violations: {len(viols)}")
print(f"\nViolating symbols — full diagnostic:")
print(viols[[
    "symbol","vol_252","dv_252","n_total","n_neg","pct_neg",
    "std_all_lr","std_neg_lr","mean_lr","mean_neg_lr"
]].to_string(index=False))

print(f"\nNote: downside_vol > total_vol occurs when negative days are few but")
print(f"widely dispersed — std(neg_subset) can exceed std(full_series) when")
print(f"positive days cluster near zero and anchor the full distribution tightly.")
