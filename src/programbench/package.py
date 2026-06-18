# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the license found in the
# LICENSE file in the root directory of this source tree.

"""Turn a ``programbench eval`` run directory into a leaderboard submission, in place.

Packaging is purely eval-derived. It writes:

- ``_stats/score.json`` — per-instance, per-test pass/fail (the one stat from evaluation),
- ``submission.yaml`` — the manifest, with ``[auto]`` score fields recomputed and any
  author-entered fields preserved across re-packaging,

and splits each ``<iid>.eval.json`` into a light eval.json + a heavy ``<iid>.eval.log.json``
(the raw log + failure text) so the run repo stays git-pushable; the two recombine to the
original via ``programbench submit recombine``. With ``--upload-to`` the heavy files and the
``submission.tar.gz`` artifacts go to a HuggingFace dataset (replaced by ``.url`` + ``.sha256``).

Other stats (cost, calls, …) are optional and come from the agent trajectories via scripts
the submitter writes — this command produces none of them, and makes no assumptions about
the scaffold. The run directory stays a valid input to ``programbench eval``.
"""

import logging
import os
import shutil
import tempfile
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path

import yaml
from jinja2 import Environment, PackageLoader

from programbench.submission import (
    Headline,
    aggregate,
    benchmark_instances,
    score_from_tests,
    sha256_file,
    split_eval_json,
    test_results_map,
    write_stat,
)

log = logging.getLogger(__name__)

TODO = "TODO"

# Author-entered manifest fields preserved across re-packaging: template var -> (path, default).
_CARRIED = {
    "affiliation": ("submitter.affiliation", ""),
    "agent": ("system.agent", TODO),
    "description_url": ("system.description_url", "README.md"),
    "is_os_model": ("system.is_os_model", False),
    "is_os_scaffold": ("system.is_os_scaffold", False),
    "model": ("system.model", TODO),
    "provider": ("system.provider", TODO),
    "submitter_contact": ("submitter.contact", TODO),
    "submitter_name": ("submitter.name", TODO),
    "system_type": ("system.type", "single-agent"),
}


@dataclass
class PackageResult:
    run_dir: Path
    packaged: list[str]
    skipped: list[str]
    headline: Headline


def _dig(d: dict, dotted: str):
    for key in dotted.split("."):
        if not isinstance(d, dict):
            return None
        d = d.get(key)
    return d


def _carried_values(run_dir: Path) -> dict:
    manifest_path = run_dir / "submission.yaml"
    existing = yaml.safe_load(manifest_path.read_text()) if manifest_path.exists() else {}
    # Use "is None" (not "or") so a real False/empty value is preserved, not clobbered.
    return {
        var: (default if (val := _dig(existing, path)) is None else val) for var, (path, default) in _CARRIED.items()
    }


def _upload_artifacts(
    api, dataset: str, pending: list[tuple[Path, str, str]], existing: set[str], overwrite: bool
) -> None:
    """Upload all pending files to HF, then replace each with a .url + .sha256 and delete it.

    ``pending`` is (instance_dir, instance_id, filename) — submission.tar.gz and the heavy
    <iid>.eval.log.json. Files already on HF are skipped unless ``overwrite``. Uses
    ``upload_large_folder`` (resumable, multi-commit, retrying) since logs can be hundreds
    of MB and a single big commit is fragile; files are hard-linked into a staging tree so
    nothing is copied.
    """
    for instance_dir, iid, fname in pending:
        (instance_dir / f"{fname}.sha256").write_text(sha256_file(instance_dir / fname) + "\n")
    to_upload = [(d, iid, f) for d, iid, f in pending if overwrite or f"{iid}/{f}" not in existing]
    if to_upload:
        run_dir = pending[0][0].parent
        with tempfile.TemporaryDirectory(dir=run_dir) as tmp:
            staging = Path(tmp)
            for instance_dir, iid, fname in to_upload:
                dst = staging / iid / fname
                dst.parent.mkdir(parents=True, exist_ok=True)
                try:
                    os.link(instance_dir / fname, dst)  # same-fs hardlink: no copy
                except OSError:
                    shutil.copy2(instance_dir / fname, dst)
            log.info("Uploading %d file(s) to %s (resumable)", len(to_upload), dataset)
            api.upload_large_folder(repo_id=dataset, folder_path=str(staging), repo_type="dataset")
    for instance_dir, iid, fname in pending:
        (instance_dir / f"{fname}.url").write_text(
            f"https://huggingface.co/datasets/{dataset}/resolve/main/{iid}/{fname}\n"
        )
        (instance_dir / fname).unlink()


def package_run(run_dir: Path, upload_to: str | None = None, overwrite: bool = False) -> PackageResult:
    instances = benchmark_instances()
    run_name = run_dir.resolve().name

    api = dataset = None
    existing: set[str] = set()
    if upload_to:
        # Each submission gets its own dataset: bare "org" -> "org/<run-name>";
        # an explicit "org/name" is used as-is.
        dataset = upload_to if "/" in upload_to else f"{upload_to}/{run_name}"
        from huggingface_hub import HfApi

        api = HfApi()
        api.create_repo(dataset, repo_type="dataset", exist_ok=True)
        # Force public so `verify`/`recombine` can fetch the artifacts anonymously
        # (orgs may default new datasets to private).
        api.update_repo_settings(dataset, repo_type="dataset", private=False)
        existing = set(api.list_repo_files(dataset, repo_type="dataset"))

    test_maps: dict[str, dict[str, bool]] = {}
    packaged: list[str] = []
    skipped: list[str] = []
    pending: list[tuple[Path, str, str]] = []
    for instance_dir in sorted(d for d in run_dir.iterdir() if d.is_dir()):
        iid = instance_dir.name
        eval_json = instance_dir / f"{iid}.eval.json"
        # Any artifact form resolve_submission_tar understands counts as a solution.
        has_solution = any(
            (instance_dir / f).exists() for f in ("submission.tar.gz", "submission.tar.gz.url", "submission.ref.yaml")
        )
        if not (eval_json.exists() and has_solution):
            continue
        if iid not in instances:
            log.warning("Skipping %s (not a known ProgramBench instance)", iid)
            skipped.append(iid)
            continue
        test_maps[iid] = test_results_map(eval_json, instances[iid])
        # Split the (potentially huge) eval.json into a light eval.json + a heavy
        # <iid>.eval.log.json (log + failure text); they recombine to the original.
        split_eval_json(instance_dir, iid)
        if api:
            for fname in (f"{iid}.eval.log.json", "submission.tar.gz"):
                if (instance_dir / fname).exists():
                    pending.append((instance_dir, iid, fname))
        packaged.append(iid)

    if not packaged:
        raise ValueError(f"No packageable instances found under {run_dir}")

    # Write the scoring-derived artifacts first; they don't depend on the upload, so a
    # failed/throttled upload leaves them correct and the run simply resumable.
    # score.json is per-test ({iid: {test: passed}}) so scores can be recomputed later
    # while striking out specific tests; the manifest headline is the score with no
    # tests struck.
    write_stat(run_dir, "score", test_maps)
    scores = {iid: score_from_tests(m) for iid, m in test_maps.items()}
    headline = aggregate(scores, len(instances))

    carried = _carried_values(run_dir)
    env = Environment(loader=PackageLoader("programbench", "data/templates"), autoescape=False)
    (run_dir / "submission.yaml").write_text(
        env.get_template("submission.yaml.j2").render(
            run_dir=run_dir,
            submission_id=run_dir.resolve().name,
            programbench_version=version("programbench"),
            **carried,
        )
        + "\n"
    )

    # README is created once (a starting point for the author); never overwritten.
    readme = run_dir / "README.md"
    if not readme.exists():
        readme.write_text(
            env.get_template("README.md.j2").render(
                submission_id=run_dir.resolve().name,
                mean_pct=round(headline.mean_score * 100, 1),
                resolved_pct=headline.resolved_pct,
                n_attempted=headline.n_instances_attempted,
                n_total=headline.n_instances_total,
                **carried,
            )
        )

    if api and pending:
        _upload_artifacts(api, dataset, pending, existing, overwrite)

    return PackageResult(run_dir, packaged, skipped, headline)
