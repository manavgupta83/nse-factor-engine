"""
build_regime_html_pdf.py
Reads the same regime_consolidated_<date>.json as build_regime_pdf.py,
transforms it to the template schema, and renders a PDF via template.html
(Jinja2 + headless Chromium / Playwright).

Usage:
    python3 build_regime_html_pdf.py                    # latest consolidated JSON
    python3 build_regime_html_pdf.py --date 2026-08-07  # specific date
    python3 build_regime_html_pdf.py --date 2026-08-07 --out /tmp/report.pdf
"""

import argparse
import json
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, BaseLoader
from playwright.sync_api import sync_playwright

# ---------- paths -----------------------------------------------------------
REGIME_DATA = Path("/home/ec2-user/nse-factor-engine/hmm-factor-engine/regime/data")
TEMPLATE    = Path(__file__).parent / "template.html"

# ---------- mappings --------------------------------------------------------
UNIVERSE_ORDER = ["nifty100", "nifty500", "niftymidcap150", "niftysmallcap250"]
UNIVERSE_NAMES = {
    "nifty100":         "Nifty 100",
    "nifty500":         "Nifty 500",
    "niftymidcap150":   "Nifty Midcap 150",
    "niftysmallcap250": "Nifty Smallcap 250",
}
RISK_MEASURES = ["rv", "avg_corr", "vov", "dispersion", "drawdown", "skew"]
LIQ_MEASURES  = ["amihud", "cs_spread", "turnover"]
MEASURE_LABELS = {
    "rv":         "Realised Volatility",
    "avg_corr":   "Avg Correlation",
    "vov":        "Vol of Vol",
    "dispersion": "Dispersion",
    "drawdown":   "Drawdown",
    "skew":       "Skew",
    "amihud":     "Amihud Illiquidity",
    "cs_spread":  "CS Spread",
    "turnover":   "Turnover",
}
TIER_CLASS = {
    "CALM":       "tag-neutral",
    "NORMAL":     "tag-neutral",
    "NEUTRAL":    "tag-neutral",
    "SHALLOW":    "tag-neutral",
    "MODERATE":   "tag-outline",
    "RALLY-LIKE": "tag-accent",
    "ELEVATED":   "tag-outline",
    "COMPRESSED": "tag-outline",
    "ILLIQUID":   "tag-accent",
    "SEVERE":     "tag-accent",
    "EXTREME":    "tag-accent",
    "CRISIS":     "tag-accent",
    "HIGH":       "tag-accent",
}

# ---------- helpers ---------------------------------------------------------
def fmt_date(d):
    """'2026-08-07'  ->  '2026-Aug-07'"""
    try:
        return datetime.strptime(d, "%Y-%m-%d").strftime("%Y-%b-%d")
    except Exception:
        return d

def fmt_month(m):
    """'2026-08'  ->  'Aug-2026'"""
    try:
        return datetime.strptime(m, "%Y-%m").strftime("%b-%Y")
    except Exception:
        return m

def tier_class(tier):
    return TIER_CLASS.get((tier or "").upper(), "tag-neutral")

# ---------- transform -------------------------------------------------------
def transform(consolidated):
    """Convert regime_consolidated schema -> template.html schema."""
    meta  = consolidated["meta"]
    hmm   = consolidated["hmm"]
    univs = consolidated["universes"]

    indices = []
    for key in UNIVERSE_ORDER:
        if key not in univs:
            continue
        udata    = univs[key]
        narr     = udata["liquidity_risk_narrative"]
        measures = narr.get("measures", {})

        def rows(keys):
            return [
                {
                    "measure": MEASURE_LABELS.get(k, k),
                    "tier":    measures[k].get("tier", ""),
                    "reading": measures[k].get("reading", ""),
                }
                for k in keys if k in measures
            ]

        indices.append({
            "name":               UNIVERSE_NAMES.get(key, key),
            "as_of":              fmt_date(udata["narrative_date"]),
            "overall_assessment": narr.get("overall", ""),
            "risk_measures":      rows(RISK_MEASURES),
            "liquidity_measures": rows(LIQ_MEASURES),
        })

    return {
        "generated_date": fmt_date(meta["run_date"]),
        "regime": {
            "signal":             hmm["regime"].upper(),
            "conviction":         f"{hmm['regime_conviction'].upper()} CONVICTION",
            "month":              fmt_month(hmm["as_of_month"]),
            "bull_probability":   round(hmm["P_Bull"]   * 100, 2),
            "choppy_probability": round(hmm["P_Choppy"] * 100, 2),
            "crisis_probability": round(hmm["P_Crisis"] * 100, 2),
            "synthesis":          hmm["regime_synthesis"],
        },
        "indices": indices,
    }

# ---------- load JSON -------------------------------------------------------
def load_consolidated(date_str=None):
    if date_str:
        path = REGIME_DATA / f"regime_consolidated_{date_str}.json"
        if not path.exists():
            raise FileNotFoundError(f"Not found: {path}")
        return json.loads(path.read_text())
    files = sorted(REGIME_DATA.glob("regime_consolidated_*.json"))
    if not files:
        raise FileNotFoundError(f"No consolidated JSON in {REGIME_DATA}")
    return json.loads(files[-1].read_text())

# ---------- render ----------------------------------------------------------
def render(data, template_path, out_pdf):
    env = Environment(loader=BaseLoader())
    env.filters["tier_class"] = tier_class
    html = env.from_string(Path(template_path).read_text()).render(**data)

    html_path = Path(out_pdf).with_suffix(".html")
    html_path.write_text(html, encoding="utf-8")

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page    = browser.new_page()
        page.goto(html_path.resolve().as_uri())
        page.wait_for_load_state("networkidle")   # lets Google Fonts load
        page.pdf(
            path=str(out_pdf),
            format="A4",
            print_background=True,
            margin={"top": "0", "bottom": "0", "left": "0", "right": "0"},
        )
        browser.close()

    print(f"PDF  -> {out_pdf}")
    print(f"HTML -> {html_path}  (keep for debugging)")

# ---------- CLI -------------------------------------------------------------
def parse_args():
    p = argparse.ArgumentParser(
        description="Render regime report PDF from consolidated JSON (Claude Design template)")
    p.add_argument("--date", "-d", default=None,
                   help="Run date YYYY-MM-DD (default: latest)")
    p.add_argument("--out",  "-o", default=None,
                   help="Output PDF path")
    p.add_argument("--template", "-t", default=str(TEMPLATE),
                   help=f"Path to template.html (default: {TEMPLATE})")
    return p.parse_args()

def main():
    args         = parse_args()
    consolidated = load_consolidated(args.date)
    run_date     = consolidated["meta"]["run_date"]
    data         = transform(consolidated)
    out_pdf      = Path(args.out) if args.out \
                   else REGIME_DATA / f"regime_report_design_{run_date}.pdf"
    render(data, args.template, out_pdf)

if __name__ == "__main__":
    main()
