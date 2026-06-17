# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the license found in the
# LICENSE file in the root directory of this source tree.

"""Create a submission's public GitHub repo and push it.

The middle step between ``package`` and ``register``: it turns a packaged run directory
into a public Git repo and pushes it. The heavy artifacts already live on HuggingFace (as
``.url`` + ``.sha256`` written by ``package``), so only light files are committed. With
``gh`` the repo is created and pushed in one shot; without ``gh`` it commits locally and
either pushes to a ``--remote`` you pre-created, or prints the steps to finish by hand.

The repo URL is never stored in ``submission.yaml`` — it defaults to the submission id and
``register`` reads it back from the git remote this sets, keeping the manifest host-agnostic.
"""

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def _to_https(url: str) -> str:
    """A git remote (``git@host:owner/repo.git`` or ``https://…``) as a browsable https URL."""
    url = url.removesuffix(".git")
    if url.startswith("git@"):
        host, path = url[4:].split(":", 1)
        return f"https://{host}/{path}"
    return url


def _origin(run_dir: Path) -> str | None:
    if not (run_dir / ".git").exists() or "origin" not in _git(run_dir, "remote").split():
        return None
    return _git(run_dir, "remote", "get-url", "origin")


@dataclass
class PublishResult:
    repo_url: str | None  # the pushed repo (https), when known
    committed: bool  # whether a new commit was made
    next_steps: str | None  # manual steps when we could not finish (no gh, no --remote)


def _ensure_committed(run_dir: Path) -> bool:
    """Init the repo if needed and commit any pending changes; True if a commit was made.

    Supplies a fallback git identity when none is configured (common in fresh CI containers,
    where ``git commit`` would otherwise error out)."""
    if not (run_dir / ".git").exists():
        _git(run_dir, "init", "-b", "main")
    _git(run_dir, "add", "-A")
    if not _git(run_dir, "status", "--porcelain"):
        return False
    ident = []
    if subprocess.run(["git", "config", "user.email"], cwd=run_dir, capture_output=True).returncode != 0:
        ident = ["-c", "user.name=ProgramBench", "-c", "user.email=submissions@programbench.com"]
    _git(run_dir, *ident, "commit", "-m", f"ProgramBench submission: {run_dir.resolve().name}")
    return True


def _gh_repo_url(slug: str, private: bool) -> str:
    """The repo's URL, creating it (public unless ``private``) if it doesn't exist yet."""
    view = ["gh", "repo", "view", slug, "--json", "url", "-q", ".url"]
    if subprocess.run(view, capture_output=True, text=True).returncode != 0:
        subprocess.run(
            ["gh", "repo", "create", slug, "--private" if private else "--public"],
            check=True,
            capture_output=True,
            text=True,
        )
    return subprocess.run(view, check=True, capture_output=True, text=True).stdout.strip()


def publish(run_dir: Path, owner: str = "", repo: str = "", private: bool = False, remote: str = "") -> PublishResult:
    name = repo or run_dir.resolve().name
    committed = _ensure_committed(run_dir)

    # Pick the target repo: an explicit --remote, an already-wired origin, or one created
    # via gh. Without any of those we can only commit locally and hand back the steps.
    target = remote or _origin(run_dir)
    if not target:
        if not shutil.which("gh"):
            steps = (
                "`gh` is not installed and no --remote was given, so the repo could not be created. "
                f"The submission is committed locally in {run_dir}. To finish:\n"
                f"  1. Create an empty PUBLIC repo (named '{name}') at https://github.com/new\n"
                "  2. From the submission directory, wire it up and push:\n"
                "       git remote add origin <its-url>\n"
                "       git push -u origin HEAD:main\n"
                "Then run `programbench submit register .` to register it on the leaderboard."
            )
            return PublishResult(None, committed, steps)
        target = _gh_repo_url(f"{owner}/{name}" if owner else name, private)

    # Push over HTTPS using gh's credentials: reliable everywhere (an SSH origin needs keys
    # set up, and would fail in sandboxes that block port 22).
    url = _to_https(target)
    _git(run_dir, "remote", "set-url" if _origin(run_dir) else "add", "origin", url)
    _git(run_dir, "push", "-u", "origin", "HEAD:main")
    return PublishResult(url, committed, None)
