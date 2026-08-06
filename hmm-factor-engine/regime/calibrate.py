import pandas as pd
import numpy as np
import json
import os

OUT_DIR  = "/home/ec2-user/nse-factor-engine/hmm-factor-engine/regime/data"
CAL_PATH = f"{OUT_DIR}/calibration.json"

UNIVERSES = [
    "nifty100",
    "nifty500",
    "niftymidcap150",
    "niftysmallcap250",
]

# Percentile breakpoints used per measure.
# drawdown is stored as magnitude (abs value) so all measures read
# "higher percentile = more stress" consistently.
BREAKPOINTS = {
    "rv":         [25, 75, 90],
    "avg_corr":   [25, 75, 90],
    "vov":        [25, 75, 90],
    "dispersion": [25, 75, 95],   # p95 for crash-day spike tier
    "amihud":     [25, 75, 90],
    "cs_spread":  [10, 75, 90],   # p10 lower band catches compressed spread
    "turnover":   [10, 75, 90],   # p10 catches very low participation
    "drawdown":   [25, 75, 90],   # computed on magnitude (abs), stored as magnitude
    "skew":       [10, 25, 75],   # inverted: low percentile = crash-like
}

def compute_calibration(df, univ_name):
    cal = {}
    for measure, pct_list in BREAKPOINTS.items():
        if measure not in df.columns:
            print(f"  WARNING: {measure} not found in {univ_name}, skipping")
            continue

        series = df[measure].dropna()

        if measure == "drawdown":
            # Flip to magnitude so higher = worse, consistent with all others
            series = series.abs()

        if measure == "skew":
            # Skew is inverted: more negative = more crash-like = worse.
            # Flip so higher magnitude of negativity gets higher percentile rank.
            # We store thresholds on the ORIGINAL scale (negative values)
            # but compute percentiles on the flipped series so p90 = most crash-like.
            # Store raw percentile values (negative) for lookup in narrative.
            raw_pcts = np.percentile(series.dropna(), pct_list).tolist()
            # pct_list for skew = [10, 25, 75]
            # On original (unflipped) scale these will be negative numbers
            # We store them directly — narrative compares raw skew against these
            cal[measure] = {
                f"p{p}": round(v, 6)
                for p, v in zip(pct_list, np.percentile(df[measure].dropna(), pct_list))
            }
            # Also store median and p5/p95 for context
            cal[measure]["p5"]  = round(float(np.percentile(df[measure].dropna(), 5)), 6)
            cal[measure]["p50"] = round(float(np.percentile(df[measure].dropna(), 50)), 6)
            cal[measure]["p95"] = round(float(np.percentile(df[measure].dropna(), 95)), 6)
            continue

        pct_values = np.percentile(series, pct_list)
        cal[measure] = {
            f"p{p}": round(float(v), 8)
            for p, v in zip(pct_list, pct_values)
        }
        # Store p5, p50, p95 for context / display
        cal[measure]["p5"]  = round(float(np.percentile(series, 5)), 8)
        cal[measure]["p50"] = round(float(np.percentile(series, 50)), 8)
        cal[measure]["p95"] = round(float(np.percentile(series, 95)), 8)

    return cal


def main():
    result = {}

    for univ in UNIVERSES:
        path = f"{OUT_DIR}/liquidity_risk_{univ}.parquet"
        print(f"\nCalibrating: {univ}")
        df = pd.read_parquet(path)
        print(f"  Shape: {df.shape}  Date range: {df.index.min().date()} → {df.index.max().date()}")

        cal = compute_calibration(df, univ)
        result[univ] = cal

        # Print summary for verification
        for measure, vals in cal.items():
            print(f"  {measure:15s}: {vals}")

    # Save
    with open(CAL_PATH, "w") as f:
        json.dump(result, f, indent=2)

    print(f"\nCalibration saved → {CAL_PATH}")
    print(f"Universes: {list(result.keys())}")
    print(f"Measures per universe: {list(result[UNIVERSES[0]].keys())}")


if __name__ == "__main__":
    main()
