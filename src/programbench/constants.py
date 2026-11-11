# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the license found in the
# LICENSE file in the root directory of this source tree.

import os
from pathlib import Path

DOCKER_EXECUTABLE = os.environ.get("PROGRAMBENCH_DOCKER_EXECUTABLE", "docker")
DOCKER_CPUS = int(os.environ.get("PROGRAMBENCH_DOCKER_CPUS", "10"))
DOCKER_RUN_ARGS: list[str] = []
# Timeouts (seconds) for blocking docker subcommands. Pulls + container start
# can be slow under parallelism, so defaults are generous.
DOCKER_RUN_TIMEOUT = int(os.environ.get("PROGRAMBENCH_DOCKER_RUN_TIMEOUT", "300"))
DOCKER_CP_TIMEOUT = int(os.environ.get("PROGRAMBENCH_DOCKER_CP_TIMEOUT", "300"))

PACKAGE_ROOT = Path(__file__).resolve().parent
TASKS_DIR = PACKAGE_ROOT / "data" / "tasks"

DOCKER_ORG = os.environ.get("PROGRAMBENCH_DOCKER_ORG", "programbench")

TASK_YAML = "task.yaml"
TESTS_JSON = "tests.json"
WORKSPACE_DIR = "/workspace"
PIP_CONSTRAINTS_PATH = "/etc/programbench/pip-constraints.txt"
# Seconds compile.sh may run. A task can raise it with `compile_timeout` in its task.yaml.
DEFAULT_COMPILE_TIMEOUT = 900

HF_REPO_ID = os.environ.get("PROGRAMBENCH_HF_REPO", "programbench/ProgramBench-Tests")
HF_REVISION = os.environ.get("PROGRAMBENCH_HF_REVISION", "main")


def image_name_from_instance_id(instance_id: str) -> str:
    return f"{DOCKER_ORG}/{instance_id.replace('__', '_1776_')}"
