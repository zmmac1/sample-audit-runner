#!/usr/bin/env python3
"""
Local parallel batch orchestrator for the sample audit.

Runs `scripts/sample-audit-runner.py` for each pending batch index in
parallel (default 4 workers). After each batch completes, commits and
pushes the results back to the repo. Skips batches that already have
results.

Designed to run as a double-forked daemon so it survives across bash
toolcalls. Logs to /home/z/my-project/audit-work/audit-orchestrator.log.

Usage:
  python3 scripts/local-audit-orchestrator.py --workers 4 --start 0 --end 145
  python3 scripts/local-audit-orchestrator.py --daemonize --workers 4
"""
import argparse, json, os, sys, time, subprocess, traceback, signal
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime

REPO_DIR = Path("/home/z/my-project/audit-work/web-daw-samples")
RESULTS_DIR = REPO_DIR / "audit-results"
BATCH_PLAN_PATH = REPO_DIR / "scripts" / "sample-batch-plan.json"
AUDIT_SCRIPT = REPO_DIR / "scripts" / "sample-audit-runner.py"
LOG_PATH = Path("/home/z/my-project/audit-work/audit-orchestrator.log")
PROGRESS_PATH = Path("/home/z/my-project/audit-work/audit-progress.json")
PAT = os.environ.get("GH_PAT", "os.environ.get("GH_PAT", "")")


def log(msg):
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_PATH, "a") as f:
        f.write(line + "\n")


def load_progress():
    if PROGRESS_PATH.exists():
        return json.loads(PROGRESS_PATH.read_text())
    return {"completed": [], "failed": [], "in_progress": {}, "last_run": None}


def save_progress(p):
    p["last_run"] = datetime.utcnow().isoformat()
    PROGRESS_PATH.write_text(json.dumps(p, indent=2))


def is_batch_done(batch_idx):
    """A batch is done if its jsonl AND summary exist and summary has totalZones > 0."""
    jsonl = RESULTS_DIR / f"batch-{batch_idx:03d}.jsonl"
    summary = RESULTS_DIR / f"batch-{batch_idx:03d}-summary.json"
    if not (jsonl.exists() and summary.exists()):
        return False
    try:
        s = json.loads(summary.read_text())
        return s.get("totalZones", 0) > 0
    except Exception:
        return False


def git_pull_rebase():
    """Pull latest changes with rebase."""
    try:
        r = subprocess.run(
            ["git", "pull", "--rebase", "--autostash",
             f"https://x-access-token:{PAT}@github.com/zulfikarbarbora-outl/web-daw-samples.git",
             "main"],
            cwd=REPO_DIR, capture_output=True, text=True, timeout=60
        )
        if r.returncode != 0:
            log(f"  git pull: {r.stdout[-200:]} {r.stderr[-200:]}")
        return r.returncode == 0
    except Exception as e:
        log(f"  git pull exception: {e}")
        return False


def git_commit_push(batch_idx):
    """Commit and push the batch results."""
    try:
        # Configure
        subprocess.run(["git", "config", "user.name", "audit-bot"], cwd=REPO_DIR, capture_output=True)
        subprocess.run(["git", "config", "user.email", "audit-bot@users.noreply.github.com"], cwd=REPO_DIR, capture_output=True)

        # Pull first to avoid conflicts
        git_pull_rebase()

        # Add specific batch files
        jsonl = f"audit-results/batch-{batch_idx:03d}.jsonl"
        summary = f"audit-results/batch-{batch_idx:03d}-summary.json"
        subprocess.run(["git", "add", jsonl, summary], cwd=REPO_DIR, capture_output=True)

        # Check if anything to commit
        r = subprocess.run(["git", "diff", "--staged", "--quiet"], cwd=REPO_DIR, capture_output=True)
        if r.returncode == 0:
            log(f"  batch {batch_idx}: no changes to commit")
            return True

        # Commit
        r = subprocess.run(
            ["git", "commit", "-m", f"audit: batch {batch_idx} results (local runner)"],
            cwd=REPO_DIR, capture_output=True, text=True
        )
        if r.returncode != 0:
            log(f"  git commit failed: {r.stderr[-200:]}")
            return False

        # Push with retry
        for attempt in range(1, 6):
            r = subprocess.run(
                ["git", "push",
                 f"https://x-access-token:{PAT}@github.com/zulfikarbarbora-outl/web-daw-samples.git",
                 "main"],
                cwd=REPO_DIR, capture_output=True, text=True, timeout=120
            )
            if r.returncode == 0:
                log(f"  batch {batch_idx}: pushed (attempt {attempt})")
                return True
            log(f"  batch {batch_idx}: push attempt {attempt} failed: {r.stderr[-200:]}")
            # Pull and rebase before retrying
            git_pull_rebase()
            time.sleep(attempt * 3)
        log(f"  batch {batch_idx}: push FAILED after 5 attempts")
        return False
    except Exception as e:
        log(f"  batch {batch_idx}: git exception: {e}")
        return False


def run_one_batch(batch_idx):
    """Run a single batch and push results. Returns (batch_idx, success, summary)."""
    log(f"START batch {batch_idx}")
    start = time.time()
    try:
        env = dict(os.environ)
        env["GH_TOKEN"] = PAT
        r = subprocess.run(
            ["python3", str(AUDIT_SCRIPT),
             "--batch-index", str(batch_idx),
             "--results-dir", str(RESULTS_DIR)],
            cwd=REPO_DIR, capture_output=True, text=True, timeout=3300,  # 55min max per batch
            env=env
        )
        elapsed = time.time() - start
        if r.returncode != 0:
            log(f"FAIL batch {batch_idx} (exit {r.returncode}, {elapsed:.0f}s)")
            log(f"  stderr: {r.stderr[-500:]}")
            log(f"  stdout tail: {r.stdout[-500:]}")
            return (batch_idx, False, None)

        # Read summary
        summary_path = RESULTS_DIR / f"batch-{batch_idx:03d}-summary.json"
        summary = json.loads(summary_path.read_text()) if summary_path.exists() else {}
        log(f"DONE batch {batch_idx} ({elapsed:.0f}s) — ok={summary.get('ok')} warn={summary.get('warn')} fail={summary.get('fail')} zones={summary.get('totalZones')}")

        # Push
        push_ok = git_commit_push(batch_idx)
        if not push_ok:
            log(f"  batch {batch_idx}: results saved locally but push failed")

        return (batch_idx, True, summary)
    except subprocess.TimeoutExpired:
        log(f"TIMEOUT batch {batch_idx} after {time.time()-start:.0f}s")
        return (batch_idx, False, None)
    except Exception as e:
        log(f"EXCEPTION batch {batch_idx}: {e}")
        log(traceback.format_exc())
        return (batch_idx, False, None)


def daemonize():
    """Double-fork to reparent to PID 1 (survives across bash toolcalls)."""
    if os.fork():
        sys.exit(0)
    os.setsid()
    if os.fork():
        sys.exit(0)
    sys.stdout.flush()
    sys.stderr.flush()
    devnull = os.open('/dev/null', os.O_RDWR)
    os.dup2(devnull, 0)
    # Keep stdout/stderr for logging via log()
    pid = os.getpid()
    log(f"Daemonized as PID {pid}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=4, help="parallel batch workers")
    ap.add_argument("--start", type=int, default=0, help="start batch index (inclusive)")
    ap.add_argument("--end", type=int, default=145, help="end batch index (exclusive)")
    ap.add_argument("--daemonize", action="store_true", help="run as double-forked daemon")
    args = ap.parse_args()

    if args.daemonize:
        daemonize()

    log(f"=== Audit orchestrator starting (workers={args.workers}, batches {args.start}-{args.end}) ===")
    log(f"REPO_DIR={REPO_DIR}")
    log(f"RESULTS_DIR={RESULTS_DIR}")

    # Pull latest
    log("Pulling latest from origin...")
    git_pull_rebase()

    # Find pending batches
    pending = []
    for i in range(args.start, args.end):
        if is_batch_done(i):
            log(f"  batch {i:3d}: already done, skipping")
        else:
            pending.append(i)
    log(f"Pending batches: {len(pending)} (of {args.end - args.start} total in range)")
    log(f"Pending list: {pending}")

    if not pending:
        log("Nothing to do — all batches in range already completed.")
        return

    progress = load_progress()
    save_progress(progress)

    # Run in parallel
    completed_count = 0
    failed_count = 0
    overall_start = time.time()

    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        futures = {ex.submit(run_one_batch, b): b for b in pending}
        for fut in as_completed(futures):
            batch_idx = futures[fut]
            try:
                idx, success, summary = fut.result()
                progress = load_progress()
                if success:
                    progress["completed"].append(idx)
                    completed_count += 1
                else:
                    progress["failed"].append(idx)
                    failed_count += 1
                save_progress(progress)
                log(f"Progress: {completed_count + failed_count}/{len(pending)} done (ok={completed_count}, fail={failed_count})")
            except Exception as e:
                log(f"Future for batch {batch_idx} raised: {e}")
                progress = load_progress()
                progress["failed"].append(batch_idx)
                save_progress(progress)

    elapsed = time.time() - overall_start
    log(f"=== Orchestrator finished in {elapsed:.0f}s — completed={completed_count}, failed={failed_count} ===")
    if failed_count > 0:
        log(f"Failed batches: {progress['failed']}")


if __name__ == "__main__":
    main()
