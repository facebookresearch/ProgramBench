# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the license found in the
# LICENSE file in the root directory of this source tree.

"""Tests for the evaluation pipeline (data models, XML parsing, batch logic)."""

import pytest

from programbench.constants import TASKS_DIR
from programbench.eval.eval import (
    EvaluationResult,
    TestBranchError,
    TestResult,
    _process_branch_xml,
    count_worker_crashes,
    parse_test_results,
)
from programbench.eval.eval_batch import (
    BatchEvalSummary,
    InstanceEvalSummary,
    get_branches_to_eval,
)
from programbench.exceptions import EmptyTestResultError, XmlParseError
from programbench.utils.load_data import (
    get_active_branches,
    get_ignored_tests,
    load_all_instances,
)

JUNIT_XML_ALL_PASS = """\
<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest" errors="0" failures="0" skipped="0" tests="2">
    <testcase classname="tests.test_calculator" name="test_addition" time="0.01"/>
    <testcase classname="tests.test_calculator" name="test_subtraction" time="0.02"/>
  </testsuite>
</testsuites>
"""

JUNIT_XML_MIXED = """\
<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest" errors="0" failures="1" skipped="1" tests="3">
    <testcase classname="tests.test_calculator" name="test_addition" time="0.01"/>
    <testcase classname="tests.test_calculator" name="test_subtraction" time="0.02">
      <failure message="assert 7 == 8">AssertionError</failure>
    </testcase>
    <testcase classname="tests.test_calculator" name="test_skip" time="0.0">
      <skipped message="reason"/>
    </testcase>
  </testsuite>
</testsuites>
"""

JUNIT_XML_DUP_SAME_KIND = """\
<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest" tests="1">
    <testcase name="pytest.internal" time="0.0">
      <error message="internal error">trace 1</error>
      <error message="internal error">trace 2</error>
    </testcase>
  </testsuite>
</testsuites>
"""

JUNIT_XML_DUP_MIXED_KIND = """\
<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest" tests="1">
    <testcase name="test_mixed" time="0.0">
      <failure message="timeout">subprocess timed out</failure>
      <error message="worker crashed">xdist worker died</error>
    </testcase>
  </testsuite>
</testsuites>
"""


class TestParseTestResults:
    def test_all_pass(self):
        result = parse_test_results(JUNIT_XML_ALL_PASS, branch="b1")
        assert len(result) == 2
        assert all(t.status == "passed" for t in result)
        assert all(t.branch == "b1" for t in result)
        assert {t.name for t in result} == {
            "tests.test_calculator.test_addition",
            "tests.test_calculator.test_subtraction",
        }

    def test_mixed_results(self):
        result = parse_test_results(JUNIT_XML_MIXED, branch="b1")
        by_name = {t.name: t for t in result}
        assert by_name["tests.test_calculator.test_addition"].status == "passed"
        assert by_name["tests.test_calculator.test_subtraction"].status == "failure"
        assert by_name["tests.test_calculator.test_skip"].status == "skipped"

    def test_empty_xml_raises(self):
        with pytest.raises(EmptyTestResultError):
            parse_test_results("   ", branch="b1")

    def test_malformed_xml_raises(self):
        with pytest.raises(XmlParseError):
            parse_test_results("<not>valid xml", branch="b1")

    def test_duplicate_same_kind_collapses(self):
        result = parse_test_results(JUNIT_XML_DUP_SAME_KIND, branch="b1")
        assert len(result) == 1
        t = result.test_results[0]
        assert t.status == "error"
        assert t.extra["message"] == "internal error"

    def test_duplicate_mixed_kind_is_system_error(self):
        result = parse_test_results(JUNIT_XML_DUP_MIXED_KIND, branch="b1")
        assert len(result) == 1
        assert result.test_results[0].status == "system_error"
        assert "got 2" in result.test_results[0].extra["error_details"]


class TestProcessBranchXml:
    def test_missing_tests_get_not_run(self):
        tests_by_branch = {
            "b1": [
                "tests.test_calculator.test_addition",
                "tests.test_calculator.test_subtraction",
                "tests.test_calculator.test_missing",
            ]
        }
        results, warnings = _process_branch_xml(JUNIT_XML_ALL_PASS, "b1", tests_by_branch)
        by_name = {r.name: r for r in results}
        assert by_name["tests.test_calculator.test_missing"].status == "not_run"
        assert len(warnings) == 0

    def test_unexpected_tests_warn(self):
        tests_by_branch = {"b1": ["tests.test_calculator.test_addition"]}
        results, warnings = _process_branch_xml(JUNIT_XML_ALL_PASS, "b1", tests_by_branch)
        assert any("not in tests.json" in w for w in warnings)


class TestCountWorkerCrashes:
    XDIST_CRASH_XML = """\
<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest" errors="2" failures="0" skipped="0" tests="3">
    <testcase classname="t.t1" name="ok" time="0.01"/>
    <testcase classname="t.t1" name="bust" time="0.000">
      <error message="failed on setup with &quot;worker 'gw7' crashed while running 't.t1::bust'&quot;">worker 'gw7' crashed while running 't.t1::bust'</error>
    </testcase>
    <testcase classname="t.t2" name="bust2" time="0.000">
      <error message="failed on setup with &quot;worker 'gw3' crashed while running 't.t2::bust2'&quot;">worker 'gw3' crashed while running 't.t2::bust2'</error>
    </testcase>
  </testsuite>
</testsuites>
"""

    REGULAR_FAILURE_XML = """\
<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest" errors="0" failures="1" skipped="0" tests="1">
    <testcase classname="t" name="bad"><failure message="AssertionError">expected 1 got 2</failure></testcase>
  </testsuite>
</testsuites>
"""

    @pytest.mark.parametrize(
        ("xml", "expected"),
        [
            ("", 0),
            ("<not xml>", 0),
            (REGULAR_FAILURE_XML, 0),
            (JUNIT_XML_ALL_PASS, 0),
            (XDIST_CRASH_XML, 2),
        ],
    )
    def test_counts(self, xml, expected):
        assert count_worker_crashes(xml) == expected


class TestEvaluationResult:
    def test_score_all_pass(self):
        result = EvaluationResult(
            test_results=[
                TestResult(name="t1", branch="b1", status="passed", extra={}),
                TestResult(name="t2", branch="b1", status="passed", extra={}),
            ]
        )
        assert result.score == 1.0
        assert result.n_resolved == 2

    def test_score_partial(self):
        result = EvaluationResult(
            test_results=[
                TestResult(name="t1", branch="b1", status="passed", extra={}),
                TestResult(name="t2", branch="b1", status="failure", extra={}),
            ]
        )
        assert result.score == 0.5

    def test_score_empty(self):
        assert EvaluationResult().score == 0.0

    def test_without_ignored(self):
        result = EvaluationResult(
            test_results=[
                TestResult(name="t1", branch="b1", status="passed", extra={}),
                TestResult(name="t2", branch="b1", status="failure", extra={}),
            ]
        )
        filtered = result.without_ignored({"b1/t2"})
        assert len(filtered) == 1
        assert filtered.score == 1.0

    def test_for_branches(self):
        result = EvaluationResult(
            test_results=[
                TestResult(name="t1", branch="b1", status="passed", extra={}),
                TestResult(name="t2", branch="b2", status="failure", extra={}),
            ],
            test_branches=["b1", "b2"],
            test_branch_errors={"b2": [TestBranchError(error_code="x", error_details="")]},
        )
        scoped = result.for_branches(["b1"])
        assert len(scoped) == 1
        assert "b2" not in scoped.test_branch_errors


class TestGetBranchesToEval:
    def test_no_existing_returns_all(self, tmp_path):
        assert get_branches_to_eval(
            eval_json=tmp_path / "nonexistent.json",
            all_test_branches=["b1", "b2"],
            tests_by_branch={"b1": ["t1"], "b2": ["t2"]},
            ignored_tests=set(),
        ) == ["b1", "b2"]

    def test_fully_evaluated_returns_empty(self, tmp_path):
        eval_json = tmp_path / "eval.json"
        result = EvaluationResult(
            test_results=[
                TestResult(name="t1", branch="b1", status="passed", extra={}),
            ],
            test_branches=["b1"],
        )
        eval_json.write_text(result.model_dump_json())
        assert (
            get_branches_to_eval(
                eval_json=eval_json,
                all_test_branches=["b1"],
                tests_by_branch={"b1": ["t1"]},
                ignored_tests=set(),
            )
            == []
        )

    def test_branch_with_error_needs_reeval(self, tmp_path):
        eval_json = tmp_path / "eval.json"
        result = EvaluationResult(
            test_results=[],
            test_branches=["b1"],
            test_branch_errors={"b1": [TestBranchError(error_code="fail", error_details="")]},
        )
        eval_json.write_text(result.model_dump_json())
        assert get_branches_to_eval(
            eval_json=eval_json,
            all_test_branches=["b1"],
            tests_by_branch={"b1": ["t1"]},
            ignored_tests=set(),
        ) == ["b1"]


class TestInstanceEvalSummary:
    def test_from_eval_result(self):
        result = EvaluationResult(
            test_results=[
                TestResult(name="t1", branch="b1", status="passed", extra={}),
                TestResult(name="t2", branch="b1", status="failure", extra={}),
            ],
            test_branches=["b1"],
        )
        summary = InstanceEvalSummary.from_eval_result("test_instance", result)
        assert summary.score == 0.5
        assert summary.n_resolved == 1
        assert summary.n_tests == 2


class TestBatchEvalSummary:
    def test_average_pass_rate(self):
        batch = BatchEvalSummary(
            summaries=[
                InstanceEvalSummary(instance_id="a", score=1.0, n_resolved=2, n_tests=2),
                InstanceEvalSummary(instance_id="b", score=0.0, n_resolved=0, n_tests=2),
            ]
        )
        assert batch.average_pass_rate == 0.5
        assert batch.total_instances == 2


class TestLoadData:
    def test_load_all_instances(self):
        instances = load_all_instances()
        assert len(instances) >= 1
        ids = {inst["instance_id"] for inst in instances}
        assert "testorg__calculator.abc1234" in ids

    def test_get_active_branches(self):
        instances = load_all_instances()
        calc = next(i for i in instances if i["instance_id"] == "testorg__calculator.abc1234")
        assert get_active_branches(calc) == ["33128f6b8600"]

    def test_get_ignored_tests_empty(self):
        instances = load_all_instances()
        calc = next(i for i in instances if i["instance_id"] == "testorg__calculator.abc1234")
        assert get_ignored_tests(calc) == set()

    def test_task_dir_exists(self):
        assert (TASKS_DIR / "testorg__calculator.abc1234" / "task.yaml").exists()
        assert (TASKS_DIR / "testorg__calculator.abc1234" / "tests.json").exists()
        assert (TASKS_DIR / "testorg__calculator.abc1234" / "tests" / "33128f6b8600" / "eval" / "run.sh").exists()
