# Nifty Portfolio Hedging Strategy — Backtest Results

## Overview

Systematic backtest of put option hedging strategies on a **₹1 Crore Nifty 50 portfolio**
over 25 years (2000–2026). Objective: find a hedging strategy that provides meaningful
downside protection during crashes without significantly dragging long-term returns.

**Spoiler: It exists. And it beats unhedged by 32%.**

---

## Chart

![Hedging Strategy Comparison](final_backtest_plot.png)

---

## Setup

| Parameter | Value |
|---|---|
| Portfolio | ₹1,00,00,000 (₹1 Crore) |
| Index | Nifty 50 |
| Period | Dec 2000 – Aug 2026 |
| Lot size | 65 units |
| Roll frequency | Annual (last trading day of year) |
| Option type | European put (Nifty index options) |
| Premium model | Black-Scholes with India VIX as sigma input |
| Vol scalar | 1.17x (implied vs realised vol gap, calibrated to live market) |
| Skew multiplier | 1.4x (accounts for OTM vol skew and bid-ask spread) |
| Tax on payout | 33% |
| Risk-free rate | 6.5% (India T-bill) |

### Data Sources

| Data | Source | Coverage |
|---|---|---|
| Nifty 50 daily close | Local parquet (NSE) | 1999–2026 |
| India VIX | yfinance ^INDIAVIX | 2008–2026 |
| Historical vol | 63-day rolling, annualised | 1999–2026 (fallback pre-2008) |

### BS Calibration
Calibrated against live market on Sep 2026 (Nifty ~24,400):
- 24,000 PE (ATM, Jun 2027): live = Rs 488.75 → implied vol = 13.20%
- 20,000 PE (OTM, Jun 2027): live = Rs 17.00  → implied vol = 13.23%
- Vol skew: near zero (0.03%) — calm market

---

## Winner: Momentum 20/30% OTM

### The Rule

At each year-end roll date:
IF Nifty > 200-day MA → buy 20% OTM annual put
IF Nifty < 200-day MA → buy 30% OTM annual put


### Results

| Metric | Value |
|---|---|
| Final hedged portfolio | Rs 34.3 Cr |
| Final unhedged portfolio | Rs 25.9 Cr |
| Outperformance | +Rs 8.4 Cr (+32%) |
| Total premium paid (25 yrs) | Rs 1.86 Cr |
| Total payout received (after tax) | Rs 1.79 Cr |
| Net hedge cost | ~Rs 0 (self-financing) |
| Average annual premium drag | 1.03% |
| Puts that paid out | 2 times (2007 and 2010) |

---

## Why It Works

1. Deep OTM puts cost only 0.5-1.5% per year vs Nifty CAGR of 14.5%
2. Momentum rule cuts premium by 60% in bear markets (30% OTM) when less protection needed
3. Crash payouts reinvested at market bottoms compound through recovery
4. Annual puts catch full crash cycles — real crashes take 12-18 months to bottom
5. Self-financing — two crash payouts covered 25 years of premiums

---

## What Does Not Work

| Strategy | Final Value | Why It Failed |
|---|---|---|
| ATM Put Annual | Rs 13.4 Cr | Costs 4-6%/yr — too expensive |
| Sigma-Gated Hedge | Rs 19.2 Cr | Skips protection when VIX high — exactly wrong |
| Drawdown-Triggered | Rs 21.6 Cr | Biggest crashes start from all-time highs |
| Cash Buffer 15% | Rs 28.7 Cr | 8% opportunity cost vs Nifty kills returns |
| Put Ladder | Rs 33.5 Cr | Complexity adds cost not value |
| Collar | Rs 5.4 Cr | Selling call caps all bull market upside |
| Partial Hedge 50% | Rs 30.3 Cr | Full hedge always better at these premium levels |
| Bear=CASH | Rs 11.5 Cr | Misses 2009 +88%, 2012 +32% recoveries |

---

## All Strategies Compared

| Rank | Strategy | Final Value | vs Unhedged | Avg Cost/yr |
|---|---|---|---|---|
| 1 | Momentum Bull=20% Bear=30% | Rs 34.3 Cr | +Rs 8.4 Cr | 1.03% |
| 2 | Momentum Bull=20% Bear=25% | Rs 32.7 Cr | +Rs 6.8 Cr | 1.23% |
| 3 | Fixed 30% OTM Annual | Rs 32.1 Cr | +Rs 6.3 Cr | 0.56% |
| 4 | Fixed 25% OTM Annual | Rs 31.8 Cr | +Rs 5.9 Cr | 0.91% |
| 5 | Fixed 20% OTM Annual | Rs 30.9 Cr | +Rs 5.1 Cr | 1.50% |
| — | Unhedged | Rs 25.9 Cr | baseline | 0% |
| ❌ | ATM Put Annual | Rs 13.4 Cr | -Rs 12.5 Cr | 5.75% |
| ❌ | Bear=CASH | Rs 11.5 Cr | -Rs 14.4 Cr | — |
| ❌ | Collar | Rs 5.4 Cr | -Rs 20.5 Cr | — |

---

## Key Crash Behaviour

| Crash | Nifty Fall | Regime | Put Strike | Payout |
|---|---|---|---|---|
| GFC 2007-2008 | -57% | BULL (at peak) | 20% OTM | Rs 1.41 Cr |
| Eurozone 2010-2011 | -27% | BULL | 20% OTM | Rs 38.9 L |
| COVID 2020 | -38% intra-year | BULL | 20% OTM | Rs 0 (recovered by Dec) |

---

## Practical Implementation

At each December year-end:

Check: Nifty close vs 200-day MA
BULL (above MA) → buy 20% OTM December put
BEAR (below MA) → buy 30% OTM December put
Strike:
BULL: round(Nifty * 0.80, -2)
BEAR: round(Nifty * 0.70, -2)
Lots: round(portfolio_value / (Nifty * 65))
Roll: buy on last trading day of December
close position on last trading day of next December

### Real Market Considerations
- Liquidity: 20-30% OTM annual puts are thinly traded — use limit orders
- Bid-ask: adds 1-3% to premium, already in 1.4x skew multiplier
- Lot size: currently 65 (was 75 before 2024, 50 before that)
- Tax: put payouts as short-term capital gains (33% assumed)
- Options are European — only expiry payout matters, no early exercise

---

## Files

hedging/
├── README.md # this file
├── final_backtest_plot.png # main comparison chart
├── nifty_close.parquet # Nifty 50 daily closes 1999-2026
├── nifty_with_vol.parquet # + rolling vol + VIX sigma merged
├── indiavix.parquet # India VIX 2008-2026
├── backtest_results.parquet # raw quarterly backtest
├── backtest_summary.csv # quarterly summary
└── backtest_annual.csv # annual summary


---

## Conclusion

A simple momentum-based annual put buying strategy on Nifty 50 — costing only ~1% per
year on average — outperformed a pure unhedged Nifty portfolio by 32% over 25 years,
while providing meaningful protection during GFC 2008 and Eurozone 2011.

The net cost of hedging over 25 years was essentially zero — crash payouts covered the premiums.

**The best hedge is cheap, always on, and lets equity do the heavy lifting.**

---

*Backtest: September 2026 | Data: NSE Nifty 50 (1999-2026), India VIX (2008-2026)*
*Model: Black-Scholes, India VIX sigma, 1.17x vol scalar, 1.4x skew, 33% tax*
