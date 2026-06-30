from pathlib import Path

path = Path("universe/run_universe.py")
src  = path.read_text()

# ── Scenario 1: exit immediately if last run is today ──
old1 = '''        print("      Last run date is today -- skipping price fetch")
        skip_price_fetch = True'''
new1 = '''        print("\\n      Run already completed today (last_run_date = {}). Nothing to do. Exiting.".format(last_run))
        import sys
        sys.exit(0)'''

if old1 not in src:
    raise SystemExit("Scenario 1 anchor not found -- aborting")
src = src.replace(old1, new1)

# ── Scenario 2: message before price fetch loop if pre-close ──
old2 = '''    print("\\n[2/5] Processing {} symbols...".format(len(SYMBOLS)))
    print("      Market cap first -> prices only if >= Rs {} Cr\\n".format(MKTCAP_FLOOR))'''
new2 = '''    print("\\n[2/5] Processing {} symbols...".format(len(SYMBOLS)))
    if not market_closed_today():
        print("      NOTE: Current IST time is before market close (3:30 PM).")
        print("            Today's data not yet available -- fetching up to last trading day.")
    print("      Market cap first -> prices only if >= Rs {} Cr\\n".format(MKTCAP_FLOOR))'''

if old2 not in src:
    raise SystemExit("Scenario 2 anchor not found -- aborting")
src = src.replace(old2, new2)

path.write_text(src)
print("Patch applied successfully.")
