# Sample Audit Runner — Status

## Current state (2026-08-16)

### GHA billing issue

The `zmmac1` account that owns this repo has a **billing lock** on GitHub
Actions. All workflow dispatches fail with:

> The job was not started because your account is locked due to a billing issue.

This affects:
- `zmmac1/sample-audit-runner` (this repo, public)
- `zulfikarbarbora-outl/web-daw-samples` (private, where zmmac1 has write access but the workflows run under zmmac1's billing)

### Workaround

Until the billing issue is resolved, the audit is running **locally in the
Z.ai Code sandbox container** via `scripts/local-audit-orchestrator.py`:

- 4 parallel workers (subprocess + multiprocessing.Pool)
- Each worker runs `sample-audit-runner.py` for one batch index
- After each batch completes, results are committed and pushed to
  `zulfikarbarbora-outl/web-daw-samples` main branch via PAT
- Runs as a double-forked daemon so it survives across bash toolcalls
- Does NOT survive container recycles — needs to be restarted manually
  after a recycle (see "Restart after recycle" below)

Throughput: ~2.2 zones/sec/worker × 4 workers = ~8.8 zones/sec total.
Full catalog (600k zones) would take ~19 hours wall clock.

### Where things live

| Artifact | Location |
|---|---|
| Audit script | `zulfikarbarbora-outl/web-daw-samples/scripts/sample-audit-runner.py` |
| Batch plan | `zulfikarbarbora-outl/web-daw-samples/scripts/sample-batch-plan.json` |
| GHA workflow (for when billing is fixed) | `zmmac1/sample-audit-runner/.github/workflows/sample-audit-matrix.yml` |
| Local orchestrator | `zmmac1/sample-audit-runner/scripts/local-audit-orchestrator.py` |
| Results | `zulfikarbarbora-outl/web-daw-samples/audit-results/batch-NNN.{jsonl,summary.json}` |
| Progress log | `/home/z/my-project/audit-work/audit-orchestrator.log` |
| Progress JSON | `/home/z/my-project/audit-work/audit-progress.json` |

### Restart after recycle

If the container recycles, the daemon dies. To restart:

```bash
cd /home/z/my-project/audit-work
# Pull latest (in case results were pushed by a previous run)
cd web-daw-samples && git pull && cd ..
# Re-launch the daemon
python3 -c "
import os, sys
def daemonize():
    if os.fork(): os._exit(0)
    os.setsid()
    if os.fork(): os._exit(0)
    sys.stdout.flush(); sys.stderr.flush()
    devnull = os.open('/dev/null', os.O_RDWR)
    os.dup2(devnull, 0); os.dup2(devnull, 1); os.dup2(devnull, 2)
daemonize()
os.execvp('python3', ['python3', '/home/z/my-project/audit-work/local-audit-orchestrator.py', '--workers', '4', '--start', '0', '--end', '145'])
"
```

The orchestrator automatically skips batches that already have results, so
it's safe to re-run after a restart.

### To fix the GHA billing

1. Log into GitHub as `zmmac1`
2. Go to Settings → Billing & plans → Actions
3. Add a payment method or upgrade to a plan that includes Actions minutes
4. After the billing lock is lifted, dispatch the workflow:
   ```bash
   curl -X POST -H "Authorization: token $PAT"      https://api.github.com/repos/zmmac1/sample-audit-runner/actions/workflows/sample-audit-matrix.yml/dispatches      -d '{"ref":"main","inputs":{"start-batch":"0","end-batch":"5"}}'
   ```

### GitLab backup

A daemon (`scripts/gitlab-push-daemon.py`) retries pushing
`web-daw-samples` to `gitlab.com/ansgareutychisO/web-daw-samples` every
10 minutes. The gitlab project does NOT exist yet — the user needs to
create it via the gitlab web UI first. Once created, the daemon will
automatically start pushing.
