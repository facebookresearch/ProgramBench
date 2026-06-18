# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the license found in the
# LICENSE file in the root directory of this source tree.

"""Tests for the build-time DNS-blackhole internet block."""

from unittest import mock

import pytest

from programbench.eval.eval import Evaluator
from programbench.utils.internet_control import (
    _BLACKHOLE_NS,
    block_build_internet_dns,
    restore_build_internet_dns,
)


class FakeEnv:
    """Records executed commands and replays canned responses."""

    def __init__(self, responses: list[dict] | None = None):
        self.commands: list[str] = []
        self._responses = list(responses or [])

    def execute(self, command: str, *, timeout: int | None = None) -> dict:
        self.commands.append(command)
        if self._responses:
            return self._responses.pop(0)
        return {"output": "", "returncode": 0, "exception_info": ""}


class TestBlockBuildInternetDns:
    def test_block_writes_blackhole_and_backs_up(self):
        env = FakeEnv([{"output": f"{_BLACKHOLE_NS}\n", "returncode": 0, "exception_info": ""}])
        block_build_internet_dns(env)
        cmd = env.commands[0]
        assert "/etc/resolv.conf.programbench-build-bak" in cmd
        assert _BLACKHOLE_NS in cmd
        assert "/etc/resolv.conf" in cmd

    def test_block_raises_when_write_not_confirmed(self):
        env = FakeEnv([{"output": "something else", "returncode": 0, "exception_info": ""}])
        with pytest.raises(RuntimeError, match="blackhole DNS"):
            block_build_internet_dns(env)

    def test_block_raises_on_nonzero_returncode(self):
        env = FakeEnv([{"output": f"{_BLACKHOLE_NS}\n", "returncode": 1, "exception_info": ""}])
        with pytest.raises(RuntimeError, match="blackhole DNS"):
            block_build_internet_dns(env)

    def test_restore_copies_backup_back(self):
        env = FakeEnv()
        restore_build_internet_dns(env)
        cmd = env.commands[0]
        assert "/etc/resolv.conf.programbench-build-bak" in cmd
        assert "rm -f" in cmd


class TestEvaluatorBuildInternetWiring:
    def _make_evaluator(self, tmp_path) -> Evaluator:
        archive = tmp_path / "submission.tar.gz"
        archive.write_bytes(b"")
        ev = Evaluator(tests_branches=[], submission_archive=archive)
        ev._remove_hashed_files = lambda env, log_buf: None  # type: ignore[method-assign]
        return ev

    def test_block_restore_wrap_compile(self, tmp_path):
        ev = self._make_evaluator(tmp_path)
        env = FakeEnv()
        env.copy_in_tar = lambda *a, **k: None  # type: ignore[attr-defined]
        calls: list[str] = []
        with (
            mock.patch(
                "programbench.eval.eval.block_build_internet_dns",
                side_effect=lambda e: calls.append("block"),
            ),
            mock.patch(
                "programbench.eval.eval.restore_build_internet_dns",
                side_effect=lambda e: calls.append("restore"),
            ),
            mock.patch.object(
                ev,
                "_run_step",
                side_effect=lambda *a, **k: calls.append(k["step_name"]) or {"output": "h x", "returncode": 0},
            ),
        ):
            ev._compile_executable(env, [])
        assert calls.index("block") < calls.index("compile") < calls.index("restore")

    def test_restore_runs_even_when_compile_fails(self, tmp_path):
        from programbench.exceptions import EvalStepError

        ev = self._make_evaluator(tmp_path)
        env = FakeEnv()
        env.copy_in_tar = lambda *a, **k: None  # type: ignore[attr-defined]
        calls: list[str] = []

        def fake_run_step(*a, **k):
            calls.append(k["step_name"])
            if k["step_name"] == "compile":
                raise EvalStepError("compile_failed", "boom")
            return {"output": "h x", "returncode": 0}

        with (
            mock.patch("programbench.eval.eval.block_build_internet_dns", side_effect=lambda e: calls.append("block")),
            mock.patch(
                "programbench.eval.eval.restore_build_internet_dns", side_effect=lambda e: calls.append("restore")
            ),
            mock.patch.object(ev, "_run_step", side_effect=fake_run_step),
            pytest.raises(EvalStepError),
        ):
            ev._compile_executable(env, [])
        assert "restore" in calls
        assert calls.index("block") < calls.index("restore")
