#!/usr/bin/env bash
# Re-run gold eval only on instances that should benefit from fixes shipped
# after the 2026-04-30 PB-vs-RB sweep:
#
#   - programbench 2f66f6a75: 300s timeouts on copy/restore/hash steps
#       fixes the 8 instances that errored copy_executable_failed and the 6
#       branches that errored restore_executable_failed / results_read_failed
#       / clean_stale_results_failed.
#
#   - programbench 54bf3f61f: deterministic synthetic .git seed
#       fixes executable_hash drift for builds that embed the SHA (cargo+vergen
#       and similar). This re-uses the same submission.zips already on disk;
#       no make_gold_submissions.py rebuild needed.
#
#   - RevEngBench 7fa85da1: download_all worktree fallback for export-ignore
#       fixes google__brotli, which previously had no PB eval json at all.
#       NOTE: brotli zips are already re-downloaded; you still need to
#       repopulate (see preflight below) and rebuild its gold submission.
#
# What you must do BEFORE running this script
# -------------------------------------------
# Brotli is the only instance whose data on disk is stale. The zips were
# refreshed on 2026-05-01 but the populate-side blob and the
# make_gold_submissions output haven't been regenerated yet. Run:
#
#   uv run python /home/kilian/RevEngBench/scripts/populate_programbench_tasks.py \
#       --filter 'google__brotli' --force
#   uv run python /home/kilian/RevEngBench/scripts/make_gold_submissions.py \
#       --filter 'google__brotli' --force
#
# Everything else (the 14 timeout-hit instances and the determinism-hit
# instances) re-uses existing submission.zips and existing tasks-blob data —
# the timeouts and seed are pure eval-time behaviour.

set -euo pipefail

# 8 instances that errored copy_executable_failed at 20s (timeout fix #2f66f6a75)
TIMEOUT_HIT=(
    'danmar__cppcheck\.0a5b103'
    'doxygen__doxygen\.966d98e'
    'duckdb__duckdb\.bdb65ec'
    'epistates__treemd\.825c6dd'
    'ip7z__7zip\.839151e'
    'ivanceras__svgbob\.6d00ad9'
    'jesseduffield__lazygit\.1d0db51'
    'rust-embedded__svd2rust\.1760b5e'
)

# 6 instances with branch-level restore/results/clean failures at 20s
BRANCH_TIMEOUT_HIT=(
    'eudoxia0__hashcards\.48aa136'
    'mkj__dropbear\.75f699b'
    'rvben__rumdl\.2d75c4d'
    'sayanarijit__xplr\.1751065'
    'sstadick__hck\.b66c751'
    'tomarrell__wrapcheck\.c058da1'
)

# 1 instance previously missing entirely from PB output (download_all fix #7fa85da1)
BROTLI=(
    'google__brotli\.b3dc9cc'
)

# Determinism fix #54bf3f61f only affects executable_hash for SHA-embedding
# builds; it does not change pass/fail. To check whether it matters, re-run
# the full eval and diff exec hashes — but the targeted set above is the
# minimum to see the parity gap close. To also re-check determinism on the
# 79 instances whose exec hash didn't match RB last run, set ALL_HASH=1.
if [[ "${ALL_HASH:-0}" == "1" ]]; then
    HASH_DRIFT_FROM_LAST_COMPARISON=(
        # Add iids here if you want to verify the determinism fix on them.
        # Empty by default — leave the broad sweep to a separate full run.
    )
else
    HASH_DRIFT_FROM_LAST_COMPARISON=()
fi

ALL=(
    "${TIMEOUT_HIT[@]}"
    "${BRANCH_TIMEOUT_HIT[@]}"
    "${BROTLI[@]}"
    "${HASH_DRIFT_FROM_LAST_COMPARISON[@]}"
)

FILTER="$(IFS='|'; echo "${ALL[*]}")"

WORKERS="${WORKERS:-8}"
GOLD_DIR="${GOLD_DIR:-$HOME/gold}"
BLOB_DIR="${PROGRAMBENCH_BLOB_DIR:-$HOME/programbench/src/programbench/data/tasks-blob}"

cd "$(dirname "$0")/.."
export PROGRAMBENCH_BLOB_DIR="$BLOB_DIR"

echo "Re-running ${#ALL[@]} instances with --workers $WORKERS"
echo "Filter: $FILTER"
echo

uv run programbench eval "$GOLD_DIR" \
    --force \
    --workers "$WORKERS" \
    --filter "$FILTER" \
    "$@"
