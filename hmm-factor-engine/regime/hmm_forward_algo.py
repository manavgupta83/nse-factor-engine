"""
hmm_forward_algo.py
Causal forward algorithm — at month T, only data up to T is used.
No lookahead. Safe for production use.
"""

import json
import joblib
import numpy as np
import pandas as pd
from pathlib import Path
from scipy.stats import multivariate_normal

BASE_DIR   = Path("/home/ec2-user/nse-factor-engine/hmm-factor-engine")
MODEL_FILE = BASE_DIR / "regime/models/hmm_3states_200511_201912.pkl"
DATA_FILE  = BASE_DIR / "data/nifty500_hmm_data.parquet"
OUT_PARQUET = BASE_DIR / "regime/models/hmm_forward_probs_202001_202606.parquet"
OUT_JSON    = BASE_DIR / "regime/models/hmm_forward_probs_202001_202606_summary.json"
BW_CSV      = Path("/tmp/eval_window_gamma.csv")

EVAL_START = "2020-01"
EVAL_END   = "2026-06"

# ── Load model ───────────────────────────────────────────────────────────────
bundle       = joblib.load(MODEL_FILE)
model        = bundle["model"]
scaler       = bundle["scaler"]
state_labels = bundle["state_labels"]          # {0: Choppy, 1: Crisis, 2: Bull}
features     = bundle["hmm_features"]
label_map    = {v: k for k, v in state_labels.items()}  # Bull->2, Choppy->0, Crisis->1
n_states     = model.n_components

print(f"Model           : {bundle['train_start']} → {bundle['train_end']}")
print(f"State labels    : {state_labels}")

# ── Load eval data ───────────────────────────────────────────────────────────
df   = pd.read_parquet(DATA_FILE)
df.index = pd.to_datetime(df.index)
mask = (df.index >= pd.Period(EVAL_START, "M").to_timestamp()) & \
       (df.index <= pd.Period(EVAL_END,   "M").to_timestamp(how="end"))
data = df.loc[mask, features].dropna()

print(f"Eval window     : {data.index[0].date()} → {data.index[-1].date()}")
print(f"Months          : {len(data)}")

# ── Scale ────────────────────────────────────────────────────────────────────
X = scaler.transform(data.values)

# ── Emission log-probabilities ───────────────────────────────────────────────
log_b = np.column_stack([
    multivariate_normal.logpdf(X, mean=model.means_[s], cov=model.covars_[s])
    for s in range(n_states)
])

# ── Initial state: use Dec 2019 Viterbi label as prior ───────────────────────
# Transition from Dec-2019 state → Jan-2020 prior
dec2019_label = "Bull"   # from hmm_viterbi_labels_200511_201912.parquet last row
init_state    = label_map[dec2019_label]
state_vec     = np.zeros(n_states)
state_vec[init_state] = 1.0
prior         = model.transmat_[init_state]   # one-step transition from Dec-2019

# ── Forward pass (causal) ────────────────────────────────────────────────────
alpha = np.zeros((len(X), n_states))
alpha[0] = prior * np.exp(log_b[0])
s = alpha[0].sum()
alpha[0] = alpha[0] / s if s > 0 else prior

for t in range(1, len(X)):
    alpha[t] = (model.transmat_.T @ alpha[t-1]) * np.exp(log_b[t])
    s = alpha[t].sum()
    alpha[t] = alpha[t] / s if s > 0 else alpha[t-1]

# ── Build output ─────────────────────────────────────────────────────────────
P_Bull   = alpha[:, label_map["Bull"]]
P_Choppy = alpha[:, label_map["Choppy"]]
P_Crisis = alpha[:, label_map["Crisis"]]
regime   = [state_labels[np.argmax(alpha[t])] for t in range(len(X))]

out = pd.DataFrame({
    "P_Bull":   P_Bull,
    "P_Choppy": P_Choppy,
    "P_Crisis": P_Crisis,
    "regime":   regime,
}, index=data.index)
out.index.name = "date"

# ── Print sequence ───────────────────────────────────────────────────────────
print(f"\n{'Date':<10} {'P_Bull':>8} {'P_Choppy':>10} {'P_Crisis':>10} {'regime':<10}")
print("-" * 52)
for date, row in out.iterrows():
    print(f"{date.strftime('%Y-%m'):<10} {row['P_Bull']:>8.4f} {row['P_Choppy']:>10.4f} {row['P_Crisis']:>10.4f} {row['regime']:<10}")

# ── Regime distribution ───────────────────────────────────────────────────────
print(f"\n--- Regime distribution ---")
counts = out["regime"].value_counts()
for r in ["Bull", "Choppy", "Crisis"]:
    n = counts.get(r, 0)
    print(f"  {r:<8} : {n:>3} months  ({n/len(out)*100:.1f}%)")

# ── Validation vs BW ─────────────────────────────────────────────────────────
if BW_CSV.exists():
    bw = pd.read_csv(BW_CSV)
    bw["date"] = pd.to_datetime(bw["date"])
    out_reset  = out.reset_index()
    out_reset["date"] = pd.to_datetime(out_reset["date"].dt.to_period("M").dt.to_timestamp())
    merged = bw.merge(out_reset[["date","regime"]], on="date", suffixes=("_bw","_fwd"))
    matches = (merged["viterbi"] == merged["regime"]).sum()
    total   = len(merged)
    print(f"\n--- Validation vs BW gamma (eval_window_gamma.csv) ---")
    print(f"  Forward algo matches Viterbi : {matches} / {total}")
    diff = merged[merged["viterbi"] != merged["regime"]][["date","viterbi","regime","P_Bull","P_Choppy","P_Crisis"]]
    if not diff.empty:
        print(f"  Months that differ ({len(diff)}):")
        print(diff.to_string(index=False))

# ── Save ─────────────────────────────────────────────────────────────────────
out.to_parquet(OUT_PARQUET)
print(f"\nSaved parquet → {OUT_PARQUET}")

summary = {
    "eval_window": {"start": EVAL_START, "end": EVAL_END, "n_months": len(out)},
    "initial_state": {"dec2019_label": dec2019_label, "prior": prior.tolist()},
    "regime_counts": {r: int(counts.get(r, 0)) for r in ["Bull","Choppy","Crisis"]},
    "regime_pct":    {r: round(counts.get(r,0)/len(out)*100,2) for r in ["Bull","Choppy","Crisis"]},
}
with open(OUT_JSON, "w") as f:
    json.dump(summary, f, indent=2)
print(f"Saved json     → {OUT_JSON}")
