# MR_M Backtest — Research Log & File Map

**Strategy:** MR_M (Momentum-Ranked, Mid-cap focus)
**Universe:** NSE stocks (Nifty 500 eligible)
**Period:** Jan 2016 – Sep 2026 (~10.4 years, 126 monthly periods)
**Capital base:** ₹1 crore (10M)
**Run environment:** EC2 `/home/ec2-user/nse-factor-engine/`

---

## Strategy Core (V3)

- **Signal:** Every Friday close → compute momentum scores
- **Execution:** Following Monday open
- **Portfolio:** 25 stocks, equal-weight
- **Scoring:** 60% × 12M momentum / vol  +  40% × 6M momentum / vol → normalised z-score
- **Buffer zone:** rank ≤ 38 → stock stays in portfolio (avoids excessive churn)
- **Forced-in:** top-12 ranked stocks always enter, displacing weakest holders if needed
- **Fill:** remaining slots filled to 25 from ranked pool
- **Transaction cost:** 0.0004 one-way (where modelled)

---

## Evolution of Scripts

### Generation 1 — Early explorations (g6/c6)

simulation_fri_signal_mon_open_backtest_g6_c6.py # basic gross sim
simulation_fri_signal_mon_open_backtest_g6_c6_with_costs.py
simulation_fri_signal_mon_open_backtest_g6_c6.txt # results snapshot

Early signal/execution cadence experiments. Not the canonical strategy.

### Generation 2 — MR variants

simulation_fri_signal_mon_open_backtest_mr.py # base MR sim
simulation_fri_signal_mon_open_backtest_mr_variants.py # parameter sweep
simulation_fri_signal_mon_open_backtest_mr_12way.py # 12-way split test
simulation_fri_signal_mon_open_backtest_mr_hybrid.py # hybrid scoring
simulation_fri_signal_mon_open_backtest_mr_targeted.py # targeted entry

MR scoring introduced. These are analytical return-chain sims, not PortfolioState-based.

### Generation 3 — V3 canonical (current)

v3_backtest.py # ✅ BASE: monthly rebalance, PortfolioState, gross
v3_rsi50_overlay.py # RSI exit → cash (analytical overlay on activity CSV)
v3_rsi_replacement_overlay.py # RSI exit → watchlist replacement (analytical overlay)
v3_rsi_replacement_sim.py # ✅ LATEST: full live sim, RSI replacement, net of cost

V3 uses `PortfolioState` throughout. `v3_rsi_replacement_sim.py` is the single most
complete and accurate script — runs the full strategy end-to-end in one pass.

### Other V3 variants

v3_rsi_backtest.py # RSI filter at entry (not mid-month exit)
v3_15d_backtest.py # 15-day holding period experiment
v3_15d_rsi_backtest.py # 15-day hold + RSI filter


---

## Benchmark Results Table

All results over Jan 2016 – Sep 2026 (~10.4 years).

| Script | Type | CAGR | Sharpe | Max DD | Notes |
|---|---|---|---|---|---|
| `v3_backtest.py` | Gross | 34.12% | 1.197 | -35.08% | Base monthly, no RSI |
| `v3_rsi50_overlay.py` | Net | 37.83% | 1.484 | -19.82% | RSI exit → cash |
| `v3_rsi_replacement_overlay.py` | Net | 38.02% | 1.454 | -19.55% | RSI exit → replace (analytical) |
| `v3_rsi_replacement_sim.py` | Gross | 38.94% | 1.432 | -27.41% | Full live sim (CHECK_DAY=10) |
| **`v3_rsi_replacement_sim.py`** | **Net** | **38.53%** | **1.420** | **-27.80%** | **Full live sim, net of cost** |

**Key observations:**
- RSI mid-month filter adds ~+3.9% CAGR over pure monthly and cuts max DD nearly in half
- Replacement vs cash is marginal on CAGR (+0.19%) but meaningful over time
- The sim (CHECK_DAY=10, live PortfolioState) shows higher gross CAGR than the overlay
  (CHECK_DAY=15, analytical) — likely because it checks RSI earlier in the holding window
- Max DD of the sim (~-27%) is worse than the overlay (~-20%) — the overlay benefits from
  analytical smoothing; the sim reflects actual capital movement and real sequence-of-returns risk
- Cost drag is minimal: avg 0.0255%/period → only ~0.41% CAGR impact gross-to-net

---

## RSI Logic (mid-month check)

| Parameter | Value | Meaning |
|---|---|---|
| `CHECK_DAY` | 10 | ~10th trading day (~15 calendar days into hold) |
| `RSI_EXIT_THRESH` | 50 | Exit held stock if RSI < 50 at CHECK_DAY |
| `RSI_ENTRY_THRESH` | 50 | Replacement must have RSI > 50 at same date |
| RSI window | 14-period Wilder | Applied to daily closes up to CHECK_DAY |

**Replacement pool:** watchlist stocks ranked ≤ 38, not already held, sorted by mr_rank ascending.
Entry at day-16 open. If no eligible replacement → freed slot sits in cash until next SOM.

In the Sep 2026 run: 1339 RSI exits over 126 months (10.6/month), 671 replaced (50.1%), 668 went to cash.

---

## How to Run

Always run from repo root:

```bash
cd /home/ec2-user/nse-factor-engine
```

**Base monthly backtest (gross):**
```bash
python3 backtest/v3_backtest.py
```

**RSI exit → cash overlay (net, analytical):**
```bash
python3 backtest/v3_rsi50_overlay.py
```

**RSI exit → replacement overlay (net, analytical, needs activity CSV):**
```bash
python3 backtest/v3_rsi_replacement_overlay.py
```

**Full end-to-end sim with RSI replacement (net + gross, canonical):**
```bash
python3 backtest/v3_rsi_replacement_sim.py
```

---

## Output Files

All results land in `backtest/results/` with a `DDMMYYYY` run-date suffix.

| File pattern | Contents |
|---|---|
| `MR_M_V3_RSI_SIM_returns_*.csv` | Period-level NAV, gross/net returns, cost drag, benchmark |
| `MR_M_V3_RSI_SIM_activity_*.csv` | Every trade: symbol, date, price, action, SOM/MID tag, mr_rank |

---

## Support Modules

backtest/simulation/portfolio.py # PortfolioState — tracks cash, holdings, NAV, executes rebalances
backtest/strategies/ # Strategy definitions
backtest/pipeline/ # Data pipeline scripts
backtest/metrics/ # Metric computation utilities
backtest/signals/historical/ # Friday signal parquets (signals_DDMMYYYY.parquet)
backtest/data/prices_backtest.parquet
backtest/data/benchmark/nifty500_weekly.parquet


---

## Pipeline Scripts

```bash
python3 backtest/run_historical_pipeline.py       # regenerate historical signals
python3 backtest/run_monday_signal_pipeline.py    # generate current week's signal
python3 backtest/run_backtest.py                  # run configured backtest variant
python3 backtest/run_backtest_cadence.py          # cadence/timing experiments
bash   backtest/run_mr_variants.sh                # batch run MR parameter variants
python3 backtest/update_registry.py               # update strategy registry
python3 backtest/position_sizing_compare.py       # compare equal-weight vs other sizing
python3 backtest/vol_check.py                     # volatility diagnostics
```

---

*Last updated: Sep 2026 — after v3_rsi_replacement_sim.py net-of-cost run*

---

## What Didn't Work

### SOM + Mid-Month RSI Filter (`v3_rsi_replacement_som_midmonth_sim.py`)
Tested adding an RSI filter at **start of month** on top of the existing mid-month check.
Logic: after momentum-based portfolio construction, any top-25 stock with RSI < 50
(at signal Friday) gets swapped with the best watchlist stock with RSI > 50.
Swapped-out stocks go to watchlist and remain eligible for mid-month re-entry.

**Result: CAGR 19.70% net | Sharpe 0.733 | MaxDD -29.68%** — badly underperformed.

**Why it failed:**
- SOM → cash: 876 instances over 126 months (~7 slots/month with no replacement found)
- ~28% of the portfolio (7/25 slots) sat in cash at SOM on average
- Momentum stocks by definition have elevated RSI — RSI < 50 at SOM was screening out
  best performers before they had a chance to run, not just weak names
- Mid-month RSI works because it catches stocks that entered strong and then weakened
  *during* the hold. SOM RSI fires too early — before momentum has played out.

**Conclusion:** Keep SOM purely momentum-based. RSI filter only belongs mid-month.

---

## Diagnostic Findings — Why Mid-Month RSI Works

Two diagnostic scripts were run to understand *why* the mid-month RSI replacement
adds alpha, and *why* the SOM RSI filter destroyed performance.

### Script 1: `v3_rsi_som_diagnostic.py`
**Question:** Does RSI < 50 at start of month predict worse next-month returns?

**Result:** No. Zero alpha.

| RSI Bucket | Count | % of Holdings | Mean Next-Month Ret | % Positive |
|---|---|---|---|---|
| < 30 | 367 | 11.7% | **+3.92%** | 58.0% |
| 30–40 | 516 | 16.5% | +2.84% | 56.4% |
| 40–50 | 686 | 22.0% | +2.38% | 55.4% |
| 50–60 | 593 | 19.0% | +3.10% | 53.5% |
| 60–70 | 495 | 15.8% | +1.46% | 50.5% |
| 70–80 | 326 | 10.4% | +2.77% | 58.3% |
| > 80 | 142 | 4.5% | +6.06% | 57.0% |

Binary split: RSI < 50 stocks returned **+2.89%** vs RSI ≥ 50 stocks returning
**+2.78%** — a delta of only +0.11%. No meaningful difference.

**Key facts:**
- 50.2% of SOM holdings had RSI < 50 on average — ~12.6 slots per month
- In 77 out of 126 periods, more than 10 holdings had RSI < 50
- RSI < 30 stocks actually returned the second-best next-month return (+3.92%)
- The SOM RSI filter was screening out momentum stocks in normal mid-uptrend pullbacks

**Conclusion:** The SOM RSI sim's -14% CAGR delta was entirely caused by cash drag
(~7 slots/month sitting empty when no watchlist replacement was found). The filter
itself has no predictive value at SOM. RSI < 50 at SOM = dip within uptrend, not breakdown.

---

### Script 2: `v3_rsi_mid_diagnostic.py`
**Question:** Do mid-month RSI replacements actually outperform the stocks they replaced
in the residual ~10 trading days (d16 open → next SOM open)?

**Result:** Yes. Cleanly and consistently.

| Metric | Replaced (if held) | Replacement | Delta |
|---|---|---|---|
| Count | 1339 | 671 | |
| Mean return | +0.02% | +0.64% | **+0.61%** |
| Median return | -0.29% | +0.36% | **+0.65%** |
| % positive | 48.5% | 51.4% | |
| Std dev | 10.09% | 8.58% | lower risk |
| Mean tail (worst 10%) | -18.03% | -13.50% | less downside |

Per-period: replacements beat the replaced in **67/122 periods (54.9%)** with
avg delta of +0.31% and median of +0.34% per period.

**RSI < 50 prevalence comparison:**

| | SOM | Mid-Month |
|---|---|---|
| Avg slots with RSI < 50/period | 12.6 (50.2%) | 10.7 (42.8%) |
| Periods with > 10 exits | 77 | 62 |

Mid-month actually flags *fewer* stocks than SOM — yet those flags are meaningful
while the SOM ones were not.

**Why mid-month RSI works but SOM RSI doesn't:**

| | SOM RSI | Mid-Month RSI |
|---|---|---|
| What it measures | RSI at entry on momentum stocks | Deterioration of an existing hold |
| Signal type | Redundant (momentum already captured) | New information — breakdown after entry |
| Prediction horizon | ~21 trading days (too long for RSI) | ~10 trading days (matches RSI window) |
| RSI < 50 means | Normal dip within uptrend | Momentum stalled post-entry |
| Alpha | None (+0.11% delta) | Real (+0.61% delta, lower vol) |

**Conclusion:** The mid-month RSI mechanism earns its keep by simultaneously
improving residual return (+0.61%) and cutting downside risk (tail -13.50% vs -18.03%).
The max DD improvement from -35.08% (base) to -27.80% (RSI sim net) is explained by
this — systematically removing breaking-down stocks before they do their worst damage
in the back half of the holding period. Same indicator, completely different information
content depending on *when* it is applied.

---

*Diagnostics run: Sep 2026*

---

## ⭐ CANONICAL BACKTEST — PRODUCTION EQUIVALENT

backtest/v3_rsi_replacement_sim.py


This is the single script that mirrors what runs in production:
- SOM momentum reconstitution (60/40 scoring, buffer=38, forced-in top-12, fill to 25)
- Mid-month RSI check at day-10 (exit RSI < 50, replace with watchlist RSI > 50)
- Net of transaction costs (COST = 0.0004 one-way)
- Uses PortfolioState throughout — live capital, not analytical return chains

**Result: CAGR 38.53% net | Sharpe 1.420 | MaxDD -27.80% | 10.4 years**

All other scripts in this folder are either earlier generations, experiments,
overlays that depend on pre-computed CSVs, or diagnostic tools. If you want
to know what the strategy actually does — this is the one to read.

To run:
```bash
cd /home/ec2-user/nse-factor-engine
python3 backtest/v3_rsi_replacement_sim.py
```
