# Blob Store: Moving Binary Files to HuggingFace Hub

## Problem

Task directories under `src/programbench/data/tasks/` will contain large binary files (compiled executables, Docker image layers, test fixtures) mixed with small text files (YAML configs, shell scripts, Python tests). These binaries bloat the git repo and make cloning slow.

## Goal

A user with nothing but `uv` installed can run:

```bash
uv run programbench eval <submission>
```

This installs `programbench` from PyPI, starts the eval, and transparently downloads any required assets on the fly. No separate sync step, no HF CLI, no manual prep.

## Solution

Use a HuggingFace Hub repository as a blob store. The HF repo mirrors the `data/tasks/` directory structure. Text files (configs, test scripts) ship inside the Python package on PyPI. Binary/large files live only in HF and are fetched lazily at eval time — per-instance, on demand, with local caching.

### Why HF over alternatives

| Option | Pros | Cons |
|--------|------|------|
| **Git LFS** | Transparent (just `git pull`) | GitHub bandwidth limits (1 GB/month free), expensive at scale; doesn't help PyPI package size |
| **HuggingFace Hub** | Free unlimited storage, versioned (git under the hood), good Python SDK, lazy per-file download with caching | Extra dependency, separate repo |
| **GitHub Releases** | Simple | No directory structure, no per-file download |
| **S3/GCS** | Full control | Requires cloud account, no built-in versioning, auth setup |

HF wins: free, versioned, commonly used by ML benchmarks, and `huggingface_hub` gives caching + partial downloads out of the box.

---

## Design

### What ships where

**In the PyPI package** (`src/programbench/data/tasks/`) — small text/config files:
```
<instance_id>/
  task.yaml
  tests.json
  build.sh
  Dockerfile
  tests/<branch_hash>/eval/run.sh
  tests/<branch_hash>/eval/tests/*.py
```

These are part of the Python package and installed with `pip`/`uv`.

**In the HF repo** — binary/large files, same structure:
```
<instance_id>/
  executable              # reference binary
  tests/<branch_hash>/eval/fixtures/large_input.bin
  ...
```

No file appears in both places. The split is by convention: text config/code goes in the package; binaries/large artifacts go in HF.

### Revision lock

A constant in `constants.py` pins the HF commit:

```python
HF_REVISION = "abc123..."  # full HF commit hash
```

Every PyPI release maps to exactly one HF state. Simple, no file I/O, no missing-file edge cases.

### Transparent lazy download

The key design principle: **the evaluator never explicitly "syncs" — it just asks for a path, and the blob store returns a local path, downloading if necessary.**

```python
# blob_store.py — the only interface eval code needs
def get_blob_dir(instance_id: str) -> Path | None:
    """Return local path to blobs for this instance, downloading if needed.
    Returns None if blob store is disabled or instance has no blobs."""
```

Under the hood this calls `snapshot_download()` with `allow_patterns` scoped to the instance. HF Hub's built-in cache means the second call for the same instance+revision is a no-op.

### Download & caching

`huggingface_hub.snapshot_download()`:
- Downloads to `~/.cache/huggingface/hub/` (configurable via `HF_HOME`)
- Caches by revision — re-download only when `HF_REVISION` changes (i.e., new PyPI release)
- `allow_patterns=f"{instance_id}/**"` fetches only the files for the instance being evaluated
- Thread-safe — multiple eval workers can call it concurrently

---

## Core module

```python
# src/programbench/utils/blob_store.py

from pathlib import Path
from programbench.constants import HF_REPO_ID, HF_REVISION

def get_blob_dir(instance_id: str) -> Path | None:
    """Download blobs for one instance on demand. Returns local path or None."""
    if not HF_REVISION:
        return None
    from huggingface_hub import snapshot_download
    base = Path(snapshot_download(
        HF_REPO_ID,
        revision=HF_REVISION,
        allow_patterns=f"{instance_id}/**",
    ))
    result = base / instance_id
    return result if result.exists() else None

def sync_all() -> Path | None:
    """Eagerly download all blobs. Returns cache root or None."""
    if not HF_REVISION:
        return None
    from huggingface_hub import snapshot_download
    return Path(snapshot_download(HF_REPO_ID, revision=HF_REVISION))
```

Note: `huggingface_hub` is imported lazily inside the functions. This keeps `import programbench` fast and avoids requiring HF Hub for code paths that don't need blobs (e.g., running tests, loading metadata).

### Push-side (developer only, not needed by eval users)

```python
def push_blobs(local_dir: Path, instance_id: str) -> str:
    """Upload binary files for an instance to HF. Returns new commit hash."""
    from huggingface_hub import HfApi
    api = HfApi()
    api.upload_folder(
        folder_path=str(local_dir),
        path_in_repo=instance_id,
        repo_id=HF_REPO_ID,
        repo_type="dataset",
    )
    return api.dataset_info(HF_REPO_ID).sha

def get_latest_revision() -> str:
    """Get the latest HF commit hash. Print this and paste into constants.py."""
    from huggingface_hub import HfApi
    return HfApi().dataset_info(HF_REPO_ID).sha
```

---

## Configuration

```python
# src/programbench/constants.py (additions)

import os

HF_REPO_ID = os.environ.get("PROGRAMBENCH_HF_REPO", "org/programbench-data")  # TODO: set actual org/repo
HF_REVISION = ""  # set to HF commit hash when blobs exist; empty string = blob store disabled
```

---

## Evaluator integration

### Changes to `Evaluator.__init__()`

Add `blob_dir: Path | None = None` parameter.

### Changes to `_run_test_branch()` in `eval/eval.py`

After copying the git-shipped test files, overlay blobs:

```python
def _run_test_branch(self, branch: str) -> str:
    # ... existing wipe ...
    test_dir = self.task_dir / "tests" / branch
    self.env.copy_in(test_dir, f"{WORKSPACE_DIR}/")

    # Overlay blob files if present for this branch
    if self.blob_dir is not None:
        blob_branch_dir = self.blob_dir / "tests" / branch
        if blob_branch_dir.exists():
            self.env.copy_in(blob_branch_dir, f"{WORKSPACE_DIR}/")

    self._restore_executable()
    # ... rest unchanged ...
```

### Changes to batch eval (`eval/eval_batch.py`)

The batch evaluator resolves blobs per-instance before constructing the `Evaluator`. The download happens here — one call per instance, cached:

```python
from programbench.utils.blob_store import get_blob_dir

blob_dir = get_blob_dir(instance_id)  # downloads on first call, cached after
evaluator = Evaluator(..., blob_dir=blob_dir)
```

With `--workers > 1`, multiple instances download concurrently. `snapshot_download` is thread-safe and the HF cache handles concurrent writes.

---

## CLI commands

### For eval users: nothing new

```bash
uv run programbench eval <submission>   # just works — downloads blobs transparently
```

### For developers: blob management

```bash
programbench blob push <local_dir> <instance_id>  # upload binaries to HF
programbench blob revision                         # print latest HF commit hash (paste into constants.py)
programbench blob sync                             # optional: eagerly pre-download all blobs
programbench blob sync <instance_id>               # optional: pre-download one instance
```

`sync` is optional — it's for pre-populating the cache (e.g., before going offline or in CI). Normal usage never needs it.

---

## Workflow for adding a new task (developer)

1. Create task directory in git with text files:
   ```
   src/programbench/data/tasks/<instance_id>/
     task.yaml, tests.json, build.sh, Dockerfile, tests/...
   ```

2. Push binary files to HF:
   ```bash
   programbench blob push /path/to/binaries <instance_id>
   ```

3. Update the revision constant:
   ```bash
   programbench blob revision  # prints latest HF commit hash
   # paste the hash into constants.py as HF_REVISION
   ```

4. Commit the git changes (including updated `HF_REVISION` in `constants.py`).

5. Release to PyPI.

## Workflow for running eval (end user)

```bash
uv run programbench eval output/my_run
```

That's it. On first run, blobs are downloaded per-instance as needed. Subsequent runs with the same package version hit the local cache.

---

## Graceful degradation

- **`HF_REVISION` is empty** → blob store disabled, eval uses package files only
- **No network** but cache populated → works from cache
- **No network** and no cache → `snapshot_download` raises; eval fails with a clear error pointing to the network requirement
- **Instance has no blobs in HF** → `get_blob_dir()` returns `None`, eval runs with package files only

This keeps the test suite and simple instances working without HF access.

---

## Dependencies

```toml
# pyproject.toml
dependencies = [
    ...,
    "huggingface_hub>=0.20",
]
```

---

## Implementation order

1. **`constants.py`** — add `HF_REPO_ID`, `HF_REVISION`
2. **`utils/blob_store.py`** — `get_blob_dir()`, `sync_all()`, `push_blobs()`, `get_latest_revision()`
3. **`eval/eval.py`** — add `blob_dir` to `Evaluator`, overlay in `_run_test_branch()`
4. **`eval/eval_batch.py`** — call `get_blob_dir()` per instance before constructing evaluators
5. **`cli/blob.py`** — developer-facing CLI commands, wire into `cli/main.py`
6. **Create HF repo** — set up on HuggingFace, push initial data, set `HF_REVISION`

---

## Open questions

- **HF repo name/org**: What org and repo name? (e.g., `programbench/data`, `klieret/programbench-blobs`)
- **Auth**: Public repo assumed (no auth for download). Developers need HF write token for `push`. Correct?
- **Which files are "binary"?**: Per-task judgment, or enforced by rule (e.g., extension allowlist)?
