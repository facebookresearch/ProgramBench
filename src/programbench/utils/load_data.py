from __future__ import annotations

import json
from concurrent.futures.thread import ThreadPoolExecutor
from pathlib import Path

import yaml

from programbench.constants import TASK_YAML, TASKS_DIR, TESTS_JSON, image_name_from_instance_id


def _load_single_instance(task_dir: Path, include_tests: bool) -> dict:
    config = yaml.safe_load((task_dir / TASK_YAML).read_text())
    entry: dict = {**config}
    entry["instance_id"] = task_dir.name
    entry["image_name"] = image_name_from_instance_id(task_dir.name)
    if include_tests:
        tests_file = task_dir / TESTS_JSON
        if tests_file.exists():
            entry.update(json.loads(tests_file.read_text()))
        entry.setdefault("branches", {})
    return entry


def get_active_branches(inst: dict) -> list[str]:
    """Return branch names that are not ignored."""
    return [name for name, info in (inst.get("branches") or {}).items() if not info.get("ignored")]


def get_ignored_tests(inst: dict) -> set[str]:
    """Return ``{branch/test_name}`` set for all ignored tests across all branches."""
    result: set[str] = set()
    for branch, info in (inst.get("branches") or {}).items():
        for t in info.get("ignored_tests") or []:
            result.add(f"{branch}/{t['name']}")
    return result


def load_all_instances(
    tasks_dir: Path = TASKS_DIR,
    *,
    include_tests: bool = True,
) -> list[dict]:
    """Load all instances from per-task directories."""
    task_dirs = sorted(d for d in tasks_dir.iterdir() if d.is_dir() and (d / TASK_YAML).exists())
    with ThreadPoolExecutor() as pool:
        return list(pool.map(lambda d: _load_single_instance(d, include_tests), task_dirs))


def save_tests(task_dir: Path, data: dict) -> None:
    """Write test-related data to tests.json."""
    (task_dir / TESTS_JSON).write_text(json.dumps(data, indent=2, sort_keys=True))
