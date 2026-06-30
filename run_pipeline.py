"""
NSE Factor Engine — Master Pipeline Runner
Runs Stage 1 (Universe) -> Stage 2 (Core Momentum) -> Stage 3 (Quality)
-> Stage 4 (Entry Quality Filters) -> Stage 5 (Ranking & Selection) in
sequence.

Each stage is invoked as a subprocess, exactly as it would be run manually.
This script does NOT reimplement any stage's logic — it only sequences
the existing, already-verified entry-point scripts:
    universe/run_universe.py
    signals/stage2/stage2_step5_assemble.py
    signals/stage3/stage3_assemble.py
    signals/stage4/stage4_assemble.py
    signals/stage5/stage5_assemble.py

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
  - Filenames are keyed to RUN_DATE (IST calendar date the pipeline
    executed), NOT T — confirmed during Stage 5 build (2026-06-30). T
    (latest trading day with complete data) is recorded separately as
    as_of_date inside each file and can lag RUN_DATE. Stage 5 requires
    an exact RUN_DATE match between universe_{run_date}.parquet and
    momentum_signals_final_{run_date}.parquet — since Stage 1 and
    Stage 2-4 run back-to-back in this same script under a single
    invocation, both stamp the same RUN_DATE by construction, so this
    match is automatic when run via run_pipeline.py. (Running Stage 5
    standalone on a day Stage 1 was skipped is the one case where this
    could mismatch — Stage 5 will assert and stop rather than guess.)

RESOLVED (previously flagged as a known gap, closed in Stage 5):
  in_universe (computed in Stage 1, universe/universe_{DDMMYYYY}.parquet)
  was not applied anywhere in Stage 2/3/4 -- those stages still produce
  signals for the full ~500-symbol universe, by design (see Stage 2/3/4
  docs: "in_universe merge deferred to Stage 4" was itself deferred again
  to Stage 5). Stage 5 is the first stage to load in_universe, merge it
  onto the final signals file, and use it to scope ranking to the
  investable universe only. Non-investable symbols are RETAINED in the
  final output (not dropped) with in_universe=False and NaN rank/FIP
  columns, per explicit decision during the Stage 5 build.
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
    print(msg)
    with open(RUN_LOG_PATH, "a") as f:
        f.write(msg + "\n")


def run_stage(label, script_path, extra_env=None):
    log("\n" + "=" * 70)
    log(f"STARTING {label}")
    log(f"Script: {script_path}")
    log(f"Time  : {datetime.now().isoformat()}")
    log("=" * 70)

    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)

    process = subprocess.Popen(
        [sys.executable, "-u", str(script_path)],
        cwd=str(BASE),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
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
                f"proceed to Stage 2-5 on a meaningfully incomplete "
                f"universe. !!!"
            )
            sys.exit(1)
        else:
            log(
                f"\n{n_failed} failures < threshold "
                f"({FAILED_SYMBOL_HALT_THRESHOLD}) — proceeding to "
                f"Stage 2-5, but this is a WARNING, not a clean run."
            )
    else:
        log("\nStage 1: 0 failed symbols. Clean universe run.")

    run_stage(
        "STAGE 2 — Momentum Core Signals",
        BASE / "signals" / "stage2" / "stage2_step5_assemble.py",
    )

    run_stage(
        "STAGE 3 — Momentum Quality Signals",
        BASE / "signals" / "stage3" / "stage3_assemble.py",
    )

    run_stage(
        "STAGE 4 — Entry Quality Filters",
        BASE / "signals" / "stage4" / "stage4_assemble.py",
    )

    run_stage(
        "STAGE 5 — Ranking & Selection",
        BASE / "signals" / "stage5" / "stage5_assemble.py",
    )

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
