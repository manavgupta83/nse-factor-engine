# Stage 6 — Portfolio Selection

## Overview

Stage 6 is the final stage of the NSE Factor Engine pipeline. It takes the ranked
momentum signals from Stage 5 and applies the MR_M reconstitution algorithm to
determine the 25-stock portfolio — deciding what to BUY, HOLD, SELL, and WATCHLIST.

---

## Pipeline Modes

Stage 6 runs in one of two modes, controlled by the `STAGE6_MODE` environment variable:

```bash
STAGE6_MODE=rebalance python3 signals/stage6/stage6_assemble.py
STAGE6_MODE=monitor   python3 signals/stage6/stage6_assemble.py
```

### What runs in each mode

| Stage | Rebalance | Monitor |
|---|---|---|
| Stage 1 — Universe & Liquidity | ✅ Runs | ✅ Runs |
| Stage 2 — Momentum Core Signals | ✅ Runs | ✅ Runs |
| Stage 3 — Momentum Quality Signals | ✅ Runs | ✅ Runs |
| Stage 4 — Entry Quality Filters | ✅ Runs | ✅ Runs |
| Stage 5 — Ranking & Selection | ✅ Runs | ✅ Runs |
| Stage 6 — Portfolio Selection | ✅ Runs + writes files | ✅ Runs + prints only |
| Index Fetch | ✅ Runs | ✅ Runs |
| Market Movement — Breadth Metrics | ✅ Runs | ✅ Runs |
| Market Movement — Compute Metrics | ✅ Runs | ✅ Runs |
| Market Movement — Generate PDF | ✅ Runs | ✅ Runs |

### What gets written in each mode

| Output | Rebalance | Monitor |
|---|---|---|
| `portfolio_recommendations_{DDMMYYYY}.parquet` | ✅ Written | ❌ Not written |
| Portfolio state (`portfolio_state.parquet`) | ✅ Updated | ❌ Not updated |
| Portfolio history snapshot | ✅ Written | ❌ Not written |
| Reject tracker CSV | ✅ Written | ❌ Not written |
| Beta parquet (`stage6/beta/beta_{DDMMYYYY}.parquet`) | ✅ Written | ❌ Not written |
| 30-day rebalance guard | ✅ Enforced | ❌ Bypassed |

### What Telegram receives in each mode

| Telegram Output | Rebalance | Monitor |
|---|---|---|
| Portfolio PDF | ✅ Sent | ❌ Not sent |
| Monitor card (per-stock RSI, beta, alpha) | ❌ Not sent | ✅ Sent |
| Market movement PDF | ✅ Sent | ✅ Sent |

---

## Rebalance Mode

Runs the full pipeline end-to-end. Stage 6 checks the 30-day rebalance guard — if
fewer than 30 days have elapsed since the last rebalance, it exits without writing
anything. If the guard passes, it runs the full MR_M reconstitution and writes all
output files.

**Trigger via Telegram:** `/run_pipeline` → `🔄 Rebalance`

**Trigger via CLI:**
```bash
cd /home/ec2-user/nse-factor-engine
STAGE6_MODE=rebalance python3 run_pipeline.py
```

---

## Monitor Mode

Runs the full pipeline but Stage 6 only prints the current portfolio state — what
would happen if rebalancing occurred today, without writing anything or changing
portfolio state. Useful for mid-month portfolio monitoring.

**Trigger via Telegram:** `/run_pipeline` → `👁 Monitor`

**Trigger via CLI:**
```bash
cd /home/ec2-user/nse-factor-engine
STAGE6_MODE=monitor python3 run_pipeline.py
```

### Monitor card format (Telegram)

Each stock is shown as a 2-line card:

🔵 1. LAURUSLABS (4.88)
RSI(rebal→now) 60→74 (+14) | β:0.95 | α:+101% | R:+102%


**Dot colour** reflects RSI condition:

| Colour | Condition | Exit Signal |
|---|---|---|
| 🔴 Red | rebal RSI < 50, today RSI < 50, flat/declining | ⚠️ Yes |
| 🟠 Amber | rebal RSI < 50, today RSI < 50, rising | No |
| 🔵 Blue | rebal RSI < 50, today RSI ≥ 50 | No |
| 🔴 Red | rebal RSI ≥ 50, today RSI < 50 | ⚠️ Yes |
| 🔵 Blue | rebal RSI ≥ 50, today RSI ≥ 50 | No |

**Exit signal conditions (⚠️):**
- Rebal RSI & Today RSI both < 50 with RSI flat/declining
- Rebal RSI > 50 and Today RSI < 50

---

## Output Files

### `portfolio_recommendations_{DDMMYYYY}.parquet`

Written on every rebalance. One row per stock in the scored universe. Key columns:

| Column | Description |
|---|---|
| `symbol` | Stock symbol |
| `as_of_date` | Signal date (last Friday) |
| `tier` | `TOP_25`, `WATCHLIST`, or `SELL` |
| `action` | `BUY`, `HOLD`, `SELL`, `WATCHLIST` |
| `mr_rank` | Momentum rank (1 = highest score) |
| `norm_momentum_score` | Momentum score |
| `rsi_14` | RSI-14 at signal date |
| `beta_12m` | 12-month beta vs Nifty500 |
| `alpha_12m` | Jensen alpha over 12 months |
| `stock_12m_ret` | 12-month compounded return |
| `market_12m_ret` | Nifty500 12-month return |
| `portfolio_beta` | Equal-weighted portfolio beta |

### `beta/beta_{DDMMYYYY}.parquet`

Written on every rebalance. One row per stock plus one PORTFOLIO summary row.
See `metrics/README.md` for full column reference.

---

## Rebalance Guard

Stage 6 enforces a minimum 30-day gap between rebalances. If fewer than 30 days
have elapsed since `last_rebalance_date` in `portfolio_state.parquet`, the script
exits immediately with no files written and no state changes.

30-day guard: only 9 days since last rebalance (2026-08-21). Need 21 more days.
Skipping — no files written, no state changed.
Tip: run with STAGE6_MODE=monitor to see current rankings without rebalancing.


---

## Index Data & Market Movement

Both rebalance and monitor modes run the index fetch and market movement stages.
However, `fetch_index_data.py` has a same-day guard (`data/last_run_date.txt`) —
it will skip if already run today. On weekends and market holidays, yfinance returns
no new data and no new parquet is created, but `last_run_date.txt` is still updated.

If the index data appears stale, delete the guard file and re-run:

```bash
rm data/last_run_date.txt
STAGE6_MODE=monitor python3 run_pipeline.py
```

---

## Files in this directory

| File | Purpose |
|---|---|
| `stage6_assemble.py` | Main Stage 6 orchestrator |
| `metrics/mr_score.py` | Momentum scoring |
| `metrics/mr_reconstitute.py` | MR_M reconstitution algorithm |
| `metrics/mr_beta.py` | 12-month beta and alpha calculator |
| `metrics/g6_gate.py` | Circuit breaker pre-filter |
| `metrics/README.md` | Detailed methodology for scoring, reconstitution and beta |

---

## Factor Weight Optimisation — Backtest Results

Tested on MR_M (monthly rebalance, Buffer Zone 38, no Weinstein gate) with the
Day15 RSI<50 exit overlay applied. Backtest period: January 2016 → June 2026
(126 monthly periods, 10.5 years). All net figures are after transaction costs
(0.04% per side, position-sized at 1/25th portfolio).

### Variant: 50/50 (12m : 6m momentum) — prior configuration

| Metric | Baseline (gross) | + Day15 RSI<50 (net) | Delta |
|---|---|---|---|
| CAGR | 32.63% | 35.21% | +2.58% |
| Sharpe | 1.156 | 1.384 | +0.228 |
| Max DD | -37.81% | -23.79% | +14.02% |
| Worst month | -33.85% | -19.34% | +14.51% |
| Mean tail (worst 10%) | -13.30% | -9.97% | +3.33% |

### Variant: 60/40 (12m : 6m momentum) — adopted configuration

| Metric | Baseline (gross) | + Day15 RSI<50 (net) | Delta |
|---|---|---|---|
| CAGR | 34.12% | 37.83% | +3.71% |
| Sharpe | 1.197 | 1.484 | +0.287 |
| Max DD | -35.08% | -19.82% | +15.26% |
| Worst month | -30.65% | -18.14% | +12.51% |
| Mean tail (worst 10%) | -12.71% | -9.55% | +3.16% |

### Key finding

The 60/40 weighting selects stocks with stronger persistent momentum (12m signal
dominant). These portfolios respond more decisively to the Day15 RSI exit signal —
the overlay adds +3.71% CAGR and cuts Max DD by 15.26 percentage points, vs +2.58%
and 14.02pp for the 50/50 configuration. The two improvements are synergistic: the
12m-heavy portfolio composition makes the RSI overlay more effective, not just
additive. **60/40 (12m:6m) is adopted as the production weight configuration.**

---

## Regime-Conditional Breakdown — Factor Weight Variants

All metrics below are net of costs + Day15 RSI<50 overlay. Regime labels sourced
from the HMM forward algorithm (causal, no lookahead) — regime assigned at signal
date (end-of-month Friday when portfolio is constructed). Backtest window: Jan 2016
→ Jun 2026 (126 months). `*` = best value in row across all variants.

### Bull Regime (88 months)

| Metric | V0 (50/50) | V1 (20/80) | V2 (40/60) | V3 (60/40) | V4 (80/20) |
|---|---|---|---|---|---|
| Mean ret/mo | 3.00% | 2.86% | 2.97% | 3.07% | 3.12%* |
| Sharpe | 1.802 | 1.780 | 1.760 | 1.830* | 1.811 |
| Hit rate | 71.6% | 71.6% | 69.3% | 70.5% | 72.7%* |
| Worst month | -13.42% | -13.85% | -13.38% | -12.98% | -12.67%* |
| Mean tail | -6.11% | -6.05% | -5.93% | -5.94% | -5.92%* |

### Choppy Regime (33 months)

| Metric | V0 (50/50) | V1 (20/80) | V2 (40/60) | V3 (60/40) | V4 (80/20) |
|---|---|---|---|---|---|
| Mean ret/mo | 2.55% | 2.41% | 2.49% | 2.76%* | 2.61% |
| Sharpe | 1.051 | 1.017 | 1.036 | 1.160* | 1.055 |
| Hit rate | 66.7% | 69.7%* | 66.7% | 66.7% | 66.7% |
| Worst month | -13.19% | -12.97% | -13.14% | -11.68%* | -13.70% |
| Mean tail | -11.50% | -11.24% | -11.40% | -10.55%* | -11.70% |

### Crisis Regime (4 months — low sample, treat as indicative only)

| Metric | V0 (50/50) | V1 (20/80) | V2 (40/60) | V3 (60/40) | V4 (80/20) |
|---|---|---|---|---|---|
| Mean ret/mo | 1.15% | 2.32%* | 1.26% | 1.54% | 1.27% |
| Sharpe | 0.254 | 0.508* | 0.281 | 0.344 | 0.310 |
| Hit rate | 50.0% | 75.0%* | 50.0% | 50.0% | 50.0% |
| Worst month | -18.98% | -17.93% | -18.66% | -18.14% | -17.33%* |
| Mean tail | -18.98% | -17.93% | -18.66% | -18.14% | -17.33%* |

### V3 vs V0 delta by regime

| Regime | V0 mean | V3 mean | Δ mean | V0 Sharpe | V3 Sharpe | ΔSharpe |
|---|---|---|---|---|---|---|
| Bull | 3.00% | 3.07% | +0.07% | 1.802 | 1.830 | +0.028 |
| Choppy | 2.55% | 2.76% | +0.21% | 1.051 | 1.160 | +0.109 |
| Crisis | 1.15% | 1.54% | +0.38% | 0.254 | 0.344 | +0.090 |

V3 (60/40) wins on mean return and Sharpe in both Bull and Choppy regimes. The
advantage is larger in Choppy (+0.21% mean, +0.109 Sharpe) than in Bull (+0.07%,
+0.028) — consistent with the 12m signal being more robust when market direction
is unclear. Crisis sample (4 months) is too thin to be conclusive.

---

## Monthly Net Returns — All Variants + Mid-Month RSI Overlay

All figures net of costs + Day15 RSI<50 mid-month exit overlay applied.
Backtest period: January 2016 → June 2026 (126 monthly periods, 10.5 years).
RSI overlay is applied mid-month only — not at rebalance.

### Summary metrics

| Metric | V0 (50/50) | V1 (20/80) | V2 (40/60) | V3 (60/40) | V4 (80/20) |
|---|---|---|---|---|---|
| CAGR | 35.84% | 34.55% | 35.32% | **37.83%** | 37.48% |
| Sharpe | 1.416 | 1.404 | 1.396 | **1.484** | 1.449 |
| Max DD | -23.46% | -21.21% | -23.70% | **-19.82%** | -22.22% |
| Worst month | -18.98% | -17.93% | -18.66% | -18.14% | **-17.33%** |
| Mean tail (worst 10%) | -9.99% | -9.73% | -9.84% | **-9.55%** | -9.86% |
| Final NAV (base=100) | 2431x | 2200x | 2336x | **2827x** | 2755x |

V3 (60/40) is the dominant configuration — best CAGR, Sharpe, MaxDD, and tail simultaneously.

### Month-by-month returns

| Month | V0 | V1 | V2 | V3 | V4 |
|---|---|---|---|---|---|
| 2016-02 | -8.69% | -7.82% | -7.96% | -8.19% | -8.21% |
| 2016-03 | 11.55% | 8.75% | 10.00% | 11.62% | 10.95% |
| 2016-04 | 4.76% | 6.38% | 4.64% | 4.56% | 5.08% |
| 2016-05 | -3.90% | -2.50% | -4.13% | -4.41% | -4.53% |
| 2016-06 | -1.64% | 1.58% | -1.36% | -2.15% | -0.67% |
| 2016-07 | 6.80% | 6.26% | 6.17% | 4.89% | 4.48% |
| 2016-08 | 6.36% | 7.02% | 7.34% | 7.58% | 8.27% |
| 2016-09 | 4.37% | 4.44% | 4.74% | 4.29% | 3.47% |
| 2016-10 | 4.63% | 3.27% | 4.76% | 4.78% | 3.92% |
| 2016-11 | -13.19% | -12.85% | -12.51% | -10.79% | -12.51% |
| 2016-12 | -2.01% | -1.57% | -2.01% | -3.06% | -3.18% |
| 2017-01 | 17.47% | 15.34% | 17.67% | 18.22% | 19.30% |
| 2017-02 | 0.61% | -0.58% | -0.75% | 1.68% | 2.97% |
| 2017-03 | 1.90% | 1.51% | 2.02% | 2.22% | 2.14% |
| 2017-04 | 3.33% | 4.68% | 4.33% | 3.49% | 4.59% |
| 2017-05 | 2.13% | 2.60% | 2.40% | 2.41% | 1.89% |
| 2017-06 | 4.85% | 4.02% | 4.48% | 3.65% | 3.56% |
| 2017-07 | 8.48% | 8.38% | 6.13% | 7.09% | 5.39% |
| 2017-08 | 0.84% | 1.48% | 0.93% | 0.83% | 0.21% |
| 2017-09 | 10.81% | 11.00% | 10.81% | 11.92% | 11.87% |
| 2017-10 | 15.56% | 15.64% | 13.59% | 14.94% | 14.89% |
| 2017-11 | 0.19% | 0.90% | 0.92% | 0.81% | 0.92% |
| 2017-12 | 4.04% | 4.70% | 3.93% | 4.02% | 5.46% |
| 2018-01 | 7.41% | 7.69% | 6.61% | 6.18% | 3.37% |
| 2018-02 | -4.03% | -5.12% | -3.82% | -3.99% | -4.60% |
| 2018-03 | -5.77% | -6.43% | -5.79% | -5.77% | -5.37% |
| 2018-04 | 11.62% | 12.65% | 11.82% | 10.75% | 12.71% |
| 2018-05 | -5.80% | -5.18% | -5.43% | -5.78% | -5.99% |
| 2018-06 | -2.58% | -1.04% | -2.77% | -3.24% | -4.34% |
| 2018-07 | 2.61% | 2.78% | 2.56% | 2.04% | 2.22% |
| 2018-08 | 6.98% | 7.10% | 6.30% | 7.45% | 7.01% |
| 2018-09 | -5.85% | -6.23% | -5.98% | -5.94% | -5.20% |
| 2018-10 | -8.51% | -8.69% | -8.64% | -8.38% | -8.36% |
| 2018-11 | 3.63% | 3.16% | 4.26% | 4.06% | 3.07% |
| 2018-12 | -2.29% | -3.52% | -3.51% | -1.64% | -2.69% |
| 2019-01 | 0.06% | 0.89% | -0.12% | 0.81% | -0.47% |
| 2019-02 | -5.23% | -3.04% | -5.11% | -4.72% | -3.77% |
| 2019-03 | 4.88% | 4.83% | 5.48% | 4.59% | 4.63% |
| 2019-04 | 2.53% | 1.80% | 1.47% | 2.69% | 2.85% |
| 2019-05 | 1.69% | 3.18% | 0.98% | -0.01% | -0.85% |
| 2019-06 | -1.63% | -1.17% | -1.76% | -1.37% | -1.26% |
| 2019-07 | -2.76% | -3.65% | -3.72% | -1.14% | -1.58% |
| 2019-08 | -2.12% | -1.57% | -2.29% | -2.35% | -3.85% |
| 2019-09 | 5.24% | 7.47% | 5.71% | 5.14% | 5.60% |
| 2019-10 | 1.90% | 3.80% | 2.66% | 2.26% | 1.96% |
| 2019-11 | -1.53% | -0.45% | -0.83% | -1.98% | -1.68% |
| 2019-12 | 2.22% | 1.73% | 2.19% | 2.43% | 3.03% |
| 2020-01 | 4.00% | 4.55% | 4.28% | 4.57% | 4.97% |
| 2020-02 | -0.63% | 0.43% | -0.04% | 0.20% | 0.50% |
| 2020-03 | -18.98% | -17.93% | -18.66% | -18.14% | -17.33% |
| 2020-04 | 18.22% | 20.00% | 18.46% | 18.59% | 16.29% |
| 2020-05 | -1.67% | 0.16% | -1.11% | -1.33% | -0.35% |
| 2020-06 | 7.03% | 7.06% | 6.35% | 7.02% | 6.45% |
| 2020-07 | 9.82% | 9.73% | 9.61% | 10.05% | 9.57% |
| 2020-08 | 14.32% | 13.77% | 14.82% | 14.29% | 14.12% |
| 2020-09 | 3.20% | 3.15% | 2.98% | 3.63% | 4.16% |
| 2020-10 | 3.49% | 2.97% | 2.68% | 1.79% | 0.73% |
| 2020-11 | 8.68% | 8.96% | 9.20% | 10.14% | 9.47% |
| 2020-12 | 8.43% | 9.15% | 9.32% | 7.92% | 7.96% |
| 2021-01 | 9.48% | 9.46% | 9.30% | 8.92% | 4.15% |
| 2021-02 | 10.40% | 12.14% | 10.08% | 9.66% | 10.87% |
| 2021-03 | 8.72% | 6.30% | 7.48% | 10.05% | 7.37% |
| 2021-04 | 5.26% | 4.09% | 5.59% | 7.41% | 9.79% |
| 2021-05 | 11.10% | 10.52% | 10.25% | 10.27% | 13.80% |
| 2021-06 | -1.48% | -1.38% | -1.69% | -2.04% | 0.17% |
| 2021-07 | 4.71% | 4.12% | 4.28% | 5.22% | 5.02% |
| 2021-08 | 2.04% | 1.30% | 2.00% | 2.96% | 3.44% |
| 2021-09 | 7.29% | 3.71% | 5.01% | 8.47% | 7.75% |
| 2021-10 | 2.59% | 1.57% | 2.12% | 2.32% | 4.03% |
| 2021-11 | 7.08% | 5.68% | 3.98% | 6.68% | 10.61% |
| 2021-12 | 6.41% | 6.69% | 6.71% | 5.67% | 5.88% |
| 2022-01 | 8.78% | 6.35% | 7.57% | 8.84% | 11.15% |
| 2022-02 | -9.08% | -9.68% | -9.73% | -9.45% | -9.14% |
| 2022-03 | 11.62% | 10.60% | 12.02% | 12.82% | 15.43% |
| 2022-04 | 7.28% | 8.15% | 6.84% | 4.79% | 5.29% |
| 2022-05 | -13.08% | -12.97% | -13.14% | -11.68% | -13.70% |
| 2022-06 | -9.59% | -9.47% | -9.95% | -8.91% | -9.87% |
| 2022-07 | 9.62% | 6.61% | 9.42% | 8.85% | 8.46% |
| 2022-08 | 4.60% | 4.79% | 4.87% | 4.72% | 2.03% |
| 2022-09 | 7.18% | 7.33% | 7.82% | 7.70% | 8.50% |
| 2022-10 | -0.83% | -0.60% | -1.08% | -0.21% | 0.76% |
| 2022-11 | 2.91% | 2.59% | 1.67% | 2.86% | 0.93% |
| 2022-12 | -5.65% | -4.75% | -4.83% | -4.91% | -5.80% |
| 2023-01 | 3.13% | 3.79% | 4.12% | 3.05% | 3.58% |
| 2023-02 | -3.37% | -3.11% | -1.75% | -4.24% | -3.06% |
| 2023-03 | 1.70% | -0.04% | 1.14% | 0.80% | 0.71% |
| 2023-04 | 10.33% | 8.71% | 9.63% | 8.15% | 6.88% |
| 2023-05 | 11.62% | 9.13% | 11.58% | 12.31% | 13.51% |
| 2023-06 | 9.31% | 10.38% | 10.33% | 10.21% | 9.24% |
| 2023-07 | 20.14% | 18.45% | 21.47% | 19.26% | 19.38% |
| 2023-08 | 4.50% | 7.61% | 5.10% | 7.47% | 6.44% |
| 2023-09 | 1.67% | 2.28% | 2.24% | 2.09% | 3.49% |
| 2023-10 | 7.06% | 5.61% | 6.28% | 6.27% | 6.98% |
| 2023-11 | 13.17% | 12.38% | 12.50% | 11.68% | 12.55% |
| 2023-12 | 5.96% | 6.16% | 5.69% | 5.00% | 4.38% |
| 2024-01 | 14.71% | 17.35% | 18.86% | 16.26% | 15.84% |
| 2024-02 | 3.78% | 1.26% | 1.42% | 3.57% | 3.31% |
| 2024-03 | -13.42% | -13.85% | -13.38% | -12.98% | -12.67% |
| 2024-04 | 13.50% | 11.76% | 13.62% | 16.57% | 16.29% |
| 2024-05 | 0.89% | 1.13% | 1.26% | 2.50% | 0.55% |
| 2024-06 | 4.35% | 1.04% | 4.35% | 4.12% | 5.19% |
| 2024-07 | 3.58% | 2.82% | 4.36% | 4.08% | 7.39% |
| 2024-08 | -0.94% | -1.72% | -0.33% | -1.42% | 0.03% |
| 2024-09 | 0.20% | -3.64% | -1.09% | -0.59% | -1.14% |
| 2024-10 | -4.42% | -5.49% | -4.31% | -3.91% | -3.32% |
| 2024-11 | 2.03% | 1.88% | 2.40% | 2.99% | 4.08% |
| 2024-12 | 5.06% | 4.31% | 4.69% | 3.48% | 4.49% |
| 2025-01 | -6.21% | -6.01% | -6.07% | -6.17% | -5.69% |
| 2025-02 | -7.30% | -5.31% | -6.07% | -7.45% | -8.24% |
| 2025-03 | 3.62% | 3.92% | 3.40% | 4.24% | 2.53% |
| 2025-04 | -1.66% | -4.33% | -3.32% | -1.28% | -0.03% |
| 2025-05 | 4.48% | 3.41% | 4.80% | 5.59% | 5.17% |
| 2025-06 | 0.38% | 1.24% | 0.84% | 1.12% | -0.11% |
| 2025-07 | -1.07% | -0.19% | -0.61% | -1.78% | 1.53% |
| 2025-08 | -1.66% | 0.57% | -1.57% | -1.84% | -2.80% |
| 2025-09 | -0.03% | 0.54% | 0.24% | -0.88% | -1.87% |
| 2025-10 | 1.36% | 0.39% | 0.93% | 0.84% | 1.68% |
| 2025-11 | 1.82% | 0.20% | 0.03% | 2.88% | 2.66% |
| 2025-12 | 2.34% | 2.15% | 2.09% | 2.50% | 1.47% |
| 2026-01 | -4.82% | -3.75% | -4.38% | -4.69% | -4.76% |
| 2026-02 | 9.49% | 6.51% | 10.92% | 10.20% | 9.96% |
| 2026-03 | -10.12% | -9.08% | -9.99% | -10.28% | -10.73% |
| 2026-04 | 14.40% | 15.88% | 14.74% | 15.22% | 14.85% |
| 2026-05 | 2.34% | 1.47% | 4.59% | 5.02% | 1.20% |
| 2026-06 | 5.26% | 6.14% | 6.69% | 5.27% | 4.83% |
