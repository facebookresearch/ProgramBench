# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the license found in the
# LICENSE file in the root directory of this source tree.

"""Verify a packaged submission against its own artifacts.

Tier 0 (default, no Docker): recompute each instance's per-test pass/fail from its own
eval.json and check it matches the submitted _stats/score.json — i.e. the reported scores
faithfully reflect the eval output. A free check a third party or CI can run with only
``programbench`` installed. (Leaderboard scores aren't stored in the submission, so there
is no headline to check against.)

Tier 1 (--tier1, Docker): resolve each submission.tar.gz, re-run ``programbench eval``,
and confirm the freshly produced scores match the submitted eval.json. This is what
proves the artifacts actually yield the reported results.
"""

import json
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from programbench.submission import (
    benchmark_instances,
    resolve_submission_tar,
    score_run,
    test_results_map,
)

TOLERANCE = 1e-6  # Tier-1 score floats are rounded; this only absorbs representation noise.


@dataclass
class Check:
    name: str
    claimed: object
    computed: object
    ok: bool


@dataclass
class VerifyResult:
    tier: int
    checks: list[Check]

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks)


def _close(a: object, b: object) -> bool:
    # Non-numeric (e.g. a user-edited/invalid manifest value) is a failed check, not a crash.
    if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
        return False
    return abs(a - b) <= TOLERANCE


def verify_tier0(submission_dir: Path) -> VerifyResult:
    """Per instance, recompute the per-test pass/fail from its eval.json and check it matches
    the submitted _stats/score.json (so the stored scores reflect the eval output, untampered)."""
    instances = benchmark_instances()
    stored = json.loads((submission_dir / "_stats" / "score.json").read_text())
    checks = []
    for iid, stored_map in sorted(stored.items()):
        eval_json = submission_dir / iid / f"{iid}.eval.json"
        if iid not in instances:
            checks.append(Check(iid, "in score.json", "not a benchmark instance", False))
        elif not eval_json.exists():
            checks.append(Check(iid, f"{sum(stored_map.values())}/{len(stored_map)} pass", "no eval.json", False))
        else:
            recomputed = test_results_map(eval_json, instances[iid])
            checks.append(
                Check(
                    iid,
                    f"{sum(stored_map.values())}/{len(stored_map)} pass",
                    f"{sum(recomputed.values())}/{len(recomputed)} pass",
                    recomputed == stored_map,
                )
            )
    return VerifyResult(0, checks)


def verify_tier1(submission_dir: Path, *, workers: int = 1, filter_spec: str = "") -> VerifyResult:
    from programbench.eval.eval_batch import run_eval_batch

    instances = benchmark_instances()
    sub_root = submission_dir
    submitted = score_run(sub_root, instances)

    # Same regex semantics as the re-eval filter (instance_filters.filter_instances): only
    # resolve/download and re-eval the targeted instances, not every submitted tarball.
    targets = [iid for iid in submitted if not filter_spec or re.match(filter_spec, iid)]

    with tempfile.TemporaryDirectory() as tmp:
        run = Path(tmp)
        for iid in targets:
            (run / iid).mkdir(parents=True)
            resolve_submission_tar(sub_root / iid, run / iid / "submission.tar.gz")
        run_eval_batch(sources=[run], workers=workers, filter_spec=filter_spec, force=True)
        fresh = score_run(run, instances)

    # A targeted instance that produced no fresh score is reported as a failure (NaN), not
    # silently skipped.
    checks = [
        Check(
            iid,
            round(submitted[iid], 4),
            round(fresh[iid], 4) if iid in fresh else float("nan"),
            _close(submitted[iid], fresh.get(iid)),
        )
        for iid in targets
    ]
    return VerifyResult(1, checks)
