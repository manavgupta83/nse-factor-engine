#!/bin/bash
# Runs all 4 MR variants sequentially and prints a combined summary at the end

SCRIPT="/home/ec2-user/nse-factor-engine/backtest/simulation_fri_signal_mon_open_backtest_mr.py"
RESULTS="/home/ec2-user/nse-factor-engine/backtest/results"
DATE=$(date +%d%m%Y)

echo "================================================================="
echo "MR BACKTEST — ALL 4 VARIANTS"
echo "Run date: $DATE"
echo "================================================================="

run_variant() {
    local gate=$1
    local adtv=$2
    local prefix=$3
    echo ""
    echo "-----------------------------------------------------------------"
    echo "Running: $prefix  (USE_G6_GATE=$gate  USE_ADTV_FILTER=$adtv)"
    echo "-----------------------------------------------------------------"
    sed -i "s/^USE_G6_GATE = .*/USE_G6_GATE = $gate/" $SCRIPT
    sed -i "s/^USE_ADTV_FILTER = .*/USE_ADTV_FILTER = $adtv/" $SCRIPT
    python3 $SCRIPT 2>&1 | grep -E "CAGR|Sharpe|Drawdown|Turnover|NAV end|NAV start|weeks "
}

run_variant 0 False MR
run_variant 0 True  MR_ADTV
run_variant 2 False MR_W
run_variant 2 True  MR_W_ADTV

# Combined summary from CSV files
echo ""
echo "================================================================="
echo "COMBINED PERFORMANCE SUMMARY"
echo "================================================================="

python3 - << 'PYEOF'
import pandas as pd
import numpy as np
import glob

RESULTS = '/home/ec2-user/nse-factor-engine/backtest/results'
DATE    = pd.Timestamp.now().strftime('%d%m%Y')

variants = [
    ('MR',        'No gate, no ADTV'),
    ('MR_ADTV',   'No gate, ADTV p10 filter'),
    ('MR_W',      'Weinstein only'),
    ('MR_W_ADTV', 'Weinstein + ADTV p10'),
]

rows = []
for prefix, desc in variants:
    pattern = f'{RESULTS}/{prefix}_weekly_returns_{DATE}.csv'
    files   = glob.glob(pattern)
    if not files:
        print(f"  WARNING: {pattern} not found — skipping")
        continue

    df = pd.read_csv(files[0])
    df['signal_friday'] = pd.to_datetime(df['signal_friday'])

    rets = df['weekly_ret'].iloc[1:].dropna()
    navs = df['nav']

    cum  = (1 + rets).prod() - 1
    ny   = len(rets) / 52
    cagr = (1 + cum) ** (1/ny) - 1 if ny > 0 else np.nan

    rf_weekly  = (1 + 0.07) ** (1/52) - 1
    excess     = rets - rf_weekly
    sharpe     = (excess.mean() / excess.std()) * np.sqrt(52) if excess.std() > 0 else np.nan

    running_max = navs.cummax()
    drawdown    = (navs - running_max) / running_max
    max_dd      = drawdown.min()

    # Calmar
    calmar = cagr / abs(max_dd) if max_dd != 0 else np.nan

    # Hit rate
    hit_rate = (rets > 0).sum() / len(rets)

    # Best/worst week
    best_week  = rets.max()
    worst_week = rets.min()

    # Benchmark comparison
    bm_rets  = df['benchmark_ret'].iloc[1:].dropna()
    bm_cum   = (1 + bm_rets).prod() - 1
    bm_cagr  = (1 + bm_cum) ** (1/ny) - 1 if ny > 0 else np.nan

    # Avg turnover from activity log
    act_pattern = f'{RESULTS}/{prefix}_activity_{DATE}.csv'
    act_files   = glob.glob(act_pattern)
    if act_files:
        act = pd.read_csv(act_files[0])
        turnover_list = []
        for sig, grp in act.groupby('signal_date'):
            n_buy   = (grp['action'] == 'BUY').sum()
            n_total = grp['symbol'].nunique()
            if n_total > 0:
                turnover_list.append(n_buy / n_total)
        avg_turnover = np.mean(turnover_list) if turnover_list else np.nan
    else:
        avg_turnover = np.nan

    rows.append({
        'Variant'       : prefix,
        'Description'   : desc,
        'CAGR'          : cagr,
        'Sharpe'        : sharpe,
        'Calmar'        : calmar,
        'Max DD'        : max_dd,
        'Hit Rate'      : hit_rate,
        'Best Week'     : best_week,
        'Worst Week'    : worst_week,
        'Avg Turnover'  : avg_turnover,
        'NAV end (M)'   : navs.iloc[-1] / 1e6,
        'BM CAGR'       : bm_cagr,
        'Alpha'         : cagr - bm_cagr,
        'Weeks'         : len(rets),
    })

summary = pd.DataFrame(rows)

# Print formatted
print(f"\n{'Variant':<12} {'Description':<28} {'CAGR':>8} {'Sharpe':>8} "
      f"{'Calmar':>8} {'Max DD':>8} {'Hit%':>7} {'Turnover':>9} "
      f"{'NAV end':>10} {'Alpha':>8}")
print("-" * 115)
for _, r in summary.iterrows():
    print(f"{r['Variant']:<12} {r['Description']:<28} "
          f"{r['CAGR']:>7.2%} {r['Sharpe']:>8.3f} "
          f"{r['Calmar']:>8.3f} {r['Max DD']:>7.2%} "
          f"{r['Hit Rate']:>6.1%} {r['Avg Turnover']:>8.1%} "
          f"Rs{r['NAV end (M)']:>7.3f}M {r['Alpha']:>7.2%}")

print(f"\nBenchmark CAGR : {summary['BM CAGR'].iloc[0]:.2%}")
print(f"Period         : {summary['Weeks'].iloc[0]:.0f} weeks")
print(f"Start capital  : Rs10.000M")
PYEOF

