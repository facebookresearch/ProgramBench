import os
from pathlib import Path

DOCKER_EXECUTABLE = os.environ.get("PROGRAMBENCH_DOCKER_EXECUTABLE", "docker")
DOCKER_RUN_ARGS = ["--cpus", os.environ.get("PROGRAMBENCH_DOCKER_CPUS", "10")]

PACKAGE_ROOT = Path(__file__).resolve().parent
TASKS_DIR = PACKAGE_ROOT / "data" / "tasks"
DEFAULT_GOLD_EVAL_DIR = Path("output") / "gold"

DOCKER_ORG = "programbench"

TASK_YAML = "task.yaml"
TESTS_JSON = "tests.json"
BUILD_SH = "build.sh"
WORKSPACE_DIR = "/workspace"

HF_REPO_ID = os.environ.get("PROGRAMBENCH_HF_REPO", "programbench/data")
HF_REVISION = ""


def image_name_from_instance_id(instance_id: str) -> str:
    return f"{DOCKER_ORG}/{instance_id.replace('__', '_1776_')}"
