# Nifty Portfolio Hedging Strategy — Backtest Results

## Overview

Systematic backtest of put option hedging strategies on a **Rs 1 Crore Nifty 50 portfolio**
over 25 years (2000–2026). Objective: find a hedging strategy that provides meaningful
downside protection during crashes without significantly dragging long-term returns.

**Conclusion: No systematic put-buying strategy robustly beats unhedged over the long run.**

---

## Chart

![Hedging Strategy Comparison](final_backtest_plot.png)

---

## Setup

| Parameter | Value |
|---|---|
| Portfolio | Rs 1,00,00,000 (Rs 1 Crore) |
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

## Strategies Tested

### 1. OTM Sweep (5% to 30%)
Tested all OTM levels on annual roll. Found 20-30% OTM is the sweet spot —
cheap enough to not drag returns, deep enough to catch real crashes.

### 2. Sigma-Gated Hedge
Skip buying put when VIX is high. FAILED — skips protection exactly when
market is most nervous. 2007 had sigma=0.36, gate fired, no put bought,
then 2008 crashed 57% with zero protection.

### 3. Variable OTM Based on Sigma
Buy closer OTM when sigma low, deeper OTM when sigma high. Decent but
beaten by the momentum rule consistently.

### 4. Momentum Gated Hedge
Buy 20% OTM when Nifty above 200MA (bull), 30% OTM when below (bear).
Appeared to be the winner on December roll dates. See stability test below.

### 5. Partial Hedge
Hedge 25-75% of portfolio. Full hedge always dominates — premium is already
cheap enough that reducing coverage just reduces protection without saving much cost.

### 6. Put Ladder
Buy two strikes simultaneously (e.g. 15% + 30% OTM). Complexity adds cost
not value. Single well-chosen strike always wins.

### 7. Drawdown-Triggered Hedge
Buy put only after Nifty falls 5-10% from 52-week peak. FAILED — biggest
crashes start from all-time highs. 2007: Nifty at peak, zero drawdown,
no put bought, then -57%.

### 8. Cash Buffer + Tail Hedge
Keep 10-15% in liquid funds, deploy after crash. FAILED — liquid fund earns
6.5%/yr vs Nifty 14.5% CAGR. 8% annual opportunity cost compounded over
25 years is devastating. Put at 1%/yr is far more efficient than cash at 8% drag.

---

## The Critical Stability Test

After finding that the December Momentum 20/30% strategy beat unhedged by 32%,
we tested all 12 calendar months as roll start dates. Results:

| Month | Momentum Hedged | Unhedged | Outperforms? |
|---|---|---|---|
| Jan | Rs 34.0 Cr | Rs 26.5 Cr | YES |
| Feb | Rs 22.8 Cr | Rs 23.6 Cr | NO |
| Mar | Rs 21.7 Cr | Rs 24.0 Cr | NO |
| Apr | Rs 15.3 Cr | Rs 16.3 Cr | NO |
| May | Rs 14.3 Cr | Rs 22.9 Cr | NO |
| Jun | Rs 13.8 Cr | Rs 23.6 Cr | NO |
| Jul | Rs 13.8 Cr | Rs 21.1 Cr | NO |
| Aug | Rs 17.0 Cr | Rs 25.0 Cr | NO |
| Sep | Rs 16.4 Cr | Rs 21.6 Cr | NO |
| Oct | Rs 20.9 Cr | Rs 24.7 Cr | NO |
| Nov | Rs 26.9 Cr | Rs 27.5 Cr | NO |
| Dec | Rs 29.3 Cr | Rs 25.6 Cr | YES |

**Momentum beats Fixed 20%: 12/12 months — genuine finding.**
**Momentum beats Unhedged: only 2/12 months — timing dependent.**

### Why December and January Worked

The GFC crash ran from Jan 2008 to Mar 2009. The December annual window
(Dec 2007 → Dec 2008) captured the full -57% move in one window — a perfect
alignment. June roll split the crash across two windows and caught neither payout.

This means the December outperformance was **partially timing luck**, not a
robust edge. If you had started this strategy in any month from February to
November, you would have underperformed unhedged over 25 years.

---

## Genuine Findings (Robust Across All Tests)

| Finding | Evidence |
|---|---|
| Momentum rule beats Fixed 20% OTM | 12/12 roll months |
| Deep OTM beats ATM puts | Consistent across all tests |
| Annual beats quarterly rolling | Quarterly splits crash cycles |
| Simple beats complex | Ladder, collar, cash buffer all lost |
| Timing gates (sigma, drawdown) backfire | Both failed at worst moment |

---

## The Structural Truth

Nifty long-run CAGR = 14.5% per year
Put premium cost = 1.0-1.5% per year
Expected payout per year = 0.3-0.5% per year (crash once per 10-12 yrs)
Net expected drag = ~0.6-1.0% per year, forever


Options are priced so that sellers make money systematically over time.
If put buying consistently beat unhedged, the market would reprice until it didn't.

Unhedged Rs 1 Cr over 25 years at 14.5% CAGR = Rs 25.9 Cr
Hedged Rs 1 Cr over 25 years at 13.5% CAGR = Rs 21.6 Cr
Drag cost in wealth terms = Rs 4.3 Cr lost
Actual hedge payout over 25 years = Rs 1.8 Cr received
Net loss from hedging = Rs 2.5 Cr


---

## When Hedging DOES Make Sense

| Situation | Why Hedge Makes Sense |
|---|---|
| Short-term known risk | Election, earnings, macro event in next 3-6 months |
| Near liquidity event | Retiring in 1-2 years, cannot afford 50% drawdown |
| Institutional mandate | Drawdown limits required by fund mandate |
| Early retirement | Sequence of returns risk — year-1 crash is catastrophic |

**For a long-term wealth compounder — hedging systematically destroys value.**

---

## What Actually Works for Long-Term Nifty Investors

1. **Asset allocation** — don't put 100% in equity if you cannot stomach -50%
2. **Stay invested** — not panic selling in 2008/2020 is worth more than any put
3. **SIP averaging** — regular buying through crashes naturally lowers cost basis
4. **Time horizon discipline** — only invest money you will not need for 7+ years
5. **Position sizing** — size equity exposure to your actual risk tolerance

---

## All Strategies Compared (December Roll, Apple to Apple)

| Rank | Strategy | Final Value | vs Unhedged | Avg Cost/yr |
|---|---|---|---|---|
| 1 | Momentum Bull=20% Bear=30% | Rs 34.3 Cr | +Rs 8.4 Cr | 1.03% |
| 2 | Momentum Bull=20% Bear=25% | Rs 32.7 Cr | +Rs 6.8 Cr | 1.23% |
| 3 | Fixed 30% OTM Annual | Rs 32.1 Cr | +Rs 6.3 Cr | 0.56% |
| 4 | Fixed 25% OTM Annual | Rs 31.8 Cr | +Rs 5.9 Cr | 0.91% |
| 5 | Fixed 20% OTM Annual | Rs 30.9 Cr | +Rs 5.1 Cr | 1.50% |
| — | Unhedged | Rs 25.9 Cr | baseline | 0% |
| x | ATM Put Annual | Rs 13.4 Cr | -Rs 12.5 Cr | 5.75% |
| x | Bear=CASH | Rs 11.5 Cr | -Rs 14.4 Cr | — |
| x | Collar | Rs 5.4 Cr | -Rs 20.5 Cr | — |

Note: December roll results above are partially explained by GFC timing alignment.
Across all 12 roll months, no strategy beats unhedged consistently.

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

## Final Conclusion

After testing 8 distinct hedging strategies with 25 years of real Nifty data,
across 12 different roll month variants, the conclusion is clear:

**Systematic put buying does not robustly outperform an unhedged Nifty portfolio
for a long-term investor.**

The one robust finding is that the **momentum rule (200MA regime switch) consistently
improves any hedging strategy relative to a fixed OTM approach.** If you must hedge,
use the momentum rule. But the better answer for a long-term compounder is to size
your equity exposure correctly and stay invested through cycles.

The best hedge is a long time horizon.

---

*Backtest: September 2026 | Data: NSE Nifty 50 (1999-2026), India VIX (2008-2026)*
*Model: Black-Scholes, India VIX sigma, 1.17x vol scalar, 1.4x skew, 33% tax*
*Stability test: 12 roll month variants x 2 strategies = 24 backtests*
