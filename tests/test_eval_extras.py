# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the license found in the
# LICENSE file in the root directory of this source tree.

"""Tests for eval functions not covered by test_eval.py."""

import pytest

from programbench.eval.eval import (
    EvaluationResult,
    TestBranchError,
    TestResult,
    count_testcases,
)
from programbench.eval.eval_batch import _can_reprocess


JUNIT_XML_THREE_CASES = """\
<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest" tests="3">
    <testcase classname="t" name="a" time="0.01"/>
    <testcase classname="t" name="b" time="0.02"/>
    <testcase classname="t" name="c" time="0.03"/>
  </testsuite>
</testsuites>
"""


class TestCountTestcases:
    @pytest.mark.parametrize(
        ("xml", "expected"),
        [
            ("", 0),
            ("   \n  ", 0),
            ("<not valid xml>", 0),
            (JUNIT_XML_THREE_CASES, 3),
        ],
    )
    def test_counts(self, xml, expected):
        assert count_testcases(xml) == expected


class TestEvaluationResultSummarize:
    def test_clean_run(self):
        result = EvaluationResult(
            test_results=[
                TestResult(name="t1", branch="b1", status="passed", extra={}),
                TestResult(name="t2", branch="b1", status="passed", extra={}),
            ],
            solution_branch="submission",
        )
        s = result.summarize()
        assert "100" in s
        assert "2/2" in s
        assert "submission" in s

    def test_with_error_code(self):
        result = EvaluationResult(error_code="compile_failed", error_details="gcc not found")
        s = result.summarize()
        assert "compile_failed" in s
        assert "gcc not found" in s

    def test_with_branch_errors(self):
        result = EvaluationResult(
            test_results=[TestResult(name="t1", branch="b1", status="passed", extra={})],
            test_branch_errors={"b2": [TestBranchError(error_code="timeout", error_details="")]},
        )
        assert "b2" in result.summarize()

    def test_with_system_errors(self):
        result = EvaluationResult(
            test_results=[TestResult(name="t1", branch="b1", status="system_error", extra={})],
        )
        assert "system_errors=1" in result.summarize()

    def test_with_warnings(self):
        result = EvaluationResult(warnings=["something unexpected"])
        assert "warnings=1" in result.summarize()


class TestCanReprocess:
    def test_error_code_is_reprocessable(self):
        assert _can_reprocess(EvaluationResult(error_code="compile_failed"))

    def test_all_branches_tagged_in_log(self):
        result = EvaluationResult(
            test_branches=["b1", "b2"],
            log=[
                {"step": "results_read", "branch": "b1", "returncode": 0, "output": "<xml/>"},
                {"step": "results_read", "branch": "b2", "returncode": 0, "output": "<xml/>"},
            ],
        )
        assert _can_reprocess(result)

    def test_missing_branch_in_log_not_reprocessable(self):
        result = EvaluationResult(
            test_branches=["b1", "b2"],
            log=[
                {"step": "results_read", "branch": "b1", "returncode": 0, "output": "<xml/>"},
            ],
        )
        assert not _can_reprocess(result)

    def test_branch_with_error_excluded_from_check(self):
        result = EvaluationResult(
            test_branches=["b1", "b2"],
            test_branch_errors={"b2": [TestBranchError(error_code="fail", error_details="")]},
            log=[
                {"step": "results_read", "branch": "b1", "returncode": 0, "output": "<xml/>"},
            ],
        )
        assert _can_reprocess(result)
