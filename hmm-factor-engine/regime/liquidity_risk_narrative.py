import pandas as pd
import numpy as np
import json
import sys
import os
from datetime import date

OUT_DIR  = "/home/ec2-user/nse-factor-engine/hmm-factor-engine/regime/data"
CAL_PATH = f"{OUT_DIR}/calibration.json"

UNIVERSES = ["nifty100", "nifty500", "niftymidcap150", "niftysmallcap250"]


# ─────────────────────────────────────────────
# TIER FUNCTIONS
# Each returns (tier_label, one_line_reading)
# ─────────────────────────────────────────────

def tier_rv(val, cal):
    p25, p75, p90 = cal["p25"], cal["p75"], cal["p90"]
    pct = round(val * 100, 1)
    if val < p25:
        return "calm",    f"Realized vol at {pct}% annualised — well within normal range"
    elif val < p75:
        return "moderate", f"Realized vol at {pct}% annualised — unremarkable"
    elif val < p90:
        return "elevated", f"Realized vol at {pct}% annualised — above median, stress building"
    else:
        return "extreme",  f"Realized vol at {pct}% annualised — systemic stress territory"


def tier_avg_corr(val, cal):
    p25, p75, p90 = cal["p25"], cal["p75"], cal["p90"]
    pct = round(val * 100, 1)
    if val < p25:
        return "calm",    f"Avg stock-index correlation {pct}% — stocks moving independently, macro calm"
    elif val < p75:
        return "moderate", f"Avg stock-index correlation {pct}% — normal co-movement"
    elif val < p90:
        return "elevated", f"Avg stock-index correlation {pct}% — macro-driven, herding beginning"
    else:
        return "extreme",  f"Avg stock-index correlation {pct}% — panic-driven herding, macro dominates"


def tier_vov(val, cal):
    p25, p75, p90 = cal["p25"], cal["p75"], cal["p90"]
    if val < p25:
        return "calm",    "VoV low — volatility regime stable, no transition in progress"
    elif val < p75:
        return "moderate", "VoV moderate — vol oscillating normally"
    elif val < p90:
        return "elevated", "VoV elevated — regime transition likely underway"
    else:
        return "extreme",  "VoV extreme — sharp regime shift, vol itself becoming volatile"


def tier_dispersion(val, cal):
    p25, p75, p95 = cal["p25"], cal["p75"], cal["p95"]
    pct = round(val * 100, 2)
    if val < p25:
        return "calm",    f"Cross-sectional dispersion {pct}% — stocks moving together, macro driven"
    elif val < p75:
        return "moderate", f"Cross-sectional dispersion {pct}% — normal stock divergence"
    elif val < p95:
        return "elevated", f"Cross-sectional dispersion {pct}% — stocks diverging, factor or sector stress"
    else:
        return "extreme",  f"Cross-sectional dispersion {pct}% — crash-day spike, extreme stock divergence"


def tier_amihud(val, cal):
    # val is raw (e.g. ~1e-12 for nifty100); cal stores magnitude post-abs
    # amihud is already positive — no flip needed
    p25, p75, p90 = cal["p25"], cal["p75"], cal["p90"]
    display = round(val * 1e10, 3)
    if val < p25:
        return "liquid",           f"Amihud {display} (x1e10) — deep market, low price impact"
    elif val < p75:
        return "normal",           f"Amihud {display} (x1e10) — normal liquidity"
    elif val < p90:
        return "illiquid",         f"Amihud {display} (x1e10) — elevated price impact, liquidity thinning"
    else:
        return "severely illiquid", f"Amihud {display} (x1e10) — severe illiquidity, large trades moving prices"


def tier_cs_spread(val, cal):
    # p10 catches compressed spread (slow-burn macro stress)
    # p90 catches spike (sudden liquidity event)
    p10, p75, p90 = cal["p10"], cal["p75"], cal["p90"]
    bps = round(val * 10000, 1)
    if val < p10:
        return "compressed", f"CS spread {bps}bps — spread compressed, likely slow-burn macro stress not a liquidity crisis"
    elif val < p75:
        return "normal",     f"CS spread {bps}bps — normal transaction cost"
    elif val < p90:
        return "wide",       f"CS spread {bps}bps — elevated transaction cost, liquidity thinning"
    else:
        return "spike",      f"CS spread {bps}bps — sudden liquidity event, bid-ask blowing out"


def tier_turnover(val, cal):
    # Inverted interpretation: low turnover = low participation
    p10, p75, p90 = cal["p10"], cal["p75"], cal["p90"]
    pct = round(val * 100, 3)
    if val < p10:
        return "low",     f"Turnover {pct}% — very thin participation, market quiet or disengaged"
    elif val < p75:
        return "normal",  f"Turnover {pct}% — normal market participation"
    elif val < p90:
        return "elevated", f"Turnover {pct}% — active market, above-average participation"
    else:
        return "surge",   f"Turnover {pct}% — volume surge, panic buying or selling"


def tier_drawdown(val, cal):
    # val is raw (negative). cal stores magnitude (abs).
    # We compare abs(val) against cal thresholds.
    mag = abs(val)
    p25, p75, p90 = cal["p25"], cal["p75"], cal["p90"]
    pct = round(val * 100, 1)   # negative, e.g. -16.8%
    if mag < p25:
        return "shallow",  f"Drawdown {pct}% — near recent highs, no structural damage"
    elif mag < p75:
        return "moderate", f"Drawdown {pct}% — moderate correction from 52-week high"
    elif mag < p90:
        return "deep",     f"Drawdown {pct}% — deep correction, meaningful distance from peak"
    else:
        return "severe",   f"Drawdown {pct}% — severe drawdown, market well below peak"


def tier_skew(val, cal):
    # More negative = crash-like = worse.
    # cal["p10"] is the most negative (crash-like) threshold on raw scale.
    # cal["p25"] is the second threshold.
    # cal["p75"] is where skew turns positive (rally-like).
    p10, p25, p75 = cal["p10"], cal["p25"], cal["p75"]
    rounded = round(val, 3)
    if val > p75:
        return "rally-like",          f"Skew +{rounded} — recent 60-day return distribution positively skewed, rally-like"
    elif val > p25:
        return "neutral",             f"Skew {rounded} — return distribution neutral, no strong directional memory"
    elif val > p10:
        return "crash-like",          f"Skew {rounded} — moderately negative skew, crash-like distribution memory"
    else:
        return "extreme crash memory", f"Skew {rounded} — strongly negative, extreme crash-like distribution (lags stress by ~60 days)"


# ─────────────────────────────────────────────
# TIER → SEVERITY SCORE (for overall summary)
# ─────────────────────────────────────────────

SEVERITY = {
    # risk measures
    "calm": 0, "moderate": 1, "elevated": 2, "extreme": 3,
    # liquidity measures
    "liquid": 0, "normal": 1, "illiquid": 2, "severely illiquid": 3,
    # cs_spread
    "compressed": 2, "wide": 2, "spike": 3,   # compressed also flagged
    # turnover
    "low": 1, "surge": 2,
    # drawdown
    "shallow": 0, "deep": 2, "severe": 3,
    # skew
    "rally-like": 0, "neutral": 1, "crash-like": 2, "extreme crash memory": 3,
}


# ─────────────────────────────────────────────
# OVERALL STORY BUILDER
# ─────────────────────────────────────────────

def build_overall_story(tiers, rows):
    """
    Synthesise a 2-3 sentence overall narrative from tier labels.
    tiers  : dict of measure -> tier_label
    rows   : dict of measure -> one_line_reading (for context)
    """
    rv_t      = tiers.get("rv", "moderate")
    corr_t    = tiers.get("avg_corr", "moderate")
    vov_t     = tiers.get("vov", "moderate")
    dd_t      = tiers.get("drawdown", "moderate")
    disp_t    = tiers.get("dispersion", "moderate")
    amihud_t  = tiers.get("amihud", "normal")
    cs_t      = tiers.get("cs_spread", "normal")
    turn_t    = tiers.get("turnover", "normal")
    skew_t    = tiers.get("skew", "neutral")

    # Risk stress level
    stress_scores = [
        SEVERITY.get(rv_t, 1),
        SEVERITY.get(corr_t, 1),
        SEVERITY.get(vov_t, 1),
        SEVERITY.get(dd_t, 1),
    ]
    avg_stress = sum(stress_scores) / len(stress_scores)

    # Liquidity stress level
    liq_scores = [
        SEVERITY.get(amihud_t, 1),
        SEVERITY.get(cs_t, 1),
        SEVERITY.get(turn_t, 1),
    ]
    avg_liq = sum(liq_scores) / len(liq_scores)

    # Sentence 1: Overall regime characterisation
    if avg_stress >= 2.5:
        stress_char = "under severe systemic stress"
    elif avg_stress >= 1.5:
        stress_char = "under meaningful risk stress"
    elif avg_stress >= 0.75:
        stress_char = "in a moderately elevated risk environment"
    else:
        stress_char = "in a calm, low-stress regime"

    if avg_liq >= 2.0:
        liq_char = "with liquidity significantly impaired"
    elif avg_liq >= 1.5:
        liq_char = "with some liquidity deterioration"
    elif cs_t == "compressed":
        liq_char = "with structurally adequate liquidity despite spread compression suggesting macro not liquidity stress"
    else:
        liq_char = "with adequate market liquidity"

    sentence1 = f"Market is {stress_char}, {liq_char}."

    # Sentence 2: Primary driver
    drivers = []
    if rv_t in ("elevated", "extreme") and corr_t in ("elevated", "extreme"):
        drivers.append("correlated macro selloff driving both realised vol and herding")
    elif rv_t in ("elevated", "extreme"):
        drivers.append("elevated realised volatility without broad herding — idiosyncratic stress")
    elif corr_t in ("elevated", "extreme"):
        drivers.append("macro-driven correlation spike without extreme volatility — positioning/sentiment shift")

    if dd_t in ("deep", "severe"):
        drivers.append(f"significant drawdown from peak ({rows.get('drawdown','').split('—')[0].strip()})")

    if disp_t == "extreme":
        drivers.append("crash-day dispersion spike — extreme stock-level divergence")
    elif disp_t == "elevated":
        drivers.append("elevated cross-sectional dispersion suggesting factor or sector rotation")

    if cs_t == "spike":
        drivers.append("bid-ask blowout indicating sudden liquidity event")
    elif cs_t == "compressed":
        drivers.append("spread compression consistent with slow-burn macro stress rather than a liquidity crisis")

    if turn_t == "surge":
        drivers.append("volume surge suggesting panic activity")

    if drivers:
        sentence2 = "Primary signal: " + "; ".join(drivers) + "."
    else:
        sentence2 = "No single dominant driver — conditions are broadly normal across measures."

    # Sentence 3: Skew / memory read
    if skew_t == "extreme crash memory":
        sentence3 = "Skew confirms recent 60-day window is strongly crash-like — this lags the stress event, not a leading signal."
    elif skew_t == "crash-like":
        sentence3 = "Skew mildly negative — some crash memory in the 60-day window, likely trailing a recent stress event."
    elif skew_t == "rally-like":
        sentence3 = "Skew positive — recent 60-day return distribution is rally-like, no crash memory."
    else:
        sentence3 = "Skew neutral — no strong directional memory in the recent return distribution."

    return f"{sentence1} {sentence2} {sentence3}"


# ─────────────────────────────────────────────
# MAIN NARRATIVE FUNCTION
# ─────────────────────────────────────────────

def generate_narrative(univ: str, query_date: str, cal: dict, df: pd.DataFrame) -> str:
    """
    Generate plain-English narrative for a given universe and date.

    Parameters
    ----------
    univ        : universe name e.g. 'nifty100'
    query_date  : 'YYYY-MM-DD'
    cal         : calibration dict for this universe
    df          : parquet DataFrame for this universe
    """
    dt = pd.Timestamp(query_date)

    # Find exact or nearest trading date
    if dt in df.index:
        actual_date = dt
    else:
        idx = df.index.get_indexer([dt], method="nearest")[0]
        actual_date = df.index[idx]
        print(f"  Note: {query_date} not a trading day — using nearest: {actual_date.date()}")

    row = df.loc[actual_date]

    # Run tier functions
    tier_fns = {
        "rv":         (tier_rv,         cal.get("rv",         {})),
        "avg_corr":   (tier_avg_corr,   cal.get("avg_corr",   {})),
        "vov":        (tier_vov,        cal.get("vov",        {})),
        "dispersion": (tier_dispersion, cal.get("dispersion", {})),
        "amihud":     (tier_amihud,     cal.get("amihud",     {})),
        "cs_spread":  (tier_cs_spread,  cal.get("cs_spread",  {})),
        "turnover":   (tier_turnover,   cal.get("turnover",   {})),
        "drawdown":   (tier_drawdown,   cal.get("drawdown",   {})),
        "skew":       (tier_skew,       cal.get("skew",       {})),
    }

    tiers = {}
    readings = {}

    for measure, (fn, measure_cal) in tier_fns.items():
        val = row.get(measure, np.nan)
        if pd.isna(val):
            tiers[measure]    = "unavailable"
            readings[measure] = f"{measure}: data not available for this date"
        else:
            tier, reading     = fn(val, measure_cal)
            tiers[measure]    = tier
            readings[measure] = reading

    overall = build_overall_story(tiers, readings)

    # Format output
    lines = [
        f"{'='*65}",
        f"  LIQUIDITY & RISK NARRATIVE",
        f"  Universe : {univ}",
        f"  Date     : {actual_date.date()}",
        f"{'='*65}",
        "",
        "OVERALL",
        "-------",
        overall,
        "",
        "RISK MEASURES",
        "-------------",
        f"  [{tiers['rv'].upper():20s}]  {readings['rv']}",
        f"  [{tiers['avg_corr'].upper():20s}]  {readings['avg_corr']}",
        f"  [{tiers['vov'].upper():20s}]  {readings['vov']}",
        f"  [{tiers['dispersion'].upper():20s}]  {readings['dispersion']}",
        f"  [{tiers['drawdown'].upper():20s}]  {readings['drawdown']}",
        f"  [{tiers['skew'].upper():20s}]  {readings['skew']}",
        "",
        "LIQUIDITY MEASURES",
        "------------------",
        f"  [{tiers['amihud'].upper():20s}]  {readings['amihud']}",
        f"  [{tiers['cs_spread'].upper():20s}]  {readings['cs_spread']}",
        f"  [{tiers['turnover'].upper():20s}]  {readings['turnover']}",
        "",
    ]
    return "\n".join(lines)


# ─────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────

def main():
    # Load calibration
    if not os.path.exists(CAL_PATH):
        print(f"ERROR: calibration not found at {CAL_PATH}")
        print("Run calibrate.py first.")
        sys.exit(1)

    with open(CAL_PATH) as f:
        cal_all = json.load(f)

    # Default: run all stress dates across all universes for review
    STRESS_DATES = [
        "2024-06-04",
        "2024-08-06",
        "2025-04-07",
        "2025-07-21",
        "2026-04-01",
        "2026-08-03",
    ]

    # Allow CLI override: python3 liquidity_risk_narrative.py nifty100 2025-04-07
    if len(sys.argv) == 3:
        target_univs = [sys.argv[1]]
        target_dates = [sys.argv[2]]
    else:
        target_univs = UNIVERSES
        target_dates = STRESS_DATES

    for univ in target_univs:
        if univ not in cal_all:
            print(f"ERROR: {univ} not in calibration. Available: {list(cal_all.keys())}")
            continue
        cal = cal_all[univ]
        df  = pd.read_parquet(f"{OUT_DIR}/liquidity_risk_{univ}.parquet")

        for d in target_dates:
            narrative = generate_narrative(univ, d, cal, df)
            print(narrative)


if __name__ == "__main__":
    main()
