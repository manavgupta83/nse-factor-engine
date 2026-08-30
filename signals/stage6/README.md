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
