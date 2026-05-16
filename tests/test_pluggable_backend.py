# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the license found in the
# LICENSE file in the root directory of this source tree.

"""Tests for the pluggable ContainerBackend / Environment abstraction.

The point of this PR is that downstream callers shouldn't have to monkey-
patch internals to run the eval against a non-Docker isolation primitive
(e.g. Daytona sandboxes, gVisor, firecracker, or just a bare host shell).
These tests exercise the substitution: build a fake in-memory backend,
pass it to the Evaluator, and verify the eval calls the fake's methods
instead of shelling Docker.
"""

from pathlib import Path

from programbench.container import ContainerBackend, DockerBackend
from programbench.eval.eval import Evaluator


class FakeEnv:
    """Records every method call so the test can assert the surface."""

    def __init__(self, *, image, cwd, timeout, cpus, env, run_args):
        self.image = image
        self.cwd = cwd
        self.default_timeout = timeout
        self.cpus = cpus
        self.env = env
        self.run_args = run_args
        self.calls: list[tuple] = []
        self.canned_copy_out: dict[str, str] = {}

    def execute(self, command, *, timeout=None):
        self.calls.append(("execute", command))
        return {"output": "", "returncode": 0, "exception_info": ""}

    def copy_in(self, local_path, container_path):
        self.calls.append(("copy_in", str(local_path), container_path))

    def copy_in_tar(self, tar_path, container_path):
        self.calls.append(("copy_in_tar", str(tar_path), container_path))

    def copy_out(self, container_path, *, timeout=60):
        self.calls.append(("copy_out", container_path))
        contents = self.canned_copy_out.get(container_path, "")
        return contents, f"fake-cp {container_path}"

    def commit(self, image_ref):
        self.calls.append(("commit", image_ref))
        return image_ref

    def cleanup(self):
        self.calls.append(("cleanup",))


class FakeBackend:
    """Hands out FakeEnv instances and records image removals."""

    def __init__(self):
        self.envs: list[FakeEnv] = []
        self.removed: list[str] = []

    def new_env(self, image, *, cwd, timeout, cpus, env, run_args):
        env_obj = FakeEnv(image=image, cwd=cwd, timeout=timeout,
                          cpus=cpus, env=env, run_args=run_args)
        self.envs.append(env_obj)
        return env_obj

    def remove_image(self, image_ref):
        self.removed.append(image_ref)


def test_evaluator_uses_injected_backend():
    """Constructing an Evaluator with a custom backend routes all container
    operations through it — _new_env calls backend.new_env, and
    _copy_file_from_container calls env.copy_out."""
    backend = FakeBackend()
    e = Evaluator(
        tests_branches=["abc123"],
        image_name="example/example.deadbeef",
        backend=backend,
    )

    env = e._new_env("example/example.deadbeef:task")
    assert isinstance(env, FakeEnv)
    assert env.image == "example/example.deadbeef:task"
    assert env.cwd  # WORKSPACE_DIR
    assert len(backend.envs) == 1

    log_buf: list[dict] = []
    env.canned_copy_out["/workspace/eval/results.xml"] = "<xml/>"
    contents = e._copy_file_from_container(
        env=env, log_buf=log_buf,
        container_path="/workspace/eval/results.xml",
        step_name="results_read",
    )
    assert contents == "<xml/>"
    assert ("copy_out", "/workspace/eval/results.xml") in env.calls
    # Step was logged with the env's reported command-string, not "docker cp".
    assert log_buf[0]["step"] == "results_read"
    assert log_buf[0]["command"].startswith("fake-cp ")


def test_evaluator_default_backend_is_docker():
    """No `backend=` kwarg → DockerBackend (preserves pre-PR behavior)."""
    e = Evaluator(tests_branches=[], image_name="foo")
    assert isinstance(e.backend, DockerBackend)


def test_docker_backend_satisfies_protocol():
    """DockerBackend implements the ContainerBackend protocol surface."""
    backend: ContainerBackend = DockerBackend()
    # Protocol membership is structural — this assignment compiles only
    # because DockerBackend has the required methods. Smoke-touch them.
    assert callable(backend.new_env)
    assert callable(backend.remove_image)
