"""
Market Movement PDF Report Generator
Run from repo root: python3 market_movement/generate_market_report.py
Reads : market_movement/data/market_movement_metrics.parquet
Writes: market_movement/data/market_movement_report_{RUN_DATE}.pdf
"""

import sys
import pandas as pd
import numpy as np
from datetime import date
from pathlib import Path

try:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
    from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
except ImportError:
    sys.exit("ERROR: reportlab not installed.\nRun: pip3 install reportlab --break-system-packages")

# ── Paths ─────────────────────────────────────────
if not Path("signals").is_dir():
    sys.exit("ERROR: Run from repo root (nse-factor-engine/)")

DATA_DIR   = Path("market_movement/data")
INPUT_PATH = DATA_DIR / "market_movement_metrics.parquet"
RUN_DATE   = date.today().strftime("%d%m%Y")
OUT_PATH   = DATA_DIR / f"market_movement_report_{RUN_DATE}.pdf"

if not INPUT_PATH.exists():
    sys.exit(f"ERROR: {INPUT_PATH} not found. Run compute_market_metrics.py first.")

# ── Data ──────────────────────────────────────────
df       = pd.read_parquet(INPUT_PATH)
as_of    = pd.to_datetime(df["as_of_date"].iloc[0]).strftime("%d %b %Y")
run_date = pd.to_datetime(df["run_date"].iloc[0]).strftime("%d %b %Y")

def get(symbol, col):
    row = df[df["symbol"] == symbol]
    if row.empty: return None
    val = row[col].iloc[0]
    return None if (isinstance(val, float) and np.isnan(val)) else val

# ── Ticker config ─────────────────────────────────
BROAD = [("^NSEI","Nifty 50"), ("^CRSLDX","Nifty 500")]
SECTORAL = [
    ("^NSEBANK",            "Nifty Bank"),
    ("^CNXIT",              "Nifty IT"),
    ("^CNXPHARMA",          "Nifty Pharma"),
    ("^NSEMDCP50",          "Nifty Midcap 50"),
    ("NIFTY_MIDCAP_100.NS", "Nifty Midcap 100"),
    ("SML100CASE.NS",       "Nifty Smallcap 100"),
]

# ── Fixed phrase maps ─────────────────────────────
STAGE_LABEL = {
    "STAGE_1_ACCUMULATION":         "Stage 1  Accumulation",
    "STAGE_2_ADVANCING":            "Stage 2  Advancing",
    "STAGE_3_DISTRIBUTION":         "Stage 3  Distribution",
    "STAGE_4_TRANSITION_BREAKDOWN": "Stage 4  Breakdown",
    "STAGE_4_DECLINING":            "Stage 4  Declining",
    "TRANSITION_ZONE":              "Transition Zone",
    "INSUFFICIENT_DATA":            "Insufficient Data",
}
STAGE_DESC = {
    "STAGE_1_ACCUMULATION":         "Range-bound, SMA slope flat. No confirmed direction.",
    "STAGE_2_ADVANCING":            "Above SMA, slope rising. Confirmed uptrend.",
    "STAGE_3_DISTRIBUTION":         "Whipsawing around SMA, slope flat. Topping pattern.",
    "STAGE_4_TRANSITION_BREAKDOWN": "Below support, slope negative. Breakdown underway.",
    "STAGE_4_DECLINING":            "Below SMA, slope negative. Confirmed downtrend.",
    "TRANSITION_ZONE":              "No confirmed stage. Structural clarity lacking.",
    "INSUFFICIENT_DATA":            "Insufficient history to classify.",
}
STAGE_COLOR = {
    "STAGE_1_ACCUMULATION":         colors.HexColor("#B8860B"),
    "STAGE_2_ADVANCING":            colors.HexColor("#2E7D32"),
    "STAGE_3_DISTRIBUTION":         colors.HexColor("#E65100"),
    "STAGE_4_TRANSITION_BREAKDOWN": colors.HexColor("#B71C1C"),
    "STAGE_4_DECLINING":            colors.HexColor("#B71C1C"),
    "TRANSITION_ZONE":              colors.HexColor("#37474F"),
    "INSUFFICIENT_DATA":            colors.HexColor("#37474F"),
}
VIX_TIER_DESC = {
    "GOING_UP_CONFIRMED":   "Systemic fear rising — confirmed spike.",
    "DEVELOPING_UPTREND":   "Fear building — institutional hedging picking up.",
    "STABLE":               "Fear flat — risk perception unchanged.",
    "DEVELOPING_DOWNTREND": "Fear easing — option premiums cooling.",
    "GOING_DOWN_CONFIRMED": "Fear exiting decisively — risk-off trade unwinding.",
    "INSUFFICIENT_DATA":    "Insufficient data.",
}
VIX_TIER_COLOR = {
    "GOING_UP_CONFIRMED":   colors.HexColor("#B71C1C"),
    "DEVELOPING_UPTREND":   colors.HexColor("#E65100"),
    "STABLE":               colors.HexColor("#37474F"),
    "DEVELOPING_DOWNTREND": colors.HexColor("#2E7D32"),
    "GOING_DOWN_CONFIRMED": colors.HexColor("#1B5E20"),
    "INSUFFICIENT_DATA":    colors.HexColor("#37474F"),
}
COMBO_DESC = {
    "CONFIDENCE_HEALTHY_RALLY": "VIX falling, market rising — stable, climbing conditions. No immediate danger.",
    "PANIC_REAL_CRASH":         "VIX spiking, market falling — fear is real. Do not catch a falling knife.",
    "FOMO_PRE_EVENT_ANXIETY":   "VIX rising, market rising — overstretching. Sharp reversal possible.",
    "COMPLACENCY_BOREDOM":      "VIX flat, market flat — range-bound. Big money is waiting.",
    "VIX_UP_MARKET_STABLE":     "Fear rising, market holding — stress accumulating. Watch closely.",
    "VIX_STABLE_MARKET_UP":     "Market rising on low fear — clean, unhedged rally.",
    "VIX_STABLE_MARKET_DOWN":   "Market falling on low fear — orderly decline, not panic.",
    "VIX_DOWN_MARKET_STABLE":   "Fear fading, market flat — calm returning, no breakout yet.",
    "VIX_DOWN_MARKET_DOWN":     "VIX dropping as market falls — bottom may be near.",
    "INSUFFICIENT_DATA":        "Insufficient data.",
}
COMBO_COLOR = {
    "CONFIDENCE_HEALTHY_RALLY": colors.HexColor("#1B5E20"),
    "PANIC_REAL_CRASH":         colors.HexColor("#7F0000"),
    "FOMO_PRE_EVENT_ANXIETY":   colors.HexColor("#BF360C"),
    "COMPLACENCY_BOREDOM":      colors.HexColor("#37474F"),
    "VIX_UP_MARKET_STABLE":     colors.HexColor("#E65100"),
    "VIX_STABLE_MARKET_UP":     colors.HexColor("#2E7D32"),
    "VIX_STABLE_MARKET_DOWN":   colors.HexColor("#B8860B"),
    "VIX_DOWN_MARKET_STABLE":   colors.HexColor("#37474F"),
    "VIX_DOWN_MARKET_DOWN":     colors.HexColor("#B8860B"),
    "INSUFFICIENT_DATA":        colors.HexColor("#37474F"),
}

# ── Colors ────────────────────────────────────────
BG     = colors.HexColor("#0D1117")
CARD   = colors.HexColor("#161B22")
BORDER = colors.HexColor("#30363D")
TEXT   = colors.HexColor("#E6EDF3")
MUTED  = colors.HexColor("#8B949E")
ACCENT = colors.HexColor("#58A6FF")
GREEN  = colors.HexColor("#3FB950")
RED    = colors.HexColor("#F85149")

# ── Styles ────────────────────────────────────────
def S(name, **kw):
    base = dict(fontName="Helvetica", fontSize=11, textColor=TEXT, leading=15, alignment=TA_LEFT)
    base.update(kw)
    return ParagraphStyle(name, **base)

S_TITLE = S("t",  fontName="Helvetica-Bold", fontSize=20, textColor=ACCENT, leading=24)
S_SUB   = S("s",  fontSize=10, textColor=MUTED, leading=13)
S_SEC   = S("sc", fontName="Helvetica-Bold", fontSize=11, textColor=ACCENT, leading=14)
S_NAME  = S("n",  fontName="Helvetica-Bold", fontSize=11, textColor=TEXT, leading=14)
S_DESC  = S("d",  fontSize=10, textColor=MUTED, leading=13)
S_PILL  = S("pl", fontName="Helvetica-Bold", fontSize=9, textColor=colors.white,
            leading=11, alignment=TA_CENTER)
S_RIGHT = S("r",  fontName="Helvetica-Bold", fontSize=11, textColor=TEXT,
            leading=14, alignment=TA_RIGHT)
S_RMUT  = S("rm", fontSize=10, textColor=MUTED, leading=13, alignment=TA_RIGHT)
S_COMBO = S("cb", fontName="Helvetica-Bold", fontSize=15, textColor=colors.white,
            leading=19, alignment=TA_CENTER)
S_CDSUB = S("cs", fontSize=10, textColor=colors.HexColor("#CCCCCC"),
            leading=13, alignment=TA_CENTER)
S_FOOT  = S("ft", fontSize=8, textColor=MUTED, alignment=TA_CENTER, leading=10)

# ── Helpers ───────────────────────────────────────
def fmt_ret(v):
    if v is None: return "—"
    return f"{v:+.2f}%"
def fmt_prox(v):
    if v is None: return "—"
    if v >= 1.0:  return "At 52W high"
    return f"{v*100:.1f}% of 52W high"
def ret_color(v):
    if v is None: return MUTED
    return GREEN if v > 0 else (RED if v < 0 else MUTED)

def card_style():
    return TableStyle([
        ("BACKGROUND",    (0,0),(-1,-1), CARD),
        ("BOX",           (0,0),(-1,-1), 0.4, BORDER),
        ("TOPPADDING",    (0,0),(-1,-1), 7),
        ("BOTTOMPADDING", (0,0),(-1,-1), 7),
        ("LEFTPADDING",   (0,0),(-1,-1), 8),
        ("RIGHTPADDING",  (0,0),(-1,-1), 8),
        ("VALIGN",        (0,0),(-1,-1), "MIDDLE"),
    ])

def pill(label, color):
    t = Table([[Paragraph(label, S_PILL)]], colWidths=[38*mm])
    t.setStyle(TableStyle([
        ("BACKGROUND",    (0,0),(-1,-1), color),
        ("TOPPADDING",    (0,0),(-1,-1), 4),
        ("BOTTOMPADDING", (0,0),(-1,-1), 4),
        ("LEFTPADDING",   (0,0),(-1,-1), 4),
        ("RIGHTPADDING",  (0,0),(-1,-1), 4),
        ("VALIGN",        (0,0),(-1,-1), "MIDDLE"),
    ]))
    return t

def index_row(sym, name):
    state = get(sym, "weinstein_state") or "INSUFFICIENT_DATA"
    ret   = get(sym, "ret_21d_pct")
    prox  = get(sym, "proximity_52w_high")
    rc    = ret_color(ret)

    inner = Table([[
        pill(STAGE_LABEL.get(state, state), STAGE_COLOR.get(state, MUTED)),
        [Paragraph(f"<b>{name}</b>  <font color='#8B949E'>{sym}</font>", S_NAME),
         Paragraph(STAGE_DESC.get(state, ""), S_DESC)],
        [Paragraph(f"<font color='#{rc.hexval()[2:]}'>{fmt_ret(ret)}</font>", S_RIGHT),
         Paragraph(fmt_prox(prox), S_RMUT)],
    ]], colWidths=[40*mm, 98*mm, 32*mm])
    inner.setStyle(TableStyle([
        ("VALIGN",       (0,0),(-1,-1), "MIDDLE"),
        ("LEFTPADDING",  (0,0),(-1,-1), 0),
        ("RIGHTPADDING", (0,0),(-1,-1), 0),
        ("TOPPADDING",   (0,0),(-1,-1), 0),
        ("BOTTOMPADDING",(0,0),(-1,-1), 0),
        ("ALIGN",        (2,0),(2,-1), "RIGHT"),
    ]))
    outer = Table([[inner]], colWidths=[174*mm])
    outer.setStyle(card_style())
    return outer

# ── Build PDF ─────────────────────────────────────
doc = SimpleDocTemplate(str(OUT_PATH), pagesize=A4,
      leftMargin=18*mm, rightMargin=18*mm, topMargin=14*mm, bottomMargin=12*mm)

def on_page(canvas, doc):
    canvas.saveState()
    canvas.setFillColor(BG)
    canvas.rect(0, 0, A4[0], A4[1], fill=1, stroke=0)
    canvas.restoreState()

SP = lambda n: Spacer(1, n*mm)
HR = lambda: HRFlowable(width="100%", thickness=0.4, color=BORDER, spaceAfter=2)

story = []

# Header
story += [
    Paragraph("NSE Factor Engine — Market Movement", S_TITLE),
    Paragraph(f"Data as of {as_of}  ·  Generated {run_date}", S_SUB),
    SP(3), HR(), SP(2),
]

# Broad Market
story += [Paragraph("BROAD MARKET", S_SEC), SP(1.5)]
for sym, name in BROAD:
    story += [index_row(sym, name), SP(1.5)]

story += [SP(2)]

# Sectoral
story += [Paragraph("SECTORAL", S_SEC), SP(1.5)]
for sym, name in SECTORAL:
    story += [index_row(sym, name), SP(1.5)]

story += [SP(2)]

# VIX
vix_sym   = "^INDIAVIX"
vix_ret   = get(vix_sym, "ret_21d_pct")
vix_lvl   = get(vix_sym, "close")
vix_5tier = get(vix_sym, "vix_5tier") or "INSUFFICIENT_DATA"
vc        = ret_color(vix_ret)

story += [Paragraph("VOLATILITY", S_SEC), SP(1.5)]
vix_inner = Table([[
    pill(vix_5tier.replace("_"," "), VIX_TIER_COLOR.get(vix_5tier, MUTED)),
    [Paragraph("<b>India VIX</b>  <font color='#8B949E'>^INDIAVIX</font>", S_NAME),
     Paragraph(VIX_TIER_DESC.get(vix_5tier, ""), S_DESC)],
    [Paragraph(f"<font color='#{vc.hexval()[2:]}'>{fmt_ret(vix_ret)}</font>", S_RIGHT),
     Paragraph(f"Level: {vix_lvl:.2f}" if vix_lvl else "—", S_RMUT)],
]], colWidths=[40*mm, 98*mm, 32*mm])
vix_inner.setStyle(TableStyle([
    ("VALIGN",       (0,0),(-1,-1), "MIDDLE"),
    ("LEFTPADDING",  (0,0),(-1,-1), 0),
    ("RIGHTPADDING", (0,0),(-1,-1), 0),
    ("TOPPADDING",   (0,0),(-1,-1), 0),
    ("BOTTOMPADDING",(0,0),(-1,-1), 0),
    ("ALIGN",        (2,0),(2,-1), "RIGHT"),
]))
vix_outer = Table([[vix_inner]], colWidths=[174*mm])
vix_outer.setStyle(card_style())
story += [vix_outer, SP(2)]

# Regime combo card
combo      = get(vix_sym, "combo_state") or "INSUFFICIENT_DATA"
mkt_3tier  = get(vix_sym, "market_3tier") or "—"
mkt_ret    = get(vix_sym, "market_ret_21d_pct")
combo_col  = COMBO_COLOR.get(combo, MUTED)
combo_desc = COMBO_DESC.get(combo, combo)

story += [Paragraph("REGIME SIGNAL", S_SEC), SP(1.5)]

sub_row = Table([[
    Table([[
        Paragraph("VIX", S("vl", fontSize=9, textColor=colors.HexColor("#AAAAAA"),
                  leading=11, alignment=TA_CENTER)),
        Paragraph(vix_5tier.replace("_"," "),
                  S("vv", fontName="Helvetica-Bold", fontSize=10,
                  textColor=colors.white, leading=13, alignment=TA_CENTER)),
    ]], colWidths=[83*mm]),
    Table([[
        Paragraph("MARKET", S("ml", fontSize=9, textColor=colors.HexColor("#AAAAAA"),
                  leading=11, alignment=TA_CENTER)),
        Paragraph(f"{mkt_3tier.replace('_',' ')}  ({fmt_ret(mkt_ret)})",
                  S("mv", fontName="Helvetica-Bold", fontSize=10,
                  textColor=colors.white, leading=13, alignment=TA_CENTER)),
    ]], colWidths=[83*mm]),
]], colWidths=[83*mm, 83*mm])
sub_row.setStyle(TableStyle([
    ("VALIGN",       (0,0),(-1,-1), "MIDDLE"),
    ("LEFTPADDING",  (0,0),(-1,-1), 0),
    ("RIGHTPADDING", (0,0),(-1,-1), 0),
    ("TOPPADDING",   (0,0),(-1,-1), 0),
    ("BOTTOMPADDING",(0,0),(-1,-1), 0),
]))

combo_card = Table([
    [Paragraph(combo.replace("_"," "), S_COMBO)],
    [Paragraph(combo_desc, S_CDSUB)],
    [SP(1)],
    [sub_row],
], colWidths=[172*mm])
combo_card.setStyle(TableStyle([
    ("BACKGROUND",    (0,0),(-1,-1), combo_col),
    ("TOPPADDING",    (0,0),(-1,-1), 10),
    ("BOTTOMPADDING", (0,0),(-1,-1), 10),
    ("LEFTPADDING",   (0,0),(-1,-1), 12),
    ("RIGHTPADDING",  (0,0),(-1,-1), 12),
    ("ALIGN",         (0,0),(-1,-1), "CENTER"),
    ("VALIGN",        (0,0),(-1,-1), "MIDDLE"),
]))
story += [combo_card, SP(3), HR()]
story += [Paragraph(
    "NSE Factor Engine  ·  Stage 8  ·  market_movement/compute_market_metrics.py  ·  Signal only. Not investment advice.",
    S_FOOT
)]

doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
print(f"Saved: {OUT_PATH}")
