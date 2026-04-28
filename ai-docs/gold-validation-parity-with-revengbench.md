# Gold validation parity: programbench vs RevEngBench

Investigation and fixes for divergences between programbench gold validation
(`programbench eval ~/gold`) and the legacy RevEngBench gold validation
(`RevEngBench/output/kilian/gold_*`). The two pipelines should produce the
same per-instance scores when given equivalent inputs.

## Setup recap

- **Legacy ("A"):** RevEngBench's source eval. Solution branch is `gold`,
  test branches are checked out via `git checkout mirror/<branch>` from a
  bare clone of the GitHub mirror. Workspace at test time has the full
  branch tree (because `git checkout` writes everything that's committed).
- **New ("B"):** programbench. Gold mode was removed in programbench commit
  `184dbaf`. Gold validation now runs through the regular submission path:
  `RevEngBench/scripts/make_gold_submissions.py` builds "false" gold
  submissions from `output/github-dump/<iid>/build.zip` (renaming `build.sh`
  to `compile.sh`); `programbench eval ~/gold` then unzips them via
  `docker cp` and runs `compile.sh` like any other submission.
- **Test data:** populated by `RevEngBench/scripts/populate_programbench_tasks.py`
  into `programbench/src/programbench/data/{tasks,tasks-blob}/<iid>/`.
  Branches are keyed by 12-char SHA-256 of the original branch name; each
  branch ships its `eval/` tree (and, after fixes, the rest of the
  workspace tree) under `tasks-blob/<iid>/tests/<branch_hash>/`.

## Original symptoms (190 common instances)

| metric                           | A (legacy) | B (initial) |
|----------------------------------|-----------:|------------:|
| total branches evaluated         |      2,689 |       1,714 |
| total test results               |    575,133 |     441,154 |
| `passed`                         |    525,089 |     299,929 |
| `failure`                        |     26,531 |      49,900 |
| `not_run`                        |      9,454 |    **84,964** |
| `error_code=compile_failed`      |          0 |          15 |
| mean per-project pass rate       |      0.945 |       0.703 |

Six distinct root causes, each fixable independently.

## Root causes and fixes

### 1. `zipfile.extractall()` strips Unix file modes
**Symptom:** 12 instances (sqlite, ffmpeg, htop, jq, tinycc, dropbear,
the_silver_searcher, chafa, tig, lnav, ctags, php-src, eradman/entr) failed
with `error_code: compile_failed`, `error_details: ./configure: Permission
denied`. `submission.zip` correctly stored Unix mode `0o755` for `configure`,
but Python's `zipfile.extractall()` doesn't apply Unix permission bits on
extraction.

**Fix:** programbench `f596325` —
`programbench/src/programbench/eval/eval.py::_compile_executable()` now
extracts members one at a time and `os.chmod()`s each based on
`info.external_attr`. The `chmod +x ./compile.sh` step stays as a safety
net for submissions that don't store modes at all.

### 2. Tests-fixture filter conflated test-gen artifacts with runtime fixtures
**Symptom:** ~12,000 tests showed up as `not_run` across 7+ projects
(osgeo/proj scaffold branch alone: 6,157 not_run) because tests crashed
with `FileNotFoundError`s for paths like
`/workspace/eval/test_resources/test_crs_input/wkt_crs.golden`. The same
`.golden` extension was used both for test-gen snapshot artifacts (safe to
strip) and for runtime reference fixtures (must be kept).

**Fix:** revengbench `c5bdbebd` (in `populate_programbench_tasks.py`) — path-aware
filter. `.md` and `.profraw` are stripped only at the **immediate `eval/`
root** (where LM reports like `COMPLETION_REPORT.md`,
`FUNCTIONALITY_LIST.md` live). Anything in subdirectories
(`eval/test_resources/...`) is kept. `.golden` is no longer dropped at all.

### 3. Build steps need a working `.git` directory
**Symptom:** `jqlang/jq`, `lfos/calcurse`, `paradigmxyz/solar` failed
`compile_failed` with `fatal: not a git repository`. Their build scripts
need `.git`: jq runs `git submodule update --init --recursive` for vendored
oniguruma; calcurse's `autogen.sh` calls `autopoint --force`; solar's
cargo build invokes `vergen`, which calls `git rev-parse --is-inside-work-tree`.
Legacy A got this for free via `git clone <upstream>`; the submission-zip
path delivers only the source tree.

**Fix:** programbench `c501fee` —
`_compile_executable()` runs `if [ ! -d .git ]; then git init && git add -A
&& git commit ...; fi` after extracting submission.zip but before
`compile.sh`. Bypasses `commit.gpgsign` to dodge user-global signing
requirements. The synthetic SHA differs from upstream's, so binaries that
embed the SHA may not byte-match A's gold (already non-reproducible for
68/168 instances anyway).

### 4. Branch trees shipped only `eval/` — workspace-root data was dropped
**Symptom:** ~85% pass-rate regressions on instances where executable_hash
matched A exactly (agourlay/zip-password-finder 99.9%→14.3%,
cmatsuoka/figlet 99.9%→19.5%, cslarsen/jp2a 99.5%→21.1%, cweill/gotests
97.1%→20.5%, ecumene/rust-sloth 99.3%→26.4%, ...). Same binary, very
different test outcomes. Tests like `./executable -i
test-files/2.test.txt.zip` failed because `test-files/` (a workspace-root
directory of zip fixtures shipped in the branch zip) wasn't in the docker
workspace. Same applied to pytest rootdir markers (`pyproject.toml`,
`setup.cfg`, ...) — without them at `/workspace`, pytest's rootdir
resolved differently, producing test IDs like `tests.<x>` instead of
`eval.tests.<x>` and mismatching `tests.json` declarations.

**Fix:** revengbench `9da40562` — `_select_members()` no longer restricts
to `eval/`. The full branch tree is shipped (minus the bytecode/LM-noise
filter from `_keep_member`). Branch zips are `git archive` output, so
build artefacts (`target/`, `build/`, `node_modules/`) aren't there
anyway — what ships matches what `git checkout mirror/<branch>` produced
in A.

A short-lived earlier attempt (`81637b8c`, **reverted** in `f5ab9b23`)
synthesized a comment-only `pyproject.toml` when no upstream marker
existed. That was wrong: many branches' `tests.json` was authored against
the **marker-less** behaviour (the `tests.<x>` form pytest emits when
`run.sh` `cd`s into `eval/`). Forcing pytest's rootdir to `/workspace`
broke those branches' parity with `tests.json`.

### 5. `download-all` truncated branch listing at 100 per repo
**Symptom:** populate consistently warned "missing zip for branch X" for
hundreds of branches across 122 instances, even after running
`download_all`. download_all reported "all N branches present, skipping"
without warnings. For ammarabouzor/tui-journal: 247 branches on GitHub,
download_all only saw 100 → 19 active branches were missing on disk that
the script never even tried to fetch.

**Cause:** `api.repos.list_branches(org, repo, per_page=100)` returns just
the first page (capped at 100). The `repos` listing on the line above
already used `paged(...)` for proper pagination; the branch listing
forgot. Repos with >100 branches silently lost their tail.

**Fix:** revengbench `40f41ddb` — wrap `list_branches` in
`paged(api.repos.list_branches, org, instance_id, per_page=100)`. Verified
on ammarabouzor: pre-fix listing returns `100`, post-fix returns `247`.

### 6. LFS smudge filter aborted `git archive` mid-run
**Two-stage issue.** First, `download_all` was failing with `git-lfs:
command not found` because the user's global git config had `[filter
"lfs"] required = true` (from a prior `git lfs install`) but git-lfs
wasn't on PATH. Adding a startup check (`d4b20485`) helped diagnose but
didn't fix.

After installing git-lfs, the failure mode shifted to `Object does not
exist on the server: [404]` — the mirror references LFS oids whose blobs
the server pruned. With `required=true` this aborts the whole archive,
silently dropping the branch.

**Fix:** revengbench `7917f3fb` (supersedes the install-check) — pass
`-c filter.lfs.required=false -c filter.lfs.smudge=cat -c
filter.lfs.clean=cat -c filter.lfs.process=` to `git archive` so lfs
becomes a no-op pass-through. The archive ships the literal pointer text
for those paths and proceeds. download-all no longer needs git-lfs at
all; the install check was dropped.

## Other changes shipped along the way

- `f596325` programbench: preserve Unix file modes on `submission.zip`
  extraction (Cause 1 fix).
- `0d77dca40` programbench: include `instance_id` in branch-level
  warnings. Output now reads
  `[abishekvashok__cmatrix.5c082c6] branch 79b69dd3fd98: ...`.
  Threaded through `Evaluator(..., instance_id=...)` and
  `_process_branch_xml(..., instance_id=...)`.
- revengbench `b6f32927`: `--no-clean` flag on
  `populate_programbench_tasks.py` — copies branch contents verbatim
  without applying the `_keep_member` filter at all. Useful when chasing
  parity discrepancies.
- revengbench `5c0c1c72`: `--force-metadata` flag (subsequent edit by
  user/linter, see file at HEAD) — refreshes `task.yaml`/`tests.json`
  only, leaves the extracted blob tree untouched.

## Known residual issues

### `tests.json` declares `tests: []` for 85 of 2,900 active branches
**~3% of branches** declare an empty test list (mostly
`frozen-scaffold-testgen-*`, `coverage-test-jeff-*`, `test-johnby-*`).
Pytest discovers and runs tests anyway; the score is computed from XML
results, so it's correct. Eval pipeline produces noisy
"N test(s) in JUnit XML not in tests.json" warnings for these.

A suppression patch was prototyped (programbench commit `d0cf9efc1`,
**reverted**) on the user's request — they preferred the loud signal
over the quieter behaviour.

### Synthetic `.git` produces different binaries than legacy
The legacy gold pipeline did `git clone <upstream>` so the binary embedded
the real upstream commit SHA. Our synthetic seed produces a different SHA
each time, so binaries that embed git metadata won't byte-match A. This
explains a chunk of the remaining executable_hash mismatches and any
test that asserts on embedded version strings. Not addressable without
re-introducing real upstream cloning.

### `gromacs/gromacs` `copy_executable_failed`
Single-instance failure. `compile.sh` runs but doesn't produce
`./executable`. Likely a build-system interaction with the synthetic
`.git`; not investigated in depth.

### Branch coverage in `output/github-dump/`
Even after the pagination fix, populate may still warn "missing zip" for
branches that were:
- Deleted from the GitHub mirror after `tests.json` was authored
  (truly stale `tests.json` entries).
- Hit transient network/API failures during download_all.

The mismatch is now an order of magnitude smaller post-fix and indicative
of real upstream gaps rather than a tooling bug.

## Commit timeline

### programbench
- `f596325` Fix: preserve Unix file modes when extracting submission.zip
- `c501fee` Fix: seed a synthetic .git in the workspace before compile.sh
- `0d77dca40` eval: include instance_id in branch-level warnings

### RevEngBench
- `c5bdbebd` Fix: keep .golden / subdir-.md test fixtures in populate_programbench_tasks
- `db4198a3` Fix(populate tasks): ship workspace-root pytest rootdir markers alongside eval/
- `81637b8c` Fix(populate tasks): synthesise a workspace-root pyproject.toml when none ships
- `f5ab9b23` Revert(populate tasks): drop synthetic pyproject.toml fallback
- `9da40562` Fix(populate tasks): ship full branch tree instead of eval/ only
- `b6f32927` Feat(populate tasks): add --no-clean to copy branches verbatim
- `d4b20485` Fix(download branches): hard-fail at startup when git-lfs is missing
- `7917f3fb` Fix(download branches): bypass the LFS smudge filter on git archive
- `40f41ddb` Fix(download branches): paginate list_branches, was capped at 100

## How to reproduce / restart from scratch

```bash
# 1. Refresh the GitHub dump (now correctly paginates >100-branch repos
#    and bypasses LFS smudging).
export GITHUB_TOKEN=...      # from RevEngBench/.env
revenge run download-all -w 8 --branch-workers 4

# 2. Repopulate programbench task data (ships full branch tree minus
#    bytecode/LM-noise; --no-clean for verbatim copy when debugging).
python /home/kilian/RevEngBench/scripts/populate_programbench_tasks.py --force

# 3. Build "false" gold submissions if they don't already exist.
python /home/kilian/RevEngBench/scripts/make_gold_submissions.py

# 4. Run gold validation.
export PROGRAMBENCH_BLOB_DIR=/home/kilian/programbench/src/programbench/data/tasks-blob
cd /home/kilian/programbench
uv run programbench eval ~/gold --force --workers 8
```

Quick four-instance sanity check exercising one fix per case:
```bash
uv run programbench eval ~/gold --force --workers 4 \
  --filter 'sqlite__sqlite|paradigmxyz__solar|agourlay__zip-password-finder|rvben__rumdl'
```
- sqlite: tests fix #1 (file modes)
- solar: tests fix #3 (synthetic .git)
- agourlay: tests fix #4 (full branch tree)
- rumdl: tests fixes #2 (.golden fixtures) and #4 (rootdir marker)
