# Porting Evaluation to programbench

## Goal

Port the core evaluation pipeline to programbench, replacing all GitHub branch/mirror-based I/O with local files and direct repo checkout. The two codebases will be maintained independently — kept similar by convention, but this is a rewrite, not a shared library.

Entry point:

```bash
programbench eval <run_directory>       # evaluate submissions
programbench eval gold                  # evaluate gold solutions
```

---

## Directory Layouts

### Run directory (input to `programbench eval`)

```
<run_directory>/
  <instance_id>/
    submission.zip            # full working tree: source + compile.sh at root
    <instance_id>.eval.json   # written by eval (output)
  <instance_id>/
    submission.zip
    <instance_id>.eval.json
  ...
```

`submission.zip` contains the agent's reimplemented source code with a `compile.sh` at the root. When unzipped into the Docker workspace, the structure matches what the source project gets after checking out a solution branch. Running `compile.sh` produces `./executable`.

### Task data (shipped with the package)

```
src/programbench/data/tasks/<instance_id>/
  task.yaml                            # task metadata
  tests.json                           # test metadata (keyed by branch hash)
  tests/<branch_hash>/                 # local copy of test branch content
    eval/
      run.sh                           # test runner script
      tests/                           # pytest test files
      ...
```

`<branch_hash>` is a deterministic hash of the original branch name (e.g. SHA-256 of the branch name string, truncated). The original branch name is not stored — only the hash is used as identifier. `tests.json` uses these hashes as keys (same structure as the source project, but with hashes replacing branch names).

### task.yaml format

```yaml
repository: owner/repo          # GitHub repo (used for gold checkout)
commit: <full_sha>              # commit hash (used for gold checkout)
language: rust                  # optional metadata
difficulty: easy                # optional metadata
eval_clean_hashes:              # optional: SHA-256 hashes of files to remove (anti-cheat)
  - <hash>
```

### tests.json format

Same structure as the source project, but keys are branch hashes instead of branch names:

```json
{
  "branches": {
    "<branch_hash>": {
      "ignored": false,
      "ignore_reason": "",
      "tests": ["tests.test_module.test_foo", "tests.test_module.test_bar"],
      "ignored_tests": [
        {"name": "tests.test_module.test_baz", "reasons": [{"id": "gold_fail"}]}
      ]
    }
  }
}
```

---

## Architecture

### What stays the same (port verbatim or near-verbatim)

- **Data models**: `TestResult`, `TestBranchError`, `EvaluationResult` (Pydantic, from `eval.py`)
- **Batch models**: `InstanceEvalSummary`, `BatchEvalSummary` (from `eval_batch.py`)
- **JUnit XML parsing**: `parse_test_results()`, `_process_branch_xml()`
- **Exception classes**: `EvalStepError`, `EmptyTestResultError`, `XmlParseError`
- **Evaluator pipeline shape**: compile → stash executable → per-branch (wipe workspace, inject tests, restore exe, run tests, parse XML) → aggregate results
- **Evaluator internals**: `_run_step()`, `_remove_hashed_files()`, `_restore_executable()`, `_inject_not_run()`, `_add_branch_error()`, `run()`
- **`from_existing` reprocessing**: replay stored JUnit XML from log without Docker
- **Executable stashing** at `/opt/programbench-gold-executable-do-not-modify`, SHA-256 verification
- **Process reaping** between test branches (`pkill`)
- **Incremental evaluation**: `get_branches_to_eval()` — skip already-evaluated branches, merge results
- **Rich summary table**: `BatchEvalSummary.summary()`
- **"Branch" terminology** in all models and interfaces
- **Batch eval features**: parallel workers, force, filter/slice, summarize-only, multiple run dirs, gold mode

### What changes

| Aspect | Source project | programbench |
|--------|---------------|-------------|
| **Solution injection** | `git fetch` + `git checkout` inside container | `docker cp` unzipped submission into container, then `compile.sh` |
| **Test injection** | `git checkout mirror/<branch>` | Wipe workspace + `docker cp` local test dir into container |
| **Gold mode** | Checks out `build` branch from mirror, runs `build.sh` | Clones original GitHub repo at commit (from `task.yaml`), runs `build.sh` inside container |
| **Container management** | `minisweagent.environments.DockerEnvironment` | Own `ContainerEnvironment` class (Docker/Podman, future Singularity) |
| **Mirror / GitHub token** | Required (`git_clone_url()` with token) | Not needed (no mirrors). Gold mode uses public repo URL |
| **Task loading** | `RevEngTask` class with Docker/GitHub methods | Lightweight loader: reads `task.yaml`, derives `image_name` |
| **Instance data path** | `<repo_root>/tasks/<instance_id>/` | `src/programbench/data/tasks/<instance_id>/` (in-package) |
| **Branch identifiers** | Git branch names | SHA-256 hashes of original branch names |
| **Run discovery** | Glob `.traj.json` → extract `submission_branch` | Glob `submission.zip` files |
| **Cheat detection** | `.annotate.json` sidecar files | Deferred (not in initial port, but keep the hook points) |
| **Internet control** | `iptables` via `nsenter` | Deferred (not in initial port) |

### What's dropped entirely (initial port)

- Cheat detection / annotation tags — no `_get_annotation_tags()`, no `_get_suspicious_pct()`, no `FAIL_ANNOTATION_TAGS`. Remove from `InstanceEvalSummary` fields: `annotation_tags`, `cheat_detection_missing`, `suspicious_pct`, `formatted_tags`, `original_score`. Remove cheat-related summary table logic.
- Internet control (`turn_off_internet` / `turn_on_internet`)
- Trajectory file loading (`.traj.json`, `ijson`, `zstandard`)
- Info cache sidecar (`.info_cache.json`) and all fast-loading cache machinery (`_build_cache_dict`, `_source_max_mtime`, `_delete_legacy_caches`, `load_instance_info`, `load_instance_infos`)
- ECR push/pull
- Cloud eval (AWS Batch)
- `_migrate_old_format` validator on `EvaluationResult` (no legacy data in programbench)

---

## Container Environment Class

Build our own, modeled closely on minisweagent's `DockerEnvironment`. Must support Docker and Podman now, Singularity in the future.

### Interface

```python
class ContainerEnvironment:
    """Manage a long-running container for command execution and file injection."""

    def __init__(self, *, image: str, cwd: str = "/", executable: str = "docker",
                 timeout: int = 30, run_args: list[str] | None = None): ...

    def execute(self, command: str, *, timeout: int | None = None) -> dict[str, Any]:
        """Run a shell command. Returns {"output": str, "returncode": int, "exception_info": str}."""

    def copy_in(self, local_path: Path, container_path: str) -> None:
        """Copy a local file or directory into the container via `docker cp`."""

    def cleanup(self) -> None:
        """Stop and remove the container."""
```

### Implementation notes

- **Lifecycle**: `__init__` starts a detached container (`docker run -d --init --name <name> -w <cwd> <run_args> <image> sleep 2h`). `--init` for PID 1 zombie reaping. `--rm` is NOT in default `run_args` because we need the container to persist for `docker cp`.
- **`execute()`**: `docker exec -w <cwd> <container_id> bash -lc "<command>"`. Timeout via `subprocess.run(timeout=...)`. On timeout, return `returncode: -1` with exception info (same as minisweagent). Stdout and stderr merged.
- **`copy_in()`**: `docker cp <local_path>/. <container_id>:<container_path>/` for directories, `docker cp <local_path> <container_id>:<container_path>` for files. The trailing `/.` is important for directory contents without creating a nested directory.
- **`cleanup()`**: Fire-and-forget `docker stop` then `docker rm -f`, same pattern as minisweagent. Also called from `__del__`.
- **Podman**: Works identically — just change `executable` to `"podman"`. Same CLI interface.
- **No `_check_finished` / `Submitted` exception** — that's agent-specific, not needed for eval.
- **No `forward_env` / `env`** — not needed for eval (git config is done via `execute()`).

### Differences from minisweagent's DockerEnvironment

| minisweagent | programbench |
|---|---|
| `execute(action: dict, ...)` where `action = {"command": "..."}` | `execute(command: str, ...)` — simpler, no dict wrapping |
| No file copy support | `copy_in(local_path, container_path)` |
| `run_args` default `["--rm"]` | `run_args` default `["--cpus", "10"]` (no `--rm`; container persists for `docker cp`) |
| `_check_finished()` raises `Submitted` | Not present |
| Pydantic config model | Direct constructor args |
| `serialize()`, `get_template_vars()` | Not needed |

---

## Evaluator Changes in Detail

### `_compile_executable()` — submission mode (not gold)

**Source project** (what it does now):
```
1. git remote add mirror <url>
2. git fetch mirror <solution_branch> <test_branches...>
3. git checkout -f mirror/<solution_branch>
4. remove hashed files (anti-cheat)
5. chmod +x compile.sh && ./compile.sh
6. cp executable → stash
7. sha256sum stash → record hash
```

**programbench** (what it will do):
```
1. Wipe workspace: rm -rf /workspace/* /workspace/.[!.]*
2. docker cp <unzipped_submission>/. <container>:/workspace/
3. remove hashed files (anti-cheat)
4. chmod +x compile.sh && ./compile.sh
5. cp executable → stash
6. sha256sum stash → record hash
```

Steps 3-6 are identical. The change is steps 1-2 replacing git operations with `docker cp`.

The submission is unzipped on the host into a temp directory, then copied in via `copy_in()`.

### `_compile_executable()` — gold mode

**Source project**: checks out `build` branch from mirror, runs `build.sh`.

**programbench**: clones the original GitHub repo at the commit specified in `task.yaml`, runs `build.sh` from the task data directory.

```
1. Wipe workspace
2. git clone <repository_url> . (inside container, using commit from task.yaml)
3. git checkout <commit>
4. docker cp <task_dir>/build.sh <container>:/workspace/build.sh
5. chmod +x build.sh && ./build.sh
6. cp executable → stash
7. sha256sum stash → record hash
```

Wait — this requires the container to have network access and git. The `:task` images already have git (they use it during the source project's eval). The original repo is public (it's an open-source project), so no token needed.

**Question for implementation**: Does `build.sh` live in the task data directory (`data/tasks/<instance_id>/build.sh`)? In the source project, it lives in the task directory alongside `task.yaml`. Let's keep that — `data/tasks/<instance_id>/build.sh` is present for instances that support gold mode.

### `_run_test_branch()` — for each test branch

**Source project**:
```
1. pkill stray processes
2. rm -f .git/index.lock && git clean -fdx && git checkout -f mirror/<branch>
3. restore executable from stash
4. rm -f eval/results.xml
5. chmod +x eval/run.sh && ./eval/run.sh
6. cat eval/results.xml → return raw XML
```

**programbench**:
```
1. pkill stray processes
2. rm -rf /workspace/* /workspace/.[!.]*    (full wipe)
3. docker cp <task_data>/tests/<branch_hash>/. <container>:/workspace/
4. restore executable from stash
5. rm -f eval/results.xml
6. chmod +x eval/run.sh && ./eval/run.sh
7. cat eval/results.xml → return raw XML
```

Steps 1, 4-7 are identical. Step 2-3 replaces git checkout with wipe + copy.

---

## Batch Evaluation

### `run_eval_batch()` — top-level orchestrator

Handles both gold mode and run-directory mode. Called by the CLI.

```
1. Load all instances from data/tasks/
2. Apply filters (--filter regex, --slice)
3. For each source:
   - If "gold": collect all instances with active test branches
   - If path: glob <path>/<instance_id>/submission.zip, match against known instances
4. If --summarize-only: read existing .eval.json files, print summary, done
5. Otherwise: ThreadPoolExecutor with --workers, run _evaluate_instance() for each
6. Print Rich summary table per source
```

### `_evaluate_instance()` — per-instance wrapper

```
1. Get active test branches from tests.json
2. Get ignored tests
3. If not --force: check existing .eval.json, compute branches_to_eval (incremental)
4. If all branches evaluated: return summary from existing results
5. Determine solution source:
   - Gold mode: solution_branch = "gold"
   - Run mode: submission_zip = <run_dir>/<instance_id>/submission.zip
6. Create Evaluator, call .run()
7. If incremental: merge with existing results
8. Write .eval.json
9. Return InstanceEvalSummary
```

### Differences from source project's batch eval

- No `.traj.json` loading — submission is always `submission.zip`
- No annotation tags / cheat detection — raw scores only
- No info cache sidecar
- No `_get_annotation_tags()` / `_get_suspicious_pct()`
- `InstanceEvalSummary` is simpler (no `annotation_tags`, `cheat_detection_missing`, `suspicious_pct`, `original_score`)

---

## CLI Design

### `programbench eval`

```bash
# Evaluate a run directory
programbench eval output/run_name

# Evaluate gold solutions
programbench eval gold

# Multiple sources
programbench eval output/run_a output/run_b gold

# Options
programbench eval output/run_name --workers 4 --force
programbench eval output/run_name --filter 'eradman__entr.*'
programbench eval output/run_name --slice 0:5
programbench eval output/run_name --summarize-only
```

### Typer definition

```python
# src/programbench/cli/eval.py

@app.command()
def eval(
    sources: list[str],
    workers: int = typer.Option(1, "-w", "--workers"),
    force: bool = typer.Option(False, "-f", "--force"),
    filter_spec: str = typer.Option("", "--filter"),
    slice_spec: str = typer.Option("", "--slice"),
    summarize_only: bool = typer.Option(False, "--summarize-only"),
    image_tag: str = typer.Option("task", "--image-tag"),
) -> None: ...
```

Registered in `cli/main.py` via `app.command()` or as a separate sub-app.

---

## File Layout

```
src/programbench/
  __init__.py
  cli/
    __init__.py
    main.py              # typer app, registers eval command
    eval.py              # CLI eval command (thin wrapper)
  data/
    tasks/               # shipped task data (task.yaml, tests.json, tests/)
      <instance_id>/
        task.yaml
        tests.json
        build.sh         # for gold mode (if applicable)
        tests/
          <branch_hash>/
            eval/
              run.sh
              tests/
              ...
  exceptions.py          # EvalStepError, EmptyTestResultError, XmlParseError
  constants.py           # WORKSPACE_DIR, DOCKER_ORG, TASKS_DIR, paths
  container.py           # ContainerEnvironment class
  eval/
    __init__.py
    eval.py              # TestResult, EvaluationResult, Evaluator, parse_test_results
    eval_batch.py        # InstanceEvalSummary, BatchEvalSummary, run_eval_batch
  utils/
    __init__.py
    load_data.py         # load_all_instances, get_active_branches, get_ignored_tests
    instance_filters.py  # filter_instances (regex, slice, shuffle, has_test_branch)
```

---

## Dependencies

```toml
# pyproject.toml additions
dependencies = [
    "typer>=0.15",
    "jinja2>=3.1",
    "pydantic>=2",
    "junitparser>=3",
    "pyyaml>=6",
    "tqdm>=4",
    "rich>=13",       # may already be pulled in by typer
]
```

No `minisweagent`, `ghapi`, `orjson`, `ijson`, `zstandard`, `litellm` — all dropped.

---

## Implementation Order

### Step 1: Foundation
- `constants.py` — `WORKSPACE_DIR`, `DOCKER_ORG`, `TASKS_DIR`, `image_name_from_instance_id()`
- `exceptions.py` — `EvalStepError`, `EmptyTestResultError`, `XmlParseError`

### Step 2: Container environment
- `container.py` — `ContainerEnvironment` with `execute()`, `copy_in()`, `cleanup()`
- Test with a real Docker image

### Step 3: Data models
- `eval/eval.py` (models only) — `TestResult`, `TestBranchError`, `EvaluationResult`, `parse_test_results()`, `_process_branch_xml()`
- `utils/load_data.py` — `load_all_instances()`, `get_active_branches()`, `get_ignored_tests()`, `save_tests()`. No caching layer — just direct reads of `task.yaml` and `tests.json`.

### Step 4: Core evaluator
- `eval/eval.py` (Evaluator class) — `_run_step()`, `_compile_executable()`, `_run_test_branch()`, `_restore_executable()`, `_remove_hashed_files()`, `_inject_not_run()`, `run()`
- Including `from_existing` reprocessing

### Step 5: Batch evaluation
- `eval/eval_batch.py` — `_evaluate_instance()`, `get_branches_to_eval()`, `run_eval_batch()`, `InstanceEvalSummary`, `BatchEvalSummary`

### Step 6: CLI
- `cli/eval.py` — typer command
- Wire into `cli/main.py`

### Step 7: Test data

Create a minimal test instance (`testorg__calculator.abc1234`) that runs on a plain `ubuntu:22.04` base image. Lives in `data/` alongside real data for now; move to `tests/` later.

**The program**: a bash calculator — `./executable 2 + 3` outputs `5`. No compiler needed.

**Task data** (`data/tasks/testorg__calculator.abc1234/`):
- `task.yaml` — points to a dummy `testorg/calculator` repo
- `tests.json` — one branch hash (SHA-256 of original branch name, truncated to 12 chars)
- `build.sh` — copies source to `./executable` (for gold mode)
- `tests/<branch_hash>/eval/run.sh` — installs pytest via apt, runs tests, writes JUnit XML to `eval/results.xml`
- `tests/<branch_hash>/eval/tests/test_calculator.py` — pytest tests for addition, subtraction, multiplication

**Test submissions** (`data/test_runs/`):
- `correct/testorg__calculator.abc1234/submission.zip` — working calculator (`echo $(($1 $2 $3))`) + `compile.sh`
- `incorrect/testorg__calculator.abc1234/submission.zip` — broken calculator (always outputs `42`) + `compile.sh`

**Docker test image**:
- Dockerfile based on `ubuntu:22.04` with python3 + pip + pytest pre-installed
- Build as `programbench/testorg_1776_calculator.abc1234:task` (follows `image_name_from_instance_id` naming)
- Include a build script or document the `docker build` + `docker tag` commands

**What the tests validate**:
- Correct submission: all tests pass → score 1.0
- Incorrect submission: all tests fail → score 0.0
- Gold mode cannot be tested without a real GitHub repo (skip for now, or use a real tiny public repo later)

---

## Resolved Design Decisions

All open questions have been resolved. Summary of decisions:

- **`build.sh` location**: `data/tasks/<instance_id>/build.sh` — tracked in-tree alongside `task.yaml`
- **Private/archived repos**: No instances have private or archived original repos; gold mode `git clone` is always fine
- **Workspace wipe**: Full wipe is fine (`rm -rf /workspace/* /workspace/.[!.]*`). No test `run.sh` scripts rely on git commands being available in the workspace
- **Image tag**: Just bare `"task"` for now. Versioned tag system deferred
- **Instance filters**: Port as separate utility module (`src/programbench/utils/instance_filters.py`)
