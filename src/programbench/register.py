# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the license found in the
# LICENSE file in the root directory of this source tree.

"""Register a packaged submission into the leaderboard registry by opening a PR.

A registry entry is small and self-contained: a pointer to the submission's own public
repo, plus the manifest and stat files copied out of it.

    submissions/<id>/
      pointer.yaml      # source repo URL + the exact commit that was scored
      submission.yaml   # copied from the submission
      _stats/*.json     # copied from the submission

This builds that entry against a clone of the registry (default
github.com/ProgramBench/submissions) and opens the PR. With ``gh`` it forks the registry
and opens the PR for you; without it, it leaves the commit on a branch in a clone and
prints the compare URL so you can open the PR by hand.
"""

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import yaml

REGISTRY_DEFAULT = "https://github.com/ProgramBench/submissions"


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def _to_https(url: str) -> str:
    """A git remote (``git@host:owner/repo.git`` or ``https://…``) as a browsable https URL."""
    url = url.removesuffix(".git")
    if url.startswith("git@"):
        host, path = url[4:].split(":", 1)
        return f"https://{host}/{path}"
    return url


def _slug(registry: str) -> str:
    """``https://github.com/Owner/Repo`` -> ``Owner/Repo`` (what ``gh`` expects)."""
    return _to_https(registry).removeprefix("https://github.com/")


@dataclass
class RegisterPlan:
    submission_id: str
    source: str
    commit: str
    registry: str
    branch: str
    pointer: str  # rendered pointer.yaml
    files: list[str]  # entry-relative paths that will be added
    title: str
    body: str


@dataclass
class RegisterResult:
    plan: RegisterPlan
    pr_url: str | None  # set when a PR was opened (gh path)
    next_steps: str | None  # set when manual steps remain (no-gh path)


def build_plan(
    submission_dir: Path, registry: str, source: str | None = None, commit: str | None = None
) -> RegisterPlan:
    sub_id = submission_dir.resolve().name
    manifest = yaml.safe_load((submission_dir / "submission.yaml").read_text())
    # Overrides win; otherwise autodetect from the submission's own git remote/HEAD. The
    # autodetect calls are skipped (short-circuited) when an override is supplied.
    source = source or _to_https(_git(submission_dir, "remote", "get-url", "origin"))
    commit = commit or _git(submission_dir, "rev-parse", "HEAD")
    pointer = yaml.safe_dump({"submission_id": sub_id, "source": source, "commit": commit}, sort_keys=False)
    files = ["pointer.yaml", "submission.yaml"] + [
        f"_stats/{p.name}" for p in sorted((submission_dir / "_stats").glob("*.json"))
    ]
    system, head = manifest["system"], manifest["headline"]
    body = (
        f"Registers **{system['model']}** ({system['provider']}) + {system['agent']}.\n\n"
        f"- mean score: {head['mean_score'] * 100:.1f}\n"
        f"- resolved: {head['resolved_pct']:.1f}% / near-resolved: {head['near_resolved_pct']:.1f}%\n"
        f"- instances: {head['n_instances_attempted']}/{head['n_instances_total']}\n\n"
        f"Source: {source}\nCommit: `{commit}`\n\n"
        "Tier-0 verified (`programbench submit verify .`)."
    )
    return RegisterPlan(
        sub_id, source, commit, registry, f"add-{sub_id}", pointer, files, f"Add submission: {sub_id}", body
    )


def write_entry(plan: RegisterPlan, submission_dir: Path, registry_root: Path) -> Path:
    """Materialize ``submissions/<id>/`` under ``registry_root`` (overwriting any existing entry)."""
    entry = registry_root / "submissions" / plan.submission_id
    if entry.exists():
        shutil.rmtree(entry)
    (entry / "_stats").mkdir(parents=True)
    (entry / "pointer.yaml").write_text(plan.pointer)
    shutil.copyfile(submission_dir / "submission.yaml", entry / "submission.yaml")
    for p in sorted((submission_dir / "_stats").glob("*.json")):
        shutil.copyfile(p, entry / "_stats" / p.name)
    return entry


def register_submission(
    submission_dir: Path, registry: str, source: str | None = None, commit: str | None = None
) -> RegisterResult:
    """Clone the registry, commit the entry on a branch, and open the PR.

    Uses ``gh`` (fork + PR) when available, cleaning up its throwaway clone afterward.
    Without ``gh`` it leaves the commit on a branch in a kept clone and returns the manual
    push + compare-URL steps in ``next_steps`` (so the clone must outlive this call).
    """
    plan = build_plan(submission_dir, registry, source, commit)
    slug = _slug(registry)
    clone = Path(tempfile.mkdtemp(prefix="programbench-register-")) / "submissions"

    if shutil.which("gh"):
        # Fork the registry under the authed user (no-op if it exists) and clone the fork;
        # origin -> fork, upstream -> registry.
        subprocess.run(
            ["gh", "repo", "fork", slug, "--clone", "--default-branch-only", str(clone)],
            check=True,
            capture_output=True,
            text=True,
        )
        _git(clone, "checkout", "-b", plan.branch)
        write_entry(plan, submission_dir, clone)
        _git(clone, "add", f"submissions/{plan.submission_id}")
        _git(clone, "commit", "-m", plan.title)
        _git(clone, "push", "-u", "origin", plan.branch)
        pr_url = subprocess.run(
            ["gh", "pr", "create", "--repo", slug, "--title", plan.title, "--body", plan.body],
            cwd=clone,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        shutil.rmtree(clone.parent)
        return RegisterResult(plan, pr_url, None)

    # No gh: clone the registry directly, commit the branch, and hand back the steps.
    _git(clone.parent, "clone", "--depth", "1", _to_https(registry), str(clone))
    _git(clone, "checkout", "-b", plan.branch)
    write_entry(plan, submission_dir, clone)
    _git(clone, "add", f"submissions/{plan.submission_id}")
    _git(clone, "commit", "-m", plan.title)
    steps = (
        "`gh` not found, so the PR was not opened. The entry is committed on branch "
        f"`{plan.branch}` in:\n  {clone}\n\n"
        "To finish, from that clone push the branch to your fork of the registry and open a PR:\n"
        "  git remote add fork https://github.com/<you>/submissions\n"
        f"  git push -u fork {plan.branch}\n"
        f"  {_to_https(registry)}/compare/main...<you>:{plan.branch}?expand=1"
    )
    return RegisterResult(plan, None, steps)
