# Gold Eval Error Summary & tests.json Reconciliation

Run reviewed: `output/kilian/gold/` (190 instances, modified 2026-04-25). Reconciliation performed 2026-04-26 / 2026-04-27.

## 0. Initial prompt and surrounding context

**Initial user request:** *"I want you to look at the gold results and `@docs/skills/summarize_eval_errors.md`"* — i.e. apply the eval-error summary skill to the gold-eval output directory.

### What RevEngBench is (from `CLAUDE.md`, `.cursor/rules/`, and the skill doc)

- A benchmark that evaluates whether LM-based SWE agents can reverse-engineer black-box CLI tools (mostly Rust/Go).
- Pipeline: take an open-source CLI tool → compile into a Docker image with source removed → an LM agent runs against the `:task` image and re-implements the software → behavioral tests (also LM-generated) score the re-implementation: `score = passed / total`.
- Each instance can have multiple **test branches** stored in `tests.json`; during eval, tests from all active (non-ignored) branches run and are concatenated.
- Eval runs in two phases inside the `:task` container: a **global phase** (compile) and a **per-branch phase** (run tests). Failures in either get an `error_code`.
- Results land at `<output_dir>/<instance_id>/<instance_id>.eval.json`.
- Repo conventions in `CLAUDE.md`: always load instance data via `load_all_instances()` from `reveng/utils/load_data.py`; use `save_tests()` to write back; never parse `task.yaml`/`tests.json` directly.

### What the gold-results landscape looks like

`output/kilian/` contains many run directories. The "gold" runs (gold-patch evaluations of the canonical solution) are split across several variants:

| Run | Instances | Modified | Status |
|---|---|---|---|
| `gold` | 191 | 2026-04-25 23:17 | **Canonical / most recent** |
| `gold_6` | 201 | 2026-04-25 13:46 | Recent alternative |
| `gold_1`, `gold_2` | ~200 each | 2026-04-24 | Earlier iterations |
| `gold_4`, `gold_5` | ~200 each | 2026-04-19/21 | Older |
| `gold_cloud_3` | 200 | 2026-04-02 | Oldest (cloud variant) |

Schema of each `<id>.eval.json` matches the skill doc exactly: `error_code`, `error_details`, `test_branch_errors`, `test_results`, `warnings`, `log`, `solution_branch`, `test_branches`, `executable_hash`. **Decision:** summarize `gold` (190 instance dirs).

### Skill instructions followed

`docs/skills/summarize_eval_errors.md` prescribes categorizing issues as: (1) global errors, (2) branch errors, (3) test-level anomalies (`system_error`, `not_run`), (4) cheat detection, (5) patterns to flag (e.g. many instances with same code → systemic infra issue). The summary below uses exactly these categories.

### Bigger picture this fed into

The user then asked to **make sure all tests are registered** (i.e. reconcile `tests.json` against what gold actually emitted in JUnit XML) and to **validate the failed-fetch branch**. That triggered:

1. Running `revenge instances add-test-info` (and re-running with `--force`).
2. Investigating discrepancies between `tests.json` and JUnit output.
3. Validating mirror branches for `codesnap-rs` against `git ls-remote`.
4. Restoring `chamber` iter3_judge to avoid a regression masquerading as drift.

The remainder of this doc is the technical record of those findings and actions.

## 1. Eval-error summary (per `docs/skills/summarize_eval_errors.md`)

### Headline

- **156 / 190 instances (82%)** fully clean — no errors, no warnings.
- **1 global error** (entire instance failed)
- **20 branch errors** across **9 instances** (3 distinct codes)
- **40 warnings** across **29 instances** (one category, all benign)
- **9 `system_error` test results** across 6 instances
- **0 cheat / annotation flags** triggered

Test status totals: 533,746 passed · 26,710 failure · 4,145 error · 9,947 skipped · 9,513 not_run · 9 system_error (out of 584,070 total).

### 1a. Global errors (1 instance)

| code | count | instance |
|---|---|---|
| `fetch_failed` | 1 | `codesnap-rs__codesnap.f81e4f3` |

Empty `error_details` — turned out to be a 20s `git fetch` timeout, not a missing branch (see §3 below).

### 1b. Branch errors (20 across 9 instances)

| code | count | notes |
|---|---|---|
| `checkout_tests_failed` | 12 | 10 of 12 from `htop-dev__htop.523600b` — all "No such container: 9fafae1ac712…", i.e. the eval container vanished mid-run (infra blip). 1 each on `cordx56__rustowl` and `segmentio__chamber` from leftover build artifacts confusing `git checkout`. |
| `results_read_failed` | 6 | 6 distinct instances: `duckdb`, `gromacs`, `bore`, `ffmpeg`, `htop`, +1. `duckdb` and `gromacs` show `cat: eval/results.xml: No such file or directory`; others empty `error_details`. Likely pytest timeouts on heavy suites. |
| `no_expected_test_list` | 2 | Both on `codesnap-rs__codesnap.f81e4f3` — downstream of the global fetch failure. |

### 1c. Warnings — all 40 are "*N test(s) in JUnit XML not in tests.json*"

Top offenders (`tests.json` out of sync with the actual test suite):

- `sqlite__sqlite.839433d` — 16,525 unexpected
- `duckdb__duckdb.bdb65ec` — 6,238 + 1,670 + 220 + 220 + 195 + 195 (6 branches)
- `jgm__pandoc.5caad90` — 5,213 + 254 + 254
- `danmar__cppcheck.0a5b103` — 1,816
- `tinycc__tinycc.9b8765d` — 1,249
- 24 more instances with smaller mismatches.

Concentrated heavily on **scaffold-testgen** branches (the autogenerated ones); iter1_gen/iter1_judge pairs often duplicate the same warning.

### 1d. Test-level anomalies

`system_error` (6 instances, 9 tests): `segmentio__chamber.5f93f5f` (4), `antonmedv__fx`, `duckdb`, `mkj__dropbear`, `unhappychoice__gittype`, `xampprocky__tokei` (1 each). Chamber's 4 stand out.

`not_run` without a global error (8 instances) — driven by branch errors above plus tests missing from JUnit XML.

### 1e. Cheat detection

0 instances with `annotation_tags` set. Annotation may simply not have been run on this `gold` directory yet — `annotation_tags` is `None` for everything.

### 1f. Patterns to flag

1. `htop-dev__htop.523600b` is the largest single source of branch errors (10 of 20) — Docker-container-disappeared infra blip; re-run.
2. `results_read_failed` repeats across 6 heavy-suite instances; consider raising the test timeout.
3. Warnings concentrate on scaffold-testgen + iter1 coverage branches — `tests.json` drift from the actual suite.
4. `segmentio__chamber.5f93f5f` is anomalous — 4 `system_error` + 42 `not_run`.
5. No systemic global failures: only 1 `fetch_failed`, no `compile_failed`. Mirror & build pipeline look healthy.

## 2. Registering all tests via `revenge instances add-test-info`

Ran the canonical tool to populate `tests.json` from gold JUnit output:

```bash
revenge instances add-test-info output/kilian/gold              # initial pass
revenge instances add-test-info output/kilian/gold --force      # overwrite drift
```

- First pass updated **23 instances** cleanly.
- Many branches were skipped with "tests changed, skipping (use --force to overwrite)".
- `--force` updated the remaining instances → **61 modified total**, **77 `tasks/*/tests.json` files** changed in the working tree.

### Why `--force` was needed — three flavors of discrepancy

1. **`tests: []` (empty) → populated for the first time.** The biggest cohort. Most scaffold-testgen and iter1 coverage branches had no test list registered. Examples (deltas from `before=0`):
   - `sqlite__sqlite` / `scaffold-testgen-20260416_172124`: +16,525
   - `duckdb__duckdb` / `scaffold-testgen-20260416_074938`: +6,238
   - `duckdb__duckdb` / 4 coverage branches: +220, +220, +195, +195

   These match the eval warnings 1:1.

2. **Test count grew slightly.** Tests added since `tests.json` was last populated — e.g. `chamber` iter5_gen / iter5_judge: 987 → 1010 (+23 each). Legitimate.

3. **Test count shrank — drift / regression.** Most notable: `chamber` iter3_judge dropped 586 → 176 tests. Suspicious; may correlate with the 4 `system_error` results on chamber.

### Restore action

Per request, restored `chamber` iter3_judge `tests` field from HEAD so we are only ever *adding* tests, never losing them:

```python
# tasks/segmentio__chamber.5f93f5f/tests.json
# branch: coverage-test-jeff-coverage_all_70pct__claude-4-5-sonnet-genai_iter3_judge-20260321_063041
# before=586 after=176 → restored to 586
```

After restore, the only remaining `chamber` deltas are the legitimate +23 additions on iter5_gen and iter5_judge.

## 3. `codesnap-rs` `fetch_failed` — root cause

Instance: `codesnap-rs__codesnap.f81e4f3`. Validated against the mirror with:

```bash
git ls-remote git@github.com:gnever-reveng/codesnap-rs__codesnap.f81e4f3.git
```

- Mirror has 146 branches; **all 21 configured test branches exist on the remote.**
- The eval log shows `fetch` step failed with: `Command '[...git fetch mirror build <19 refs>...]' timed out after 20 seconds`. `wall_time` ≈ 20.0s.
- **Conclusion: not a missing-branch problem** — transient network / slow-mirror timeout. Just needs a re-eval.

## 4. Suggested next steps

- Re-eval `codesnap-rs__codesnap.f81e4f3` (transient fetch timeout).
- Re-eval `htop-dev__htop.523600b` (10 branch errors — container-disappeared infra blip).
- Investigate `segmentio__chamber.5f93f5f` iter3_judge — 410-test shrink + 4 `system_error` results suggest a real regression on that branch (now masked by the HEAD-restore).
- Review remaining 76 `tasks/*/tests.json` diffs before committing — most are legitimate fills, but any other shrinks should be sanity-checked the same way.
- Consider raising the per-instance test-suite timeout for heavy suites (duckdb, ffmpeg, gromacs, bore) to address `results_read_failed`.
- Confirm whether annotation has been run on this gold directory (`annotation_tags` is null everywhere).

## 5. Tool reference

- Skill applied: `docs/skills/summarize_eval_errors.md`
- Canonical reconciliation tool: `revenge instances add-test-info [GOLD_DIR] [--force]` — populates the `tests` field per branch in `tasks/*/tests.json` from gold JUnit output. Always re-run with `--force` after a fresh gold run if drift is expected.
- Mirror branch validation: `git ls-remote git@github.com:gnever-reveng/<instance_id>.git`. Mirror naming: `gnever-reveng/<instance_id>` (per `reveng/constants.py:GH_ORG`).
