# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the license found in the
# LICENSE file in the root directory of this source tree.

"""Verify a packaged submission against its own claimed results.

Tier 0 (default, no Docker): recompute the headline from the submission's own eval.json
files (with ignored-test filtering) and check it matches submission.yaml. This is the
free consistency check a third party or CI can run with only ``programbench`` installed.

Tier 1 (--tier1, Docker): resolve each submission.tar.gz, re-run ``programbench eval``,
and confirm the freshly produced scores match the submitted eval.json. This is what
proves the artifacts actually yield the reported results.
"""

import logging
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from programbench.submission import (
    Headline,
    aggregate,
    benchmark_instances,
    load_manifest,
    resolve_submission_tar,
    score_run,
)

log = logging.getLogger(__name__)

TOLERANCE = 0.011  # headline floats are rounded; allow a hair more than the last digit


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
    if a is None or b is None:
        return False
    return abs(float(a) - float(b)) <= TOLERANCE


def _headline_checks(claimed: dict, computed: Headline) -> list[Check]:
    return [
        Check(name, claimed.get(name), value, _close(claimed.get(name), value))
        for name, value in computed.as_dict().items()
    ]


def verify_tier0(submission_dir: Path) -> VerifyResult:
    manifest = load_manifest(submission_dir)
    instances = benchmark_instances()
    computed = aggregate(score_run(submission_dir, instances), len(instances))
    return VerifyResult(0, _headline_checks(manifest.get("headline", {}), computed))


def verify_tier1(submission_dir: Path, *, workers: int = 1, filter_spec: str = "") -> VerifyResult:
    from programbench.eval.eval_batch import run_eval_batch

    instances = benchmark_instances()
    sub_root = submission_dir
    submitted = score_run(sub_root, instances)

    with tempfile.TemporaryDirectory() as tmp:
        run = Path(tmp)
        for iid in submitted:
            (run / iid).mkdir(parents=True)
            resolve_submission_tar(sub_root / iid, run / iid / "submission.tar.gz")
        run_eval_batch(sources=[run], workers=workers, filter_spec=filter_spec, force=True)
        fresh = score_run(run, instances)

    # Same regex semantics as the re-eval filter (instance_filters.filter_instances), so a
    # filtered-in instance that produced no fresh score is reported as a failure (NaN), not
    # silently skipped.
    targets = [iid for iid in submitted if not filter_spec or re.match(filter_spec, iid)]
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
