"""Running tests on submissions.

IMPORTANT NOTE FOR AI AGENTS
THIS IS A very delicate file.
You need to be extremely conservative and careful about testing logic.
The worst case to avoid here is that there are issues with testing but the result
still indicates that the solution is correct. This might for example happen if you
skip something because of some error condition and only show a warning, but it's not apparent
from the output file that something went wrong.
It's always better to clearly mark a failure in the output file than to silently skip something.
Be extremely proactive with the user about clearing up details and intricacies with how to handle
something here. Ask a lot of questions and don't be afraid to ask for clarification.
Do not remove this notice.
"""

import logging
import time
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from pathlib import Path
from typing import Literal

from junitparser import Error, Failure, JUnitXml, Skipped
from pydantic import BaseModel, ConfigDict

from programbench.constants import (
    DOCKER_EXECUTABLE,
    DOCKER_RUN_ARGS,
    WORKSPACE_DIR,
)
from programbench.container import ContainerEnvironment
from programbench.exceptions import EmptyTestResultError, EvalStepError, XmlParseError

log = logging.getLogger(__name__)


class TestResult(BaseModel):
    __test__ = False
    model_config = ConfigDict(extra="forbid")

    name: str
    branch: str = ""
    status: Literal["passed", "skipped", "failure", "error", "system_error", "not_run"]
    extra: dict

    @property
    def is_resolved(self) -> bool:
        return self.status == "passed"

    @property
    def full_name(self) -> str:
        return f"{self.branch}/{self.name}" if self.branch else self.name

    def __str__(self) -> str:
        return f"TestResult({self.full_name}, {self.status})"


class TestBranchError(BaseModel):
    __test__ = False
    model_config = ConfigDict(extra="forbid")

    error_code: str
    error_details: str


def _process_branch_xml(
    raw_xml: str,
    branch: str,
    tests_by_branch: dict[str, list[str]],
) -> tuple[list[TestResult], list[str]]:
    """Parse JUnit XML for a branch and validate against expected test list."""
    parsed = parse_test_results(raw_xml, branch=branch).test_results
    results: list[TestResult] = list(parsed)
    warnings: list[str] = []

    expected = tests_by_branch.get(branch)
    if expected is None:
        log.warning("Branch %s: no expected test list, cannot verify completeness", branch)
        warnings.append(f"No expected test list for branch {branch}, cannot verify completeness")
        return results, warnings

    got = {t.name for t in parsed}
    missing = [name for name in expected if name not in got]
    if missing:
        log.warning(
            "Branch %s: %d/%d expected tests missing from JUnit XML",
            branch,
            len(missing),
            len(expected),
        )
        results.extend(
            TestResult(
                name=name,
                branch=branch,
                status="not_run",
                extra={"error_code": "missing_from_junit_xml"},
            )
            for name in missing
        )
    unexpected = got - set(expected)
    if unexpected:
        log.warning(
            "Branch %s: %d test(s) in JUnit XML not in tests.json",
            branch,
            len(unexpected),
        )
        warnings.append(f"Branch {branch}: {len(unexpected)} test(s) in JUnit XML not in tests.json")

    return results, warnings


class EvaluationResult(BaseModel):
    """By default INCLUDES ALL TESTS even those ignored.
    Use the `without_ignored` method to get a copy with only the non-ignored tests.
    """

    model_config = ConfigDict(extra="forbid")

    test_results: list[TestResult] = []
    error_code: str | None = None
    error_details: str | None = None
    log: list[dict] = []
    solution_branch: str | None = None
    test_branches: list[str] = []
    test_branch_errors: dict[str, list[TestBranchError]] = {}
    executable_hash: str | None = None
    warnings: list[str] = []

    @property
    def n_system_errors(self) -> int:
        return sum(t.status == "system_error" for t in self.test_results)

    @property
    def n_resolved(self) -> int:
        return sum(test.is_resolved for test in self.test_results)

    def __len__(self) -> int:
        return len(self.test_results)

    def __iter__(self) -> Iterator[TestResult]:
        return iter(self.test_results)

    @property
    def score(self) -> float:
        if len(self) == 0:
            return 0.0
        return self.n_resolved / len(self)

    def without_ignored(self, ignored_tests: set[str]) -> "EvaluationResult":
        if not ignored_tests:
            return self
        return EvaluationResult(
            test_results=[t for t in self.test_results if t.full_name not in ignored_tests],
            error_code=self.error_code,
            error_details=self.error_details,
            log=self.log,
            solution_branch=self.solution_branch,
            test_branches=self.test_branches,
            test_branch_errors=self.test_branch_errors,
            executable_hash=self.executable_hash,
            warnings=self.warnings,
        )

    def for_branches(self, branches: list[str]) -> "EvaluationResult":
        """Return a copy scoped to the given test branches."""
        if sorted(self.test_branches) == sorted(branches):
            return self
        branch_set = set(branches)
        return EvaluationResult(
            test_results=[t for t in self.test_results if t.branch in branch_set],
            error_code=self.error_code,
            error_details=self.error_details,
            log=self.log,
            solution_branch=self.solution_branch,
            test_branches=branches,
            test_branch_errors={b: e for b, e in self.test_branch_errors.items() if b in branch_set},
            executable_hash=self.executable_hash,
            warnings=self.warnings,
        )

    def summarize(self) -> str:
        summary = f"EvaluationResult({self.solution_branch}: {self.score * 100:.0f}={self.n_resolved}/{len(self)}"
        if self.error_code is not None:
            summary += f", error_code={self.error_code}"
        if self.error_details is not None:
            summary += f", error_details={self.error_details}"
        if self.test_branch_errors:
            summary += f", branch_errors={list(self.test_branch_errors)}"
        if self.n_system_errors:
            summary += f", system_errors={self.n_system_errors}"
        if self.warnings:
            summary += f", warnings={len(self.warnings)}"
        summary += ")"
        return summary


class Evaluator:
    """Evaluate a solution by compiling it and running tests in a Docker container.

    Unzips submission.zip into the container workspace, runs compile.sh, then
    runs each test branch's suite.
    """

    _stashed_executable = "/opt/programbench-stashed-executable-do-not-modify"

    def __init__(
        self,
        *,
        tests_branches: list[str],
        tests_by_branch: dict[str, list[str]] | None = None,
        image_name: str = "",
        solution_branch: str = "",
        submission_zip: Path | None = None,
        blob_dir: Path | None = None,
        remove_hashes: list[str] | None = None,
        image_tag: str = "task",
        from_existing: EvaluationResult | None = None,
    ):
        self.image_name = image_name
        self.solution_branch = solution_branch
        self.submission_zip = submission_zip
        self.blob_dir = blob_dir
        self.tests_branches = tests_branches
        self.remove_hashes = remove_hashes or []
        self.image_tag = image_tag
        self.tests_by_branch = tests_by_branch or {}
        self._from_existing = from_existing
        if from_existing is not None:
            self._xml_by_branch: dict[str, str] = {
                entry["branch"]: entry["output"]
                for entry in from_existing.log
                if entry.get("step") == "results_read" and entry.get("returncode", -1) == 0 and "branch" in entry
            }
            self.result = EvaluationResult(
                solution_branch=from_existing.solution_branch or solution_branch,
                test_branches=tests_branches,
                log=from_existing.log,
                executable_hash=from_existing.executable_hash,
            )
        else:
            self.result = EvaluationResult(
                solution_branch=solution_branch,
                test_branches=tests_branches,
            )

    def _add_branch_error(self, branch: str, error_code: str, error_details: str = "") -> None:
        self.result.test_branch_errors.setdefault(branch, []).append(
            TestBranchError(error_code=error_code, error_details=error_details)
        )

    def _inject_not_run(self, branch: str, error_code: str) -> None:
        tests = self.tests_by_branch.get(branch, [])
        if not tests:
            msg = f"No expected test list for branch {branch}, cannot inject not_run results"
            log.warning(msg)
            if branch not in self.result.test_branch_errors:
                self._add_branch_error(branch, "no_expected_test_list", msg)
            return
        self.result.test_results.extend(
            TestResult(
                name=name,
                branch=branch,
                status="not_run",
                extra={"error_code": error_code},
            )
            for name in tests
        )

    def _run_step(
        self,
        command: str,
        *,
        step_name: str,
        accept_failure: bool = False,
        timeout: int = 20,
    ) -> dict:
        log.debug("Running step: %s", command)
        t0 = time.monotonic()
        r = self.env.execute(command, timeout=timeout)
        wall_time = time.monotonic() - t0
        self.result.log.append({"step": step_name, "command": command, "wall_time": wall_time, **r})
        if r["returncode"] != 0:
            error_code = f"{step_name}_failed"
            if accept_failure:
                log.debug(
                    "%s (exit %d, accepted): %s",
                    error_code,
                    r["returncode"],
                    r["output"],
                )
            else:
                log.debug("%s (exit %d): %s", error_code, r["returncode"], r["output"])
                raise EvalStepError(error_code, r["output"].strip())
        else:
            log.debug("Output: %s", r["output"])
        return r

    def _remove_hashed_files(self) -> None:
        if not self.remove_hashes:
            return
        hashes_pattern = "|".join(self.remove_hashes)
        self._run_step(
            f"find {WORKSPACE_DIR} -type f -exec sha256sum {{}} + 2>/dev/null"
            f' | grep -E "^({hashes_pattern})  " | cut -c67- | xargs -I% rm -fv %',
            step_name="remove_hashed_files",
            accept_failure=True,
        )

    def _compile_executable(self) -> None:
        """Wipe workspace, copy in unzipped submission, run compile.sh."""
        import os
        import tempfile
        import zipfile

        self._run_step(
            f"rm -rf {WORKSPACE_DIR}/* {WORKSPACE_DIR}/.[!.]*",
            step_name="wipe_workspace",
        )
        assert self.submission_zip is not None
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            with zipfile.ZipFile(self.submission_zip) as zf:
                for info in zf.infolist():
                    extracted = zf.extract(info, tmp_path)
                    mode = (info.external_attr >> 16) & 0o7777
                    if mode and not info.is_dir():
                        os.chmod(extracted, mode)
            self.env.copy_in(tmp_path, f"{WORKSPACE_DIR}/")
        self._remove_hashed_files()
        # Seed a synthetic git repo if the submission didn't ship one. Build
        # scripts that depend on a working tree (jq submodules, calcurse's
        # autopoint, cargo+vergen, ...) succeed against this synthetic repo.
        # The legacy RevEngBench gold pipeline got this for free via
        # `git clone <upstream>`; here we approximate it locally.
        self._run_step(
            "if [ ! -d .git ]; then "
            "git -c init.defaultBranch=gold init -q && "
            "git -c user.email=gold@local -c user.name=gold "
            "-c commit.gpgsign=false add -A && "
            "git -c user.email=gold@local -c user.name=gold "
            "-c commit.gpgsign=false commit -q --allow-empty -m gold; "
            "fi",
            step_name="seed_git",
        )
        self._run_step(
            "chmod +x ./compile.sh && ./compile.sh",
            step_name="compile",
            timeout=900,
        )
        self._run_step(
            f"ls && cp ./executable {self._stashed_executable}",
            step_name="copy_executable",
        )
        r = self._run_step(f"sha256sum {self._stashed_executable}", step_name="hash_executable")
        self.result.executable_hash = r["output"].split()[0]

    def _restore_executable(self) -> None:
        if self.result.executable_hash is None:
            raise EvalStepError("no_executable_hash", "Executable hash not found")
        self._run_step(
            f"rm -f ./executable && cp {self._stashed_executable} ./executable && chmod +x ./executable",
            step_name="restore_executable",
        )
        r = self._run_step(f"sha256sum {self._stashed_executable}", step_name="verify_executable_hash")
        current_hash = r["output"].split()[0]
        if current_hash != self.result.executable_hash:
            raise EvalStepError(
                "executable_hash_mismatch",
                f"expected {self.result.executable_hash}, got {current_hash}",
            )

    def _get_xml_from_log(self, branch: str) -> str:
        if branch in self._xml_by_branch:
            return self._xml_by_branch[branch]
        errors = self._from_existing.test_branch_errors.get(branch, [])
        if errors:
            self.result.test_branch_errors[branch] = list(errors)
        raise EvalStepError(
            errors[0].error_code if errors else "no_results_in_log",
            errors[0].error_details if errors else "",
        )

    def _run_test_branch(self, branch: str) -> str:
        """Wipe workspace, inject test files, restore executable, run tests, return XML."""
        if self._from_existing is not None:
            return self._get_xml_from_log(branch)

        self._run_step(
            "pkill -9 -f 'pytest|execnet' 2>/dev/null; pkill -9 -x executable 2>/dev/null; pkill -9 -x git 2>/dev/null",
            step_name="reap_stray_processes",
            accept_failure=True,
        )
        self._run_step(
            f"rm -rf {WORKSPACE_DIR}/* {WORKSPACE_DIR}/.[!.]*",
            step_name="wipe_workspace_for_tests",
        )
        assert self.blob_dir is not None
        test_dir = self.blob_dir / "tests" / branch
        self.env.copy_in(test_dir, f"{WORKSPACE_DIR}/")
        self._restore_executable()
        self._run_step("rm -f eval/results.xml results.xml", step_name="clean_stale_results")
        self._run_step(
            "chmod +x ./eval/run.sh && ./eval/run.sh",
            step_name="run_tests",
            accept_failure=True,
            timeout=2400,
        )
        r = self._run_step("cat eval/results.xml", step_name="results_read", timeout=60)
        self.result.log[-1]["branch"] = branch
        return r["output"]

    def run(self) -> EvaluationResult:
        """Run the full evaluation pipeline."""
        if self._from_existing is None:
            self.env = ContainerEnvironment(
                image=f"{self.image_name}:{self.image_tag}",
                cwd=WORKSPACE_DIR,
                executable=DOCKER_EXECUTABLE,
                timeout=600,
                run_args=[*DOCKER_RUN_ARGS, "--init"],
            )

            try:
                self._compile_executable()
            except EvalStepError as e:
                self.result.error_code = e.error_code
                self.result.error_details = e.error_details
                for branch in self.tests_branches:
                    self._inject_not_run(branch, e.error_code)
                log.debug(self.result.summarize())
                return self.result
        elif self._from_existing.error_code:
            self.result.error_code = self._from_existing.error_code
            self.result.error_details = self._from_existing.error_details
            for branch in self.tests_branches:
                self._inject_not_run(branch, self._from_existing.error_code)
            log.debug(self.result.summarize())
            return self.result

        for branch in self.tests_branches:
            try:
                raw_xml = self._run_test_branch(branch)
            except EvalStepError as e:
                log.warning(
                    "Branch %s failed (%s), continuing with remaining branches",
                    branch,
                    e.error_code,
                )
                if branch not in self.result.test_branch_errors:
                    self._add_branch_error(branch, e.error_code, e.error_details)
                self._inject_not_run(branch, e.error_code)
                continue
            results, warnings = _process_branch_xml(raw_xml, branch, self.tests_by_branch)
            self.result.test_results.extend(results)
            self.result.warnings.extend(warnings)

        log.debug(self.result.summarize())
        return self.result


def parse_test_results(results_xml: str, branch: str = "") -> EvaluationResult:
    """Parse JUnit XML test results into an EvaluationResult."""
    if not results_xml.strip():
        raise EmptyTestResultError(f"Empty test results XML for branch {branch!r}")
    try:
        root = ET.fromstring(results_xml)
    except ET.ParseError as e:
        raise XmlParseError(
            f"Malformed test results XML for branch {branch!r}: {e} "
            f"(len={len(results_xml)}, tail={results_xml[-200:]!r})"
        ) from e
    xml = JUnitXml.fromroot(root)

    test_results = []
    for suite in xml:
        for case in suite:
            raw_name = f"{case.classname}.{case.name}" if case.classname else case.name
            if not raw_name:
                log.warning(
                    "Skipping testcase with null name in JUnit XML (classname=%r)",
                    case.classname,
                )
                continue
            name = raw_name
            extra: dict = {}
            if case.time is not None:
                extra["time"] = case.time

            results = case.result
            if not results:
                status = "passed"
            elif len(results) != 1:
                status = "system_error"
                extra["error_details"] = f"Expected 1 result for {name}, got {len(results)}: {results}"
            else:
                result = results[0]
                if isinstance(result, Skipped):
                    status = "skipped"
                elif isinstance(result, Failure):
                    status = "failure"
                elif isinstance(result, Error):
                    status = "error"
                else:
                    status = "system_error"
                    extra["error_details"] = f"Unknown result type for {name}: {type(result).__name__}"
                if hasattr(result, "message") and result.message:
                    extra["message"] = result.message
                if hasattr(result, "text") and result.text:
                    extra["text"] = result.text

            test_results.append(TestResult(name=name, branch=branch, status=status, extra=extra))

    return EvaluationResult(test_results=test_results)
