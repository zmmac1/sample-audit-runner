# sample-audit-runner

Thin GHA runner repo for the sample-library audit. The actual audit script,
batch plan, manifests, and results all live in
[`zulfikarbarbora-outl/web-daw-samples`](https://github.com/zulfikarbarbora-outl/web-daw-samples).

## Why a separate repo?

`web-daw-samples` is private, and its owner account does not have GitHub
Actions billing for private repos, so workflows there fail with
`startup_failure`. Public repos have free GHA minutes, so this thin runner
repo (public) hosts the workflow and uses a PAT to read/write
`web-daw-samples`.

## What the workflow does

For each batch index in the matrix:

1. Checkout `web-daw-samples` (private) via PAT into `./web-daw-samples`
2. Install ffmpeg + Python deps (librosa, soundfile, pyloudnorm)
3. Run `scripts/sample-audit-runner.py --batch-index N --results-dir audit-results/`
4. Commit `audit-results/batch-NNN.{jsonl,summary.json}` back to
   `web-daw-samples` main (with retry on concurrent matrix pushes)
5. Upload the same files as a workflow artifact (90-day retention)

## Dispatching

Manual dispatch via the GitHub Actions UI or API:

```bash
# Dispatch batches 0-5 (first 5)
curl -X POST \
  -H "Authorization: token $GH_TOKEN" \
  -H "Accept: application/vnd.github+json" \
  https://api.github.com/repos/zmmac1/sample-audit-runner/actions/workflows/sample-audit-matrix.yml/dispatches \
  -d '{"ref":"main","inputs":{"start-batch":"0","end-batch":"5"}}'
```

The batch plan (`scripts/sample-batch-plan.json` in `web-daw-samples`) has
145 batches covering 65 libraries (~600k zones total). Default dispatch range
is 5 batches at a time to stay within GHA's 6-hour job limit per matrix entry.

## Secrets

- `GH_TOKEN` — PAT with `repo` scope on `zulfikarbarbora-outl/web-daw-samples`
  (used for checkout + push-back of results).
- `RESULTS_PAT` — same value, kept as a redundant alias.

## Status / monitoring

```bash
# Check run status
curl -s -H "Authorization: token $GH_TOKEN" \
  https://api.github.com/repos/zmmac1/sample-audit-runner/actions/runs?per_page=5 \
  | python3 -m json.tool

# Results live in:
# https://github.com/zulfikarbarbora-outl/web-daw-samples/tree/main/audit-results
```
