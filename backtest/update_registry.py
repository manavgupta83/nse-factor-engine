"""
Backtest Registry Updater

Reads all summary CSVs from backtest/results/ and maintains a single
registry.csv with one row per variant per run. Deduplicates on
(run_date, Variant) so reruns don't create duplicate entries.

Usage:
  python3 backtest/update_registry.py

Registry columns:
  run_date, script, Variant, Description, CAGR, BM CAGR, Alpha,
  Sharpe, Calmar, Max DD, Hit Rate, Avg Turnover, Avg Universe,
  NAV end (M), Periods
"""

import pandas as pd
import glob
import os
import re
from datetime import datetime

RESULTS_DIR  = '/home/ec2-user/nse-factor-engine/backtest/results'
REGISTRY     = f'{RESULTS_DIR}/registry.csv'

# Map summary filename patterns to script names
SCRIPT_MAP = {
    'hybrid_summary'   : 'simulation_fri_signal_mon_open_backtest_mr_hybrid.py',
    '12way_summary'    : 'simulation_fri_signal_mon_open_backtest_mr_12way.py',
    'variants_summary' : 'simulation_fri_signal_mon_open_backtest_mr_variants.py',
    'targeted_summary' : 'simulation_fri_signal_mon_open_backtest_mr_targeted.py',
}

REGISTRY_COLS = [
    'run_date', 'script', 'Variant', 'Description',
    'CAGR', 'BM CAGR', 'Alpha', 'Sharpe', 'Calmar', 'Max DD',
    'Hit Rate', 'Avg Turnover', 'Avg Universe', 'NAV end (M)', 'Periods'
]

# ── Load existing registry ──
if os.path.exists(REGISTRY):
    registry = pd.read_csv(REGISTRY)
    print(f'Existing registry: {len(registry)} rows')
else:
    registry = pd.DataFrame(columns=REGISTRY_COLS)
    print('No existing registry — creating fresh')

# ── Find all summary CSVs ──
summary_files = glob.glob(f'{RESULTS_DIR}/*_summary_*.csv')
summary_files = [f for f in summary_files if 'registry' not in f
                 and 'tax_gains' not in f]
summary_files.sort()
print(f'Found {len(summary_files)} summary files')

new_rows = []

for fpath in summary_files:
    fname = os.path.basename(fpath)

    # Extract run_date from filename (last 8 digits before .csv)
    date_match = re.search(r'(\d{8})\.csv$', fname)
    if not date_match:
        print(f'  Skipping — cannot parse date: {fname}')
        continue
    date_str  = date_match.group(1)
    run_date  = f'{date_str[0:2]}/{date_str[2:4]}/{date_str[4:8]}'

    # Identify script
    script = 'unknown'
    for key, val in SCRIPT_MAP.items():
        if fname.startswith(key):
            script = val
            break

    df = pd.read_csv(fpath)

    for _, row in df.iterrows():
        new_rows.append({
            'run_date'     : run_date,
            'script'       : script,
            'Variant'      : row.get('Variant', ''),
            'Description'  : row.get('Description', ''),
            'CAGR'         : row.get('CAGR', None),
            'BM CAGR'      : row.get('BM CAGR', None),
            'Alpha'        : row.get('Alpha', None),
            'Sharpe'       : row.get('Sharpe', None),
            'Calmar'       : row.get('Calmar', None),
            'Max DD'       : row.get('Max DD', None),
            'Hit Rate'     : row.get('Hit Rate', None),
            'Avg Turnover' : row.get('Avg Turnover', None),
            'Avg Universe' : row.get('Avg Universe', row.get('Avg Universe', None)),
            'NAV end (M)'  : row.get('NAV end (M)', None),
            'Periods'      : row.get('Periods', row.get('Weeks', None)),
        })

    print(f'  Processed: {fname} — {len(df)} variants')

# ── Merge and deduplicate ──
new_df   = pd.DataFrame(new_rows, columns=REGISTRY_COLS)
combined = pd.concat([registry, new_df], ignore_index=True)
combined = combined.drop_duplicates(subset=['run_date', 'Variant'], keep='last')
combined = combined.sort_values("NAV end (M)", ascending=False).reset_index(drop=True)

# ── Write registry ──
combined.to_csv(REGISTRY, index=False)
print(f'\nRegistry updated: {len(combined)} rows -> {REGISTRY}')

# ── Print summary ──
print('\n' + '=' * 100)
print('REGISTRY CONTENTS')
print('=' * 100)
print('%-12s %-13s %-34s %7s %7s %7s %7s %7s %6s %9s' % (
    'run_date', 'Variant', 'Description',
    'CAGR', 'Alpha', 'Sharpe', 'Calmar', 'MaxDD', 'Hit%', 'NAV(M)'))
print('-' * 100)
for _, r in combined.iterrows():
    print('%-12s %-13s %-34s %6.2f%% %6.2f%% %7.3f %7.3f %6.2f%% %5.1f%% Rs%6.3fM' % (
        r['run_date'], r['Variant'], str(r['Description'])[:34],
        float(r['CAGR']) * 100, float(r['Alpha']) * 100,
        float(r['Sharpe']), float(r['Calmar']),
        float(r['Max DD']) * 100, float(r['Hit Rate']) * 100,
        float(r['NAV end (M)'])
    ))
print('=' * 100)
