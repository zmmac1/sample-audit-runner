#!/usr/bin/env python3
"""
Background gitlab push retry daemon.

Periodically attempts to push the local web-daw-samples repo to gitlab as
a backup mirror. Retries every 10 minutes because gitlab's Cloudflare
edge frequently 403s HK IPs.

The gitlab project must exist before this can push. If it doesn't exist,
this will keep retrying (and failing) until the user creates it via the
gitlab web UI.
"""
import os, sys, time, subprocess
from datetime import datetime, timezone
from pathlib import Path

REPO_DIR = Path("/home/z/my-project/audit-work/web-daw-samples")
LOG_PATH = Path("/home/z/my-project/audit-work/gitlab-push.log")
INTERVAL_SECONDS = 600  # 10 minutes

GL_PAT = os.environ.get("GL_PAT", "os.environ.get("GL_PAT", "")")
GL_REMOTE = f"https://ansgareutychisO:{GL_PAT}@gitlab.com/ansgareutychisO/web-daw-samples.git"


def log(msg):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_PATH, "a") as f:
        f.write(line + "\n")


def daemonize():
    if os.fork(): os._exit(0)
    os.setsid()
    if os.fork(): os._exit(0)
    sys.stdout.flush(); sys.stderr.flush()
    devnull = os.open('/dev/null', os.O_RDWR)
    os.dup2(devnull, 0); os.dup2(devnull, 1); os.dup2(devnull, 2)


def try_push():
    """One push attempt. Returns True on success."""
    # First check if remote project exists
    r = subprocess.run(
        ["git", "ls-remote", GL_REMOTE, "HEAD"],
        cwd=REPO_DIR, capture_output=True, text=True, timeout=60
    )
    if r.returncode != 0:
        log(f"ls-remote failed (project likely doesn't exist yet or rate-limited): {r.stderr.strip()[:150]}")
        return False

    # Push to a backup branch (don't disturb main)
    r = subprocess.run(
        ["git", "push", "--force", GL_REMOTE, "main:audit-backup"],
        cwd=REPO_DIR, capture_output=True, text=True, timeout=300
    )
    if r.returncode == 0:
        log(f"Push OK: main -> audit-backup")
        return True
    else:
        log(f"Push failed: {r.stderr.strip()[:300]}")
        return False


def main():
    ap = argparse.ArgumentParser() if False else None
    if "--daemonize" in sys.argv:
        daemonize()
    log(f"=== Gitlab push daemon starting (interval={INTERVAL_SECONDS}s) ===")
    log(f"GL_REMOTE=...{GL_REMOTE[-40:]}")
    log(f"REPO_DIR={REPO_DIR}")

    success_count = 0
    attempt_count = 0
    while True:
        attempt_count += 1
        log(f"--- Attempt {attempt_count} ---")
        if try_push():
            success_count += 1
            log(f"Success count: {success_count}")
            # After first success, push less frequently (every hour)
            time.sleep(3600)
        else:
            time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
