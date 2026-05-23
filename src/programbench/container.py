# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the license found in the
# LICENSE file in the root directory of this source tree.

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
            # When subprocess.run hits its host-side timeout, Python SIGKILLs
            # the local `docker exec` CLI. The docker daemon does not
            # propagate that into the container (only SIGINT/SIGTERM
            # propagate gracefully), so the process spawned inside the
            # container keeps running. With sleep-as-PID-1 cleanrooms it
            # never gets reaped and competes with the next docker exec call.
            #
            # Sweep the in-container processes by SIGKILL-ing every non-
            # PID-1 process. Safe here because the cleanroom's only PID-1
            # process is the long-lived `sleep` that keeps the container
            # alive; everything else is the test subprocess tree we just
            # spawned. Bounded with a short timeout so a stuck daemon
            # cannot wedge us a second time.
            try:
                subprocess.run(
                    [
                        self.executable,
                        "exec",
                        self.container_id,
                        "bash",
                        "-c",
                        "kill -KILL -1 2>/dev/null; sleep 0.2; kill -KILL -1 2>/dev/null; true",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
            except Exception as e:
                log.warning(
                    "in-container teardown after execute() timeout failed: %s", e
                )
            return {
                "output": "",
                "returncode": -1,
                "exception_info": f"Command timed out after {timeout}s",
            }

    def copy_in(self, local_path: Path, container_path: str) -> None:
        """Copy a local file or directory tree into the container.

        Directories are streamed via ``tar | docker exec -i tar -xf -`` so
        unusual entries survive: escape symlinks (skeema's
        cfgsymlinks/invalidrel/.skeema → ../../../etc/bashrc fixture),
        modes, hardlinks, sticky bits. The docker daemon validates symlinks
        in both ``docker cp <dir>`` and ``docker cp - <c>:<dest>`` (stream
        mode); routing through the container's own tar bypasses that check.
        """
        if local_path.is_dir():
            self._stream_tar_in(
                container_path,
                producer=["tar", "-C", str(local_path), "-cf", "-", "."],
            )
            return
        cmd = [self.executable, "cp", str(local_path), f"{self.container_id}:{container_path}"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=DOCKER_CP_TIMEOUT)
        if result.returncode != 0:
            raise RuntimeError(f"docker cp failed: {result.stderr.strip()}")

    def copy_in_tar(self, tar_path: Path, container_path: str) -> None:
        """Stream an on-disk tar(.gz) into the container via the container's tar.

        Avoids host-side extract entirely. Same symlink/mode-preserving
        behaviour as ``copy_in`` for directories. Lets the container's tar
        decompress (passes ``-z`` for ``.tar.gz`` / ``.tgz``).
        """
        is_gz = tar_path.name.endswith((".tar.gz", ".tgz")) or tar_path.suffix == ".gz"
        with tar_path.open("rb") as f:
            self._stream_tar_in(container_path, stdin_stream=f, compressed=is_gz)

    def _stream_tar_in(
        self,
        container_path: str,
        *,
        producer: list[str] | None = None,
        stdin_stream=None,
        compressed: bool = False,
    ) -> None:
        """Pipe a tar stream into ``docker exec -i <c> tar -xf - -C <dest>``.

        Either ``producer`` (a command spawned with stdout=PIPE) or
        ``stdin_stream`` (an open file-like) provides the tar bytes. Bypasses
        ``docker cp``'s symlink validation by letting the container's tar
        unpack the stream itself. Set ``compressed=True`` for gzip input.
        """
        mk = subprocess.run(
            [self.executable, "exec", self.container_id, "mkdir", "-p", container_path],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if mk.returncode != 0:
            raise RuntimeError(f"mkdir -p {container_path} in container failed: {mk.stderr.strip()}")

        tar_flags = "-xzf" if compressed else "-xf"
        cmd = [self.executable, "exec", "-i", self.container_id, "tar", "-C", container_path, tar_flags, "-"]
        if producer is not None:
            tar = subprocess.Popen(producer, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                cp = subprocess.run(cmd, stdin=tar.stdout, capture_output=True, text=True, timeout=DOCKER_CP_TIMEOUT)
            finally:
                tar.stdout.close()
                tar_rc = tar.wait()
                tar_err = tar.stderr.read().decode(errors="replace") if tar.stderr else ""
            if tar_rc != 0 or cp.returncode != 0:
                raise RuntimeError(
                    f"tar stream into container failed (producer rc={tar_rc}, exec rc={cp.returncode}): "
                    f"producer stderr={tar_err.strip()!r}; exec stderr={cp.stderr.strip()!r}"
                )
        else:
            cp = subprocess.run(cmd, stdin=stdin_stream, capture_output=True, text=True, timeout=DOCKER_CP_TIMEOUT)
            if cp.returncode != 0:
                raise RuntimeError(f"tar stream into container failed: {cp.stderr.strip()}")

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
