# tasks-blob cleanup plan

Investigation of `src/programbench/data/tasks-blob/` (55 GB, 200 task dirs) to identify
AI-agent-generated artifacts and prompt leakage that can be removed without breaking
the eval harness.

## What the eval harness actually uses

`eval/run.sh` runs `pytest eval/tests/` and writes `eval/results.xml`. Tests load
`./executable` (the binary) via `eval/tests/conftest.py`. Anything outside of:

- `eval/run.sh`
- `eval/__init__.py`, `eval/conftest.py`, `eval/pytest.ini`, `eval/requirements.txt`,
  `eval/.gitignore`
- `eval/tests/`
- `executable` (and any test resources it reads — `eval/test_resources/`)
- the upstream source files / docs

is candidate for removal.

A spot-check of `grep` across all `eval/tests/**.py` and `eval/conftest.py` shows
the only references to coverage / scaffold artifacts are:

- env-var-only writes (`LLVM_PROFILE_FILE=/tmp/cov.profraw`, `GOCOVERDIR=/tmp/...`)
- comments mentioning `executable_cov` / `measure_coverage.sh`
- one in-test feature test for the program `richgo`'s coverage-output formatting (unrelated)

No test reads `coverage_report.txt`, `coverage_results/`, `.scaffold/`, `*.profraw`,
or `target/` at runtime.

## Headline finding: `.scaffold/` directories

Every task has a `.scaffold/` dir containing the literal trajectory files plus
the agent's scratch state:

```
.scaffold/
├── trajectories/
│   ├── task-1-attempt-1-worker-1.traj.json.gz
│   ├── task-2-attempt-1-worker-2.traj.json.gz
│   └── task-root-attempt-1-lead-run-1.traj.json.gz
├── lead_journal.md       # leaks lead/worker architecture, prompts, coverage strategy
├── log.yaml              # event log with timestamps, agent IDs, role names
├── state.yaml
├── memory/{infra,binary_notes,project_map,quality_script,worker_behavior}.md
└── tasks/{1..N,root}.yaml
```

`lead_journal.md` excerpt (verbatim, from
`agourlay__zip-password-finder.704700d/tests/1fb751a1a8a1/.scaffold/lead_journal.md`):

> Session 1 — Initial setup and dispatch
>
> **Coverage at start:** 0% (no tests)
> **Coverage after smoke tests:** 52.1% (4 smoke tests)
>
> **Infrastructure created:**
> - eval/tests/ with conftest.py (run_binary, test_files_dir, temp_dir fixtures)
> - eval/run.sh (pytest runner with xdist)
> - eval/check_assertions.py (from scaffold base)
> - setup_coverage.sh (Rust coverage build with RUSTFLAGS)
> - measure_coverage.sh (llvm-profdata + llvm-cov + filtered to /workspace/src only)
> - Memory folder initialized with all 5 files

This directly leaks the multi-agent architecture, the coverage methodology, and
the prompt structure.

## Disk-impact summary

Total tasks-blob: 55 GB. Removable:

| Category | Files/Dirs | Size | Risk |
|---|---|---|---|
| `.scaffold/` dirs | 207 | 848 MB | none |
| `*.profraw` | 19,463 | 23 GB | none |
| `target/` (Rust build cache at task root, only under `tests/`) | 195 | 4.6 GB | none |
| `coverage_results/` (detailed_report, raw_summary, summary, reproduce.sh) | 260 | 1.3 GB | none |
| `coverage_report.txt`, `coverage_summary.json`, `coverage_detail.txt`, `lcov.info`, `lcov_full.info`, `coverage.out`, `final_coverage_report.txt`, `FINAL_COVERAGE_REPORT.txt` | ~1100 | ~2.6 GB | none |
| `setup_coverage.sh`, `measure_coverage.sh`, `run_coverage.sh` | ~430 | tiny | none — referenced only inside two `run.sh` files as a *comment* about the GOCOVERDIR env var, not actually invoked |
| AI summary markdown (see regex below) | ~1500 | ~5 MB | none |
| `eval/plan.md` | 294 | small | none — pure AI test-plan log with `(DONE)` markers |
| `eval/check_assertions.py`, `eval/check_harvest.py`, `eval/verify_harvest.py` | ~218 | small | none — `check_assertions.py` literally contains `"The lead copies this to eval/check_assertions.py in session 1"`, never invoked from `run.sh` |
| `*.gcov`, `*.gcda`, `*.gcno`, `*.profdata` | ~2,640 | TBD | none |
| `.gitignore.bak` | 51 | tiny | none |
| `<stdin>`, `con:` (literal accidental filenames) | ~10 | tiny | none |
| **`executable_cov`** | up to 96 | up to 1.3 GB | **Tier 3 — see below** |
| `CLAUDE.md` | 22 | tiny | mixed — some are upstream-legitimate |
| `eval/README.md` | 1401 | small | mixed — usually short and benign, but AI-authored |

Total clearly removable (Tier 1 + Tier 2) ≈ **~32 GB** out of 55 GB.

## Tier 1 — pure scaffold/coverage artifacts, zero runtime references

Run each step as a dry `find … -print | wc -l` first to confirm count, then with
deletion. Commit after each step so regressions bisect cleanly.

```bash
# 1. Scaffold dirs (trajectory files, journals, memory, tasks, log)
find tasks-blob/ -type d -name .scaffold -prune -exec rm -rf {} +

# 2. Rust build cache (under task tests/<hash>/target only — not the upstream src/)
find tasks-blob/ -type d -name target -path '*/tests/*/target' -prune -exec rm -rf {} +

# 3. Coverage_results dirs
find tasks-blob/ -type d -name coverage_results -prune -exec rm -rf {} +

# 4. By basename across the whole tree
find tasks-blob/ -type f \( \
    -name '*.profraw' -o -name '*.profdata' \
    -o -name '*.gcov' -o -name '*.gcda' -o -name '*.gcno' \
    -o -name 'coverage_report.txt' -o -name 'coverage_summary.json' \
    -o -name 'coverage_detail.txt' -o -name 'lcov.info' -o -name 'lcov_full.info' \
    -o -name 'coverage.out' -o -name 'final_coverage_report.txt' \
    -o -name 'FINAL_COVERAGE_REPORT.txt' \
    -o -name 'setup_coverage.sh' -o -name 'measure_coverage.sh' \
    -o -name 'run_coverage.sh' \
    -o -name '.gitignore.bak' \
    -o -name '<stdin>' -o -name 'con:' \
\) -delete
```

## Tier 2 — AI summary markdown / prompt-leak prose

All checked: not referenced from `run.sh`, `conftest.py`, or any `test_*.py`.

Basenames to remove anywhere under `tasks-blob/`:

```
COMPLETION_CHECKLIST.md, COMPLETION_REPORT.md, COMPLETION_STATUS.md,
COMPLETION_SUMMARY.md, COMPLETION_VERIFICATION.md,
TEST_SUMMARY.md, TEST_SUITE_SUMMARY.md, TESTING_SUMMARY.md, TESTS_SUMMARY.md,
TEST_COMPLETION_SUMMARY.md, TEST_COVERAGE.md, TEST_COVERAGE_SUMMARY.md,
TEST_DOCUMENTATION.md, TESTING_APPROACH.md,
SUBMISSION_SUMMARY.md, HARVEST_SUMMARY.md,
FINAL_SUMMARY.md, FINAL_SUMMARY.txt, FINAL_REPORT.md, FINAL_VERIFICATION.md,
FINAL_STATUS.md, FINAL_STATUS.txt, FINAL_CHECKLIST.md,
FINAL_SUBMISSION.md, FINAL_SUBMISSION_SUMMARY.md, FINAL_SUBMISSION_REPORT.md,
FINAL_COMPLETION_REPORT.md,
FINAL_COVERAGE_REPORT.md, FINAL_COVERAGE_SUMMARY.md, FINAL_COVERAGE_ANALYSIS.md,
FUNCTIONALITY_LIST.md, FUNCTIONALITY_CHECKLIST.md, FUNCTIONALITY_COVERAGE.md,
COVERAGE_ANALYSIS.md, COVERAGE_SUMMARY.md, COVERAGE_REPORT.md,
COVERAGE_NOTE.md, COVERAGE_NOTES.md, COVERAGE_LIMITATION.md, COVERAGE_STATUS.txt,
VERIFICATION.md, VERIFICATION.txt, VERIFICATION.sh,
STATUS.md, WORK_SUMMARY.md, IMPLEMENTATION_NOTES.md,
INVESTIGATION_SUMMARY.md, WEAK_PATTERNS_QUICK_REF.md, weak_tests_list.md,
README_TESTS.md, QUICK_START.md,
ASSERTION_NOTES.md
```

Plus, anywhere under `eval/`:

```
plan.md
check_assertions.py
check_harvest.py
verify_harvest.py
```

Suggested `find` (run `-print` first, eyeball, then `-delete`):

```bash
find tasks-blob/ -type f -regextype posix-extended -regex \
    '.*/(COMPLETION|FINAL|TESTING|TESTS?|TEST_SUITE|SUBMISSION|HARVEST|FUNCTIONALITY|COVERAGE|VERIFICATION|WORK|INVESTIGATION|STATUS|IMPLEMENTATION)_?(CHECKLIST|REPORT|SUMMARY|STATUS|ANALYSIS|NOTES?|VERIFICATION|LIMITATION|APPROACH|DOCUMENTATION|COVERAGE|SUBMISSION|LIST|COMPLETION)?\.(md|txt|sh)' \
    -print
```

(Iterate the regex until the print list looks correct, then `-delete`.)

## Tier 3 — needs per-task logic, not a basename rule

### `executable_cov` (1.3 GB, 96 files)

`eval/conftest.py` invokes `./executable`. In **122 task dirs** that `executable` is a
symlink → `executable_cov`. **26 of those symlinks are already broken** (the
`executable_cov` target is missing — those task dirs already fail).

Before deleting `executable_cov`, classify each task's
`tests/<hash>/executable` pair:

```python
# pseudocode
for task_dir in glob("tasks-blob/*/tests/*"):
    exe = task_dir / "executable"
    cov = task_dir / "executable_cov"
    if not exe.exists() and not exe.is_symlink():
        continue  # already broken / missing — separate decision
    if exe.is_symlink() and exe.readlink().name == "executable_cov":
        if cov.exists():
            # option A: replace symlink with copy, then delete cov
            data = cov.read_bytes()
            exe.unlink(); exe.write_bytes(data); exe.chmod(0o755)
            cov.unlink()
        else:
            # already broken — leave for separate triage
            pass
    elif exe.is_file() and cov.exists():
        cov.unlink()
```

Run a sample eval after this script before applying broadly.

### `CLAUDE.md` (22)

Some are upstream-legitimate (`cheat__cheat`, `cweill__gotests`, etc.). Don't blanket-
delete; for each, diff against the upstream commit referenced in the task dir name
(e.g. `cheat__cheat.b8098dc` → `b8098dc`) and remove only files that didn't exist
upstream.

### `eval/README.md` (1401)

Short and mostly benign ("run `./eval/run.sh`"), but AI-authored. Keep unless aiming
for maximum aggression.

## Recommended workflow

1. Run each Tier-1 `find` with `-print | wc -l` first, then add `-delete` /
   `-prune -exec rm -rf {} +`.
2. Commit after each tier.
3. After each tier, sample 5 tasks at random and run their `eval/run.sh`:
   ```bash
   for d in $(find tasks-blob/ -path '*/eval/run.sh' | shuf -n 5); do
       echo "=== $d ==="; bash "$d" || echo "FAILED"; done
   ```
4. Tier 3 only after Tiers 1+2 land clean.

## Verification commands used

```bash
grep -rln "executable_cov\|coverage_report\|coverage_summary\|coverage_detail\
\|setup_coverage\|measure_coverage\|TEST_SUMMARY\|COMPLETION_CHECKLIST\
\|FUNCTIONALITY_LIST\|HARVEST_SUMMARY\|FINAL_SUMMARY\|FINAL_REPORT\
\|FINAL_VERIFICATION\|COMPLETION_REPORT\|TESTING_SUMMARY\|TEST_SUITE_SUMMARY\
\|SUBMISSION_SUMMARY\|COVERAGE_ANALYSIS\|profraw\|profdata\|coverage_results\
\|\.scaffold" --include="conftest.py" --include="run.sh" --include="test_*.py"
# All hits were comments or env-var writes to /tmp; none read these files at
# runtime from the repo.
```
