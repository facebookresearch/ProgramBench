#!/usr/bin/env bash
# Re-run PB gold eval on the instances expected to move after the
# 2026-04-30 / 2026-05-01 sweep, writing results to a separate output
# directory so the original ~/gold/ stays untouched for before/after diffs.
#
# Fixes that should reflect here:
#   - programbench 2f66f6a75: 300s timeouts on copy/restore/hash
#       → reclaims the 8 copy_executable_failed instances + 6 branch-level errors.
#   - programbench 54bf3f61f: deterministic synthetic .git seed
#       → executable_hash matches across runs for SHA-embedding builds.
#   - programbench 9c50d5008/5cf8e78f6: --branch-retries=3 (default)
#       → retries any branch whose JUnit reports xdist worker crashes.
#   - programbench dece819a6: default xdist hardening + pytest-rerunfailures
#       → inside-run flake recovery (--max-worker-restart=4 + --reruns=2).
#   - RevEngBench 7fa85da1: download_all worktree fallback
#       → google__brotli now populates and evals.
#
# Brotli is the only instance whose populate-side data was stale. If you
# haven't already, run BEFORE this script:
#
#   uv run python /home/kilian/RevEngBench/scripts/populate_programbench_tasks.py \
#       --filter 'google__brotli' --force
#   uv run python /home/kilian/RevEngBench/scripts/make_gold_submissions.py \
#       --filter 'google__brotli' --force
#
# The other 49 instances re-use existing submission.zips and tasks-blob.

set -euo pipefail

# 8 instances that errored copy_executable_failed at 20s
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

# 1 instance previously absent from PB output (download_all export-ignore fix)
BROTLI=(
    'google__brotli\.b3dc9cc'
)

# 31 instances flagged by the worker-crash audit (≥1 branch with xdist crashes
# in the PB run). Now benefit from xdist max-worker-restart + rerunfailures +
# per-branch retries.
XDIST_AFFECTED=(
    'axodotdev__oranda\.27d60c7'
    'byron__dua-cli\.8570c15'
    'dundee__gdu\.ede21d2'
    'go-critic__go-critic\.9aea378'
    'hatoo__oha\.8dc6349'
    'hush-shell__hush\.560c33a'
    'jarun__nnn\.cb2c535'
    'johnkerl__miller\.8d85b46'
    'kisielk__errcheck\.dacab89'
    'kyoh86__richgo\.313114f'
    'lfos__calcurse\.49180d5'
    'luajit__luajit\.a553b3d'
    'lz4__lz4\.1519f46'
    'mgechev__revive\.201451e'
    'peco__peco\.4e58dad'
    'quinn-rs__quinn\.bb359cc'
    'rhysd__kiro-editor\.4157485'
    'rochacbruno__marmite\.7d4bc2d'
    'rust-lang__mdbook\.37273ba'
    'segmentio__chamber\.5f93f5f'
    'sharkdp__fd\.40d8eb3'
    'sibprogrammer__xq\.b89f681'
    'simeg__eureka\.df3796c'
    'skeema__skeema\.6a76243'
    'stacked-git__stgit\.430027d'
    'svenstaro__miniserve\.8449e8b'
    'tarka__xcp\.5e5b448'
    'trasta298__keifu\.3331426'
    'tree-sitter__tree-sitter\.5e23cca'
    'tukaani-project__xz\.1007bf0'
)

ALL=(
    "${TIMEOUT_HIT[@]}"
    "${BRANCH_TIMEOUT_HIT[@]}"
    "${BROTLI[@]}"
    "${XDIST_AFFECTED[@]}"
)
FILTER="$(IFS='|'; echo "${ALL[*]}")"

WORKERS="${WORKERS:-8}"
GOLD_DIR="${GOLD_DIR:-$HOME/gold}"
OUTPUT_DIR="${OUTPUT_DIR:-$HOME/gold-eval-after-fixes}"
BLOB_DIR="${PROGRAMBENCH_BLOB_DIR:-$HOME/programbench/src/programbench/data/tasks-blob}"

cd "$(dirname "$0")/.."
export PROGRAMBENCH_BLOB_DIR="$BLOB_DIR"

echo "Re-running ${#ALL[@]} instances with --workers $WORKERS"
echo "Source : $GOLD_DIR  (untouched)"
echo "Output : $OUTPUT_DIR/$(basename "$GOLD_DIR")/"
echo

uv run programbench eval "$GOLD_DIR" \
    --output "$OUTPUT_DIR" \
    --force \
    --workers "$WORKERS" \
    --filter "$FILTER" \
    "$@"

echo
echo "Diff results against the original run:"
echo "  diff <(ls $GOLD_DIR) <(ls $OUTPUT_DIR/$(basename $GOLD_DIR))"
echo "  python3 /tmp/compare_gold.py  # (or whatever before/after script you've got)"
