# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the license found in the
# LICENSE file in the root directory of this source tree.

"""Shared helpers for building (`package`) and checking (`verify`) submissions.

Both commands must score a run directory the same way, so the scoring and headline
aggregation live here and are imported by each command.
"""

import hashlib
import json
import shutil
import subprocess
import tarfile
import tempfile
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path

import yaml

from programbench.eval.eval import EvaluationResult
from programbench.utils.load_data import get_active_branches, get_ignored_tests, load_all_instances

RESOLVED_THRESHOLD = 1.0
NEAR_RESOLVED_THRESHOLD = 0.95
FIXTURE_PREFIX = "testorg__"
DOWNLOAD_TIMEOUT = 60  # seconds; fail fast rather than hang on a stalled connection


def _checked_url(raw: str) -> str:
    """A submission-supplied URL, rejecting non-http(s) schemes (e.g. file://) to avoid SSRF
    / local file reads when resolving untrusted third-party submissions."""
    url = raw.strip()
    if urllib.parse.urlparse(url).scheme not in ("http", "https"):
        raise ValueError(f"refusing to fetch non-http(s) URL: {url!r}")
    return url


def benchmark_instances() -> dict[str, dict]:
    """Real benchmark instances, keyed by id (excludes the bundled test fixture)."""
    return {i["instance_id"]: i for i in load_all_instances() if not i["instance_id"].startswith(FIXTURE_PREFIX)}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def test_results_map(eval_json: Path, instance: dict) -> dict[str, bool]:
    """Per-test pass/fail for one instance, after the same active-branch / ignored-test
    filtering as ``info``. Keyed by ``"<branch>/<test_name>"``, value ``True`` iff passed.

    This is the raw material a score is computed from, so the leaderboard can later
    recompute scores while striking out specific tests (see the registry's ignore map).
    """
    result = EvaluationResult.model_validate_json(eval_json.read_text())
    result = result.for_branches(get_active_branches(instance)).without_ignored(get_ignored_tests(instance))
    return {t.full_name: t.is_resolved for t in result.test_results}


def score_from_tests(tests: dict[str, bool], ignore: set[str] = frozenset()) -> float:
    """Fraction passed over the non-ignored tests (0.0 if none remain)."""
    kept = [passed for name, passed in tests.items() if name not in ignore]
    return sum(kept) / len(kept) if kept else 0.0


def score_instance(eval_json: Path, instance: dict) -> float:
    """Per-instance score with ignored-branch/test filtering (same logic as `info`)."""
    return score_from_tests(test_results_map(eval_json, instance))


def score_run(run_dir: Path, instances: dict[str, dict]) -> dict[str, float]:
    """Map instance_id -> score for every <iid>/<iid>.eval.json present and known."""
    scores: dict[str, float] = {}
    for instance_dir in sorted(d for d in run_dir.iterdir() if d.is_dir()):
        iid = instance_dir.name
        eval_json = instance_dir / f"{iid}.eval.json"
        if eval_json.exists() and iid in instances:
            scores[iid] = score_instance(eval_json, instances[iid])
    return scores


def write_stat(run_dir: Path, stat: str, by_instance: dict[str, object]) -> None:
    """Write a per-instance stat file: ``<run_dir>/_stats/<stat>.json`` = ``{iid: value}``."""
    (run_dir / "_stats").mkdir(exist_ok=True)
    (run_dir / "_stats" / f"{stat}.json").write_text(json.dumps(by_instance, indent=2, sort_keys=True))


_HEAVY_EXTRA_KEYS = ("message", "text")


def _full_name(t: dict) -> str:
    return f"{t['branch']}/{t['name']}" if t.get("branch") else t["name"]


def split_eval_json(instance_dir: Path, iid: str) -> None:
    """Split ``<iid>.eval.json`` into a light eval.json + a heavy ``<iid>.eval.log.json``.

    The heavy file holds the only bulky parts — the top-level ``log`` and each failing
    test's ``message``/``text`` — keyed so the two recombine losslessly. Nothing is dropped;
    the union of the two files holds everything in the original eval.json (the rebuilt file
    is semantically identical, though not necessarily byte-for-byte).
    """
    p = instance_dir / f"{iid}.eval.json"
    data = json.loads(p.read_text())
    # Idempotent: if there's nothing heavy left (already split, or genuinely light), do
    # nothing — never clobber an existing eval.log.json.
    has_heavy = bool(data.get("log")) or any(
        k in (t.get("extra") or {}) for t in data.get("test_results", []) for k in _HEAVY_EXTRA_KEYS
    )
    if not has_heavy:
        return
    heavy: dict = {"log": data.get("log") or [], "failures": {}}
    for t in data.get("test_results", []):
        extra = t.get("extra") or {}
        moved = {k: extra.pop(k) for k in _HEAVY_EXTRA_KEYS if k in extra}
        if moved:
            heavy["failures"][_full_name(t)] = moved
    data["log"] = []
    p.write_text(json.dumps(data, indent=2))
    (instance_dir / f"{iid}.eval.log.json").write_text(json.dumps(heavy))


def recombine_eval_json(instance_dir: Path, iid: str) -> bool:
    """Inverse of :func:`split_eval_json`: fold the heavy file back into ``<iid>.eval.json``
    (restoring the full eval output losslessly), then remove the heavy file and its
    ``.url``/``.sha256``.

    The heavy file is read locally, or downloaded from ``<iid>.eval.log.json.url`` if hosted;
    a downloaded file is checked against its ``.sha256`` sidecar when one is present.
    Returns True if a recombine happened.
    """
    light = instance_dir / f"{iid}.eval.json"
    log_file = instance_dir / f"{iid}.eval.log.json"
    url_file = instance_dir / f"{iid}.eval.log.json.url"
    if not light.exists():
        return False
    if log_file.exists():
        heavy = json.loads(log_file.read_text())
    elif url_file.exists():
        with urllib.request.urlopen(_checked_url(url_file.read_text()), timeout=DOWNLOAD_TIMEOUT) as r:  # noqa: S310
            raw = r.read()
        sha_file = instance_dir / f"{iid}.eval.log.json.sha256"
        if sha_file.exists() and (got := hashlib.sha256(raw).hexdigest()) != sha_file.read_text().split()[0]:
            raise ValueError(f"{iid}: eval.log.json sha256 mismatch on download (got {got[:12]}…)")
        heavy = json.loads(raw)
    else:
        return False
    data = json.loads(light.read_text())
    data["log"] = heavy.get("log", [])
    failures = heavy.get("failures", {})
    for t in data.get("test_results", []):
        if (name := _full_name(t)) in failures:
            t.setdefault("extra", {}).update(failures[name])
    light.write_text(json.dumps(data, indent=2))
    for f in (log_file, url_file, instance_dir / f"{iid}.eval.log.json.sha256"):
        f.unlink(missing_ok=True)
    return True


@dataclass
class Headline:
    mean_score: float
    resolved_pct: float
    near_resolved_pct: float
    n_instances_attempted: int
    n_instances_total: int

    def as_dict(self) -> dict:
        return asdict(self)


def aggregate(scores: dict[str, float], n_total: int) -> Headline:
    values = list(scores.values())
    if not values:
        raise ValueError("No scored instances found")
    n = len(values)
    # mean, resolved, and near are all over the full benchmark — an unattempted task counts
    # as 0, matching how the leaderboard scores partial submissions.
    return Headline(
        mean_score=round(sum(values) / n_total, 4),
        resolved_pct=round(100 * sum(s >= RESOLVED_THRESHOLD for s in values) / n_total, 1),
        near_resolved_pct=round(100 * sum(s >= NEAR_RESOLVED_THRESHOLD for s in values) / n_total, 1),
        n_instances_attempted=n,
        n_instances_total=n_total,
    )


def load_manifest(submission_dir: Path) -> dict:
    return yaml.safe_load((submission_dir / "submission.yaml").read_text())


def resolve_submission_tar(instance_dir: Path, dest_tar: Path) -> None:
    """Materialize an instance's submission.tar.gz into ``dest_tar``, verifying sha256.

    Supports three artifact forms: inline file, ``.url`` (downloaded), or
    ``submission.ref.yaml`` (git checkout packed). The sha256 sidecar, when present, is
    enforced for inline/url; for git it is advisory (packing is not byte-reproducible).
    """
    sha_file = instance_dir / "submission.tar.gz.sha256"
    expected = sha_file.read_text().split()[0] if sha_file.exists() else None

    inline = instance_dir / "submission.tar.gz"
    url_file = instance_dir / "submission.tar.gz.url"
    ref_file = instance_dir / "submission.ref.yaml"
    if inline.exists():
        shutil.copy2(inline, dest_tar)
    elif url_file.exists():
        with (
            urllib.request.urlopen(_checked_url(url_file.read_text()), timeout=DOWNLOAD_TIMEOUT) as r,  # noqa: S310
            dest_tar.open("wb") as out,
        ):
            shutil.copyfileobj(r, out)
    elif ref_file.exists():
        _pack_git_ref(yaml.safe_load(ref_file.read_text()), dest_tar)
        expected = None  # git packing is not byte-reproducible; rely on re-eval instead
    else:
        raise ValueError(f"{instance_dir.name}: no submission.tar.gz, .url, or .ref.yaml found")

    if expected and (got := sha256_file(dest_tar)) != expected:
        raise ValueError(f"{instance_dir.name}: sha256 mismatch (expected {expected[:12]}…, got {got[:12]}…)")


def _pack_git_ref(ref: dict, dest_tar: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "src"
        subprocess.run(
            ["git", "clone", "--depth", "1", "--branch", ref["ref"], ref["repo"], str(src)],
            check=True,
            capture_output=True,
        )
        root = src / ref["subpath"] if ref.get("subpath") else src
        with tarfile.open(dest_tar, "w:gz") as tar:
            for p in sorted(root.rglob("*")):
                rel = p.relative_to(root).as_posix()
                if rel.split("/", 1)[0] == ".git":
                    continue
                tar.add(p, arcname=rel, recursive=False)
