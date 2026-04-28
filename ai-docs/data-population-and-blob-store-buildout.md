# Data population and blob-store buildout

Date: 2026-04-26 → 2026-04-28
Repos touched: `RevEngBench`, `programbench`

## Initial prompt

> "i recentely created @programbench/ from @RevEngBench/ following
> @programbench/ai-docs/plans/initial_eval_port.md. I also dumped all github
> branches into @RevEngBench/output/kilian/github-dump. Now please create a
> script in @RevEngBench/scripts/ which populates
> @programbench/src/programbench/data/tasks/ with the tests. This script should
> go through all tests.json that are tracked in @RevEngBench/ and copy over
> all non-ignored branches. It should also populate the tests.json files in
> @programbench/src/programbench/data/tasks/"

(The actual github-dump path turned out to be `RevEngBench/output/github-dump/`,
not `output/kilian/github-dump/` — minor slip-of-tongue, resolved on first
exploration.)

## What this work covers

Built a one-shot porting pipeline to move test data from RevEngBench into the
new programbench layout, then iteratively shrank and split the data to fit the
HF-blob-store design from `ai-docs/plans/blob_store.md`. Along the way:
removed gold-mode from programbench, replaced it with a "false gold submission"
workflow, and made several config knobs env-overridable.

Two scripts were added to `RevEngBench/scripts/`; nothing in programbench's
`src/` was rewritten by us — only adapted (gold-mode removal, env-var
overrides, timeout bumps, image-name unification).

## Source-of-truth context (uncovered during the work)

### RevEngBench layout
- `RevEngBench/tasks/<iid>/task.yaml` — per-instance metadata.
  Fields: `commit`, `repository`, `language`, `difficulty`, `eval_clean_hashes`,
  plus `executable_test` (RevEngBench-only — dropped during port).
- `RevEngBench/tasks/<iid>/tests.json` — `{"branches": {<branch_name>: {"ignored", "ignore_reason", "tests", "ignored_tests"}}}`.
- `RevEngBench/output/github-dump/<iid>/<branch_name>.zip` — `git archive
  --format=zip` of every branch (305 instance dirs, ~100 zips per instance).
  Producer: `RevEngBench/reveng/cli/run/download_all.py`. Branch name maps 1:1
  to zip filename, no escaping needed.
- 200 of 305 dump dirs have a corresponding `tests.json` in `tasks/`.
- The CLAUDE.md rule "always use `load_all_instances()`" applies — both scripts
  do, importing from `reveng.utils.load_data`.

### programbench layout (post-port, after this work)
- `src/programbench/data/tasks/<iid>/` — **metadata only** in the package:
  `task.yaml` (5 allow-listed fields) + `tests.json` (per-branch shape
  identical to RevEngBench, but keys are 12-char SHA-256 hashes of the
  original branch name).
- `src/programbench/data/tasks-blob/<iid>/tests/<branch_hash>/eval/...` —
  full test branch contents. Gitignored. Overlaid at eval time via
  `PROGRAMBENCH_BLOB_DIR` (or pulled from HF when `HF_REVISION` is set).
- `src/programbench/utils/blob_store.py` — `get_blob_dir(instance_id)` returns
  `Path(BLOB_LOCAL_DIR) / instance_id` if the env var is set, else
  `snapshot_download(HF_REPO_ID, revision=HF_REVISION, allow_patterns=...)`.
- Eval overlay: `eval/eval.py:_run_test_branch()` does
  `blob_branch_dir = self.blob_dir / "tests" / branch` then
  `self.env.copy_in(test_dir, "/workspace/")`.

### Branch-hash convention
`hashlib.sha256(branch_name.encode()).hexdigest()[:12]`. 12 chars matches the
reference `testorg__calculator.abc1234` example. Both scripts use this.

## Scripts produced

### `RevEngBench/scripts/populate_programbench_tasks.py`

Walks `RevEngBench/tasks/`. For each instance, writes
`programbench/.../tasks/<iid>/{task.yaml,tests.json}` (metadata only) and
extracts every non-ignored branch zip into
`programbench/.../tasks-blob/<iid>/tests/<branch_hash>/eval/...`.

Filter rules (see `_keep_member`):
- Drop `__pycache__/`, `*.pyc`, `*.pyo` — bytecode regenerated on import.
- Drop `eval/results.xml` — stale junit; `eval/run.sh` rewrites it.
- Drop `eval/*.md` and `eval/*.profraw` **at eval/ root only** — those are
  LM-generated docs / LLVM coverage dumps from test-gen. Subdirectory `.md`
  and `.profraw` are real fixtures and must be kept.
- Branch is dropped (with WARNING) if its zip is missing, has no `eval/` dir,
  or has no `eval/run.sh` after extraction.

User-specified policy decisions:
- Source: only `RevEngBench/tasks/` (200 instances), not `train/`.
- Existing target: skip if `meta_target` and `blob_target` both already exist;
  `--force` wipes both and rewrites; `--force-metadata` rewrites
  `task.yaml`/`tests.json` without re-extracting blobs (added by the user
  after the conversation in this doc concluded).
- Missing zip: warn + drop the branch from the populated `tests.json`,
  continue with the rest of the instance.
- `task.yaml` is rewritten with the programbench schema only
  (`repository`, `commit`, `language`, `difficulty`, `eval_clean_hashes` — drop
  `executable_test` and any other extra fields).
- `--no-clean` flag (added by the user later): bypass the bytecode/artifact
  filter and extract zips verbatim.

Final numbers (on the run captured at end of this conversation):
- 196 instances populated, 4 skipped.
- 1772 branches written, 1117 dropped (the dropped branches almost all have
  no zip in the github-dump).
- Metadata: ~55 MB. Blob: ~1.1 GB.

Size-shrinking history (each row is the full populated tree, all 196 instances):

| Step | Result |
|---|---|
| Initial: extract whole zip per branch | 136 GB |
| Keep only `eval/` from each zip | 18 GB |
| Drop `*.profraw` (LLVM coverage, ~17 GB across 16,755 files) | 1.4 GB |
| Drop `*.golden`, LM-generated `*.md`, `results.xml` | 1.1 GB |
| (Various split-by-extension experiments; reverted) | — |
| Final layout: metadata-in-package + tests-in-blob | 55 MB + 1.1 GB |

Side observations (not acted on):
- `.py` files are 250 MB / 27,158 files / 6.67M lines / **only 10k unique by
  content** → ~2.7× duplication across iterative test-gen branches.
- `xargs wc -l` was reporting only the last chunk's total (chunked into 17
  groups for 27k files). Use `find ... -exec wc -l {} +` or sum every "total"
  line.

### `RevEngBench/scripts/make_gold_submissions.py`

For each instance in `RevEngBench/tasks/`, takes
`output/github-dump/<iid>/build.zip` and emits `~/gold/<iid>/submission.zip`
with `build.sh` renamed to `compile.sh` (executable bit preserved via
`ZipInfo.external_attr`). Lets `programbench eval ~/gold` validate the gold
solution through the regular submission path — no GitHub clone, no network.

Final run: 199 written, 1 skipped (cmatrix from a test run), 0 missing. 200
dirs in `~/gold/`, ~1.0 GB.

## Programbench changes made during this work (committed)

### `Enh: Allow to override docker org` (`d5a9084`)
- `constants.py`: `DOCKER_ORG` reads `PROGRAMBENCH_DOCKER_ORG` (default
  `"programbench"`).
- `utils/load_data.py`: was hardcoding `f"programbench/{instance_id…}"` for
  `image_name`. Now goes through `image_name_from_instance_id()` so the
  env-var override is honored.
- `constants.py`: `HF_REVISION` reads `PROGRAMBENCH_HF_REVISION` (default
  `""`).

### `Remove gold mode from eval pipeline` (`184dbaf`)
- `constants.py`: dropped `BUILD_SH`, `DEFAULT_GOLD_EVAL_DIR`.
- `eval/eval.py`: removed gold branch in `_compile_executable()`; dropped
  `task_dir`, `repository`, `commit` ctor params; renamed in-container stash
  path from `/opt/programbench-gold-executable-do-not-modify` to
  `/opt/programbench-stashed-executable-do-not-modify`; updated docstrings.
- `eval/eval_batch.py`: removed `is_gold_mode` plumbing; `run_eval_batch` is
  now run-dir-only.
- `cli/main.py`: removed gold from arg help, docstring, and examples.
- `ai-docs/plans/initial_eval_port.md`: added a "post-port update" banner at
  the top noting gold mode is gone and pointing to
  `make_gold_submissions.py` as the replacement workflow.

### `Fix: bump docker run/cp timeouts to avoid spurious failures under parallelism` (`870e1f9`)
- `container.py`: `docker run -d` timeout 60s → 300s; `docker cp` 120s → 300s.
- Both env-overridable: `PROGRAMBENCH_DOCKER_RUN_TIMEOUT`,
  `PROGRAMBENCH_DOCKER_CP_TIMEOUT` (defaults `300`).
- Triggered by repeated `subprocess.TimeoutExpired` on `docker run -d` under
  `--workers 4` when daemons race to pull/start large images.

## RevEngBench commits

- `populate-programbench-tasks: split metadata from blobs, drop pycache`
  (`73bd2caf`)
- `make-gold-submissions: build gold submissions from build.zip branches`
  (`416cafa5`)

(The user later landed additional changes to
`populate_programbench_tasks.py` — adding `--force-metadata`, `--no-clean`,
and refining the `_keep_member` rule to only drop `.md`/`.profraw` at eval/
root rather than recursively. That work is post-this-conversation and not
captured in the commit list here.)

## Standard run commands

Populate (after a fresh `output/github-dump/`):
```bash
cd /home/kilian/RevEngBench
uv run python scripts/populate_programbench_tasks.py
```

Build gold submissions:
```bash
uv run python scripts/make_gold_submissions.py
```

Run gold eval through the submission path:
```bash
export PROGRAMBENCH_BLOB_DIR=/home/kilian/programbench/src/programbench/data/tasks-blob
export PROGRAMBENCH_DOCKER_ORG=reveng
cd /home/kilian/programbench
uv run programbench eval ~/gold --workers 4
```

## Known issues surfaced (not all resolved)

- **JUnit/tests.json drift**: warnings of the form "N test(s) in JUnit XML
  not in tests.json" + `results_read_failed` on some branches. `tests.json`
  is a snapshot; the test files in the blob may collect more pytest cases
  than the metadata records. Two fixes considered (neither implemented yet):
  re-collect `tests.json` from the actual test files, or relax the equality
  check to "subset-of-expected".
- **Hash-collision guard**: `populate_instance` raises `RuntimeError` if two
  branches in the same instance hash to the same 12-char prefix. Never
  triggered in practice but worth bumping `HASH_LEN` if it does.
- **`profraw` in deeper dirs**: the latest user-edited `_keep_member` only
  drops `.profraw` at `eval/` root. Some test runs may produce `.profraw` in
  subdirs that this no longer catches; previously they were nuked
  recursively.
