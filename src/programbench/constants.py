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

HF_REPO_ID = os.environ.get("PROGRAMBENCH_HF_REPO", "programbench/data")
HF_REVISION = os.environ.get("PROGRAMBENCH_HF_REVISION", "")


def image_name_from_instance_id(instance_id: str) -> str:
    return f"{DOCKER_ORG}/{instance_id.replace('__', '_1776_')}"
