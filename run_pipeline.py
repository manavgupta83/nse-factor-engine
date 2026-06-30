"""
NSE Factor Engine — Master Pipeline Runner
Runs Stage 1 (Universe) -> Stage 2 (Core Momentum) -> Stage 3 (Quality)
-> Stage 4 (Entry Quality Filters) in sequence.

Each stage is invoked as a subprocess, exactly as it would be run manually.
This script does NOT reimplement any stage's logic — it only sequences
the existing, already-verified entry-point scripts:
    universe/run_universe.py
    signals/stage2/stage2_step5_assemble.py
    signals/stage3/stage3_assemble.py
    signals/stage4/stage4_assemble.py

DESIGN DECISIONS (confirmed explicitly before writing this script):
  - Stage 1 runs under TZ=Asia/Kolkata so date.today() inside
    run_universe.py resolves to the IST calendar date, not the server's
    UTC date. run_universe.py itself is NOT modified for this (the
    universe_*.parquet filename format WAS separately changed to
    DDMMYYYY for consistency with the rest of the pipeline — see
    run_universe.py edit history).
  - If Stage 1 finishes with >=5 symbols still failing after its own
    internal retries, the pipeline HALTS before Stage 2 -- does not
    proceed on a meaningfully incomplete universe. Threshold is
    configurable below (FAILED_SYMBOL_HALT_THRESHOLD).
  - Each stage already resolves T independently from prices.parquet
    (date_counts >= 490 convention) -- this script does NOT compute or
    pass T between stages. Each stage's own internal resolution is the
    source of truth.

KNOWN GAP (flagged, not fixed here):
  Stage 2's script contains the comment "in_universe merge deferred to
  Stage 4" -- the in_universe filter computed in Stage 1
  (universe/universe_{DDMMYYYY}.parquet) is NOT currently applied
  anywhere in Stage 2, 3, or 4. This master script does not silently
  add that filtering -- it is a pre-existing gap, left for explicit
  resolution in a future stage/conversation.
"""
import subprocess
import sys
import os
import glob
from datetime import date, datetime
from pathlib import Path

BASE = Path("/home/ec2-user/nse-factor-engine")
FAILED_SYMBOL_HALT_THRESHOLD = 5

LOG_DIR = BASE / "logs"
LOG_DIR.mkdir(exist_ok=True)
RUN_LOG_PATH = LOG_DIR / f"master_run_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"


def log(msg):
    """Print to stdout AND append to a dedicated run log for this invocation."""
    print(msg)
    with open(RUN_LOG_PATH, "a") as f:
        f.write(msg + "\n")


def run_stage(label, script_path, extra_env=None):
    """
    Run a stage script as a subprocess, streaming its output LIVE to
    stdout line-by-line as it runs (not buffered until completion) while
    also writing every line to the run log. Important for Stage 1 in
    particular, which can run 15+ minutes with per-symbol progress
    prints — without live streaming you'd see nothing until it finished.
    Raises SystemExit if the stage fails (non-zero exit code).
    """
    log("\n" + "=" * 70)
    log(f"STARTING {label}")
    log(f"Script: {script_path}")
    log(f"Time  : {datetime.now().isoformat()}")
    log("=" * 70)

    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)

    process = subprocess.Popen(
        [sys.executable, "-u", str(script_path)],  # -u: unbuffered child stdout
        cwd=str(BASE),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,  # merge stderr into stdout, single live stream
        text=True,
        bufsize=1,  # line-buffered
    )

    for line in process.stdout:
        log(line.rstrip("\n"))

    process.wait()
    returncode = process.returncode

    if returncode != 0:
        log(f"\n!!! {label} FAILED with exit code {returncode} !!!")
        log(f"Halting pipeline. See {RUN_LOG_PATH} for full output.")
        sys.exit(1)

    log(f"\n{label} completed successfully (exit code 0).")
    return returncode


def check_stage1_failures():
    """
    run_universe.py writes data/failed_symbols_{YYYYMMDD}.csv ONLY if
    symbols are still failing after its internal retries. Absence of
    the file means zero failures. Returns (count, path_or_None).
    Uses IST date (matching what run_universe.py uses under
    TZ=Asia/Kolkata) to find the right file. NOTE: failed_symbols_*.csv
    intentionally still uses YYYYMMDD (unlike universe_*.parquet, which
    was changed to DDMMYYYY) -- this is an internal artifact only this
    function reads programmatically.
    """
    import zoneinfo
    ist_today = datetime.now(zoneinfo.ZoneInfo("Asia/Kolkata")).date()
    failed_path = BASE / "data" / f"failed_symbols_{ist_today.strftime('%Y%m%d')}.csv"

    if not failed_path.exists():
        return 0, None

    import pandas as pd
    failed_df = pd.read_csv(failed_path)
    return len(failed_df), failed_path


def main():
    log("#" * 70)
    log("NSE FACTOR ENGINE — MASTER PIPELINE RUN")
    log(f"Started: {datetime.now().isoformat()}")
    log(f"Log file: {RUN_LOG_PATH}")
    log("#" * 70)

    # ── Stage 1: Universe ──────────────────────────────────────────
    run_stage(
        "STAGE 1 — Universe & Liquidity",
        BASE / "universe" / "run_universe.py",
        extra_env={"TZ": "Asia/Kolkata"},
    )

    n_failed, failed_path = check_stage1_failures()
    if n_failed > 0:
        log(f"\nStage 1 finished with {n_failed} symbol(s) still failing.")
        log(f"Failed symbols file: {failed_path}")
        if n_failed >= FAILED_SYMBOL_HALT_THRESHOLD:
            log(
                f"\n!!! HALTING: {n_failed} failures >= threshold "
                f"({FAILED_SYMBOL_HALT_THRESHOLD}). Pipeline will NOT "
                f"proceed to Stage 2-4 on a meaningfully incomplete "
                f"universe. !!!"
            )
            sys.exit(1)
        else:
            log(
                f"\n{n_failed} failures < threshold "
                f"({FAILED_SYMBOL_HALT_THRESHOLD}) — proceeding to "
                f"Stage 2-4, but this is a WARNING, not a clean run."
            )
    else:
        log("\nStage 1: 0 failed symbols. Clean universe run.")

    # ── Stage 2: Core Momentum ─────────────────────────────────────
    run_stage(
        "STAGE 2 — Momentum Core Signals",
        BASE / "signals" / "stage2" / "stage2_step5_assemble.py",
    )

    # ── Stage 3: Quality Signals ───────────────────────────────────
    run_stage(
        "STAGE 3 — Momentum Quality Signals",
        BASE / "signals" / "stage3" / "stage3_assemble.py",
    )

    # ── Stage 4: Entry Quality Filters ─────────────────────────────
    run_stage(
        "STAGE 4 — Entry Quality Filters",
        BASE / "signals" / "stage4" / "stage4_assemble.py",
    )

    # ── Final summary ──────────────────────────────────────────────
    final_files = sorted(
        glob.glob(str(BASE / "signals" / "final" / "momentum_signals_final_*.parquet"))
    )
    final_files = [f for f in final_files if "backup" not in f]

    log("\n" + "#" * 70)
    log("PIPELINE COMPLETE")
    log(f"Finished: {datetime.now().isoformat()}")
    log(f"Final signals file(s) present: {final_files}")
    log(f"Full run log: {RUN_LOG_PATH}")
    log("#" * 70)


if __name__ == "__main__":
    main()
