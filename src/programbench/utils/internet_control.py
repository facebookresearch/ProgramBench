# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the license found in the
# LICENSE file in the root directory of this source tree.

"""Block internet for the eval build script via an in-container DNS blackhole.

A submission's ``compile.sh`` runs as root inside the build container. Without
isolation it could smuggle ``pip install`` / download steps into the build,
which the eval is meant to forbid. We block from *inside* the container by
overwriting ``/etc/resolv.conf`` with an unroutable nameserver, so hostname
resolution (pip/cargo/npm/go/apt/git-over-https/...) fails. This needs no host
privileges and behaves identically locally and under docker-in-docker.

Accepted trade-offs for the build threat model: it does not block raw-IP
connections, and a root process inside the container could rewrite resolv.conf
to undo it. Test-execution containers are left untouched (they may legitimately
need network).
"""

from programbench.container import ContainerEnvironment

_RESOLV_CONF = "/etc/resolv.conf"
_RESOLV_BACKUP = "/etc/resolv.conf.programbench-build-bak"
_BLACKHOLE_NS = "nameserver 0.0.0.0"


def block_build_internet_dns(env: ContainerEnvironment) -> None:
    """Blackhole DNS inside the container so the build script can't download.

    Backs up ``/etc/resolv.conf`` and replaces it with an unroutable nameserver.
    Restore with :func:`restore_build_internet_dns`. Raises if the rewrite did
    not take effect, so callers never run a build believing internet is blocked
    when it isn't.
    """
    r = env.execute(
        f"cp -f {_RESOLV_CONF} {_RESOLV_BACKUP} && "
        f"printf '%s\\n' '{_BLACKHOLE_NS}' > {_RESOLV_CONF} && "
        f"cat {_RESOLV_CONF}",
        timeout=20,
    )
    if r["returncode"] != 0 or _BLACKHOLE_NS not in r["output"]:
        detail = (r["output"] or r["exception_info"] or "").strip()
        raise RuntimeError(f"Failed to blackhole DNS for build isolation: {detail}")


def restore_build_internet_dns(env: ContainerEnvironment) -> None:
    """Restore ``/etc/resolv.conf`` from the backup taken by the block call.

    Best-effort and idempotent: if the backup is missing (block never ran or was
    already restored), this is a no-op, so it's safe to call unconditionally from
    a ``finally`` block.
    """
    env.execute(
        f"if [ -f {_RESOLV_BACKUP} ]; then cat {_RESOLV_BACKUP} > {_RESOLV_CONF} && rm -f {_RESOLV_BACKUP}; fi",
        timeout=20,
    )
