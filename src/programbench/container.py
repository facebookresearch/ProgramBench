import logging
import subprocess
import uuid
from pathlib import Path
from typing import Any

from programbench.constants import DOCKER_CP_TIMEOUT, DOCKER_RUN_TIMEOUT

log = logging.getLogger(__name__)


class ContainerEnvironment:
    """Manage a long-running container for command execution and file injection."""

    def __init__(
        self,
        *,
        image: str,
        cwd: str = "/",
        executable: str = "docker",
        timeout: int = 30,
        cpus: int = 10,
        env: dict[str, str] | None = None,
        run_args: list[str] | None = None,
    ):
        self.cwd = cwd
        self.executable = executable
        self.default_timeout = timeout
        self.cpus = cpus
        self._name = f"programbench-{uuid.uuid4().hex[:12]}"
        run_args = list(run_args or [])
        env_dict = {"PYTEST_XDIST_AUTO_NUM_WORKERS": str(cpus), **(env or {})}
        env_args: list[str] = []
        for key, value in env_dict.items():
            env_args.extend(["-e", f"{key}={value}"])
        cmd = [
            executable,
            "run",
            "-d",
            "--init",
            "--name",
            self._name,
            "-w",
            cwd,
            "--cpus",
            str(cpus),
            *env_args,
            *run_args,
            image,
            "sleep",
            "2h",
        ]
        log.debug("Starting container: %s", " ".join(cmd))
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=DOCKER_RUN_TIMEOUT)
        if result.returncode != 0:
            raise RuntimeError(f"Failed to start container: {result.stderr.strip()}")
        self.container_id = result.stdout.strip()

    def execute(self, command: str, *, timeout: int | None = None) -> dict[str, Any]:
        """Run a shell command inside the container."""
        timeout = timeout or self.default_timeout
        cmd = [
            self.executable,
            "exec",
            "-w",
            self.cwd,
            self.container_id,
            "bash",
            "-lc",
            command,
        ]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            output = result.stdout + result.stderr
            return {
                "output": output,
                "returncode": result.returncode,
                "exception_info": "",
            }
        except subprocess.TimeoutExpired:
            return {
                "output": "",
                "returncode": -1,
                "exception_info": f"Command timed out after {timeout}s",
            }

    def copy_in(self, local_path: Path, container_path: str) -> None:
        """Copy a local file or directory into the container."""
        if local_path.is_dir():
            src = f"{local_path}/."
        else:
            src = str(local_path)
        cmd = [self.executable, "cp", src, f"{self.container_id}:{container_path}"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=DOCKER_CP_TIMEOUT)
        if result.returncode != 0:
            raise RuntimeError(f"docker cp failed: {result.stderr.strip()}")

    def commit(self, image_ref: str) -> str:
        """Commit the current container state to a new image and return its ref."""
        cmd = [self.executable, "commit", self.container_id, image_ref]
        log.debug("Committing container: %s", " ".join(cmd))
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=DOCKER_RUN_TIMEOUT)
        if result.returncode != 0:
            raise RuntimeError(f"docker commit failed: {result.stderr.strip()}")
        return image_ref

    def cleanup(self) -> None:
        """Stop and remove the container."""
        for action in ("stop", "rm -f"):
            try:
                subprocess.run(
                    [self.executable, *action.split(), self.container_id],
                    capture_output=True,
                    timeout=30,
                )
            except Exception:
                pass

    def __del__(self) -> None:
        self.cleanup()


def remove_image(image_ref: str, *, executable: str = "docker") -> None:
    """Best-effort image removal."""
    try:
        subprocess.run(
            [executable, "rmi", "-f", image_ref],
            capture_output=True,
            timeout=60,
        )
    except Exception:
        pass
