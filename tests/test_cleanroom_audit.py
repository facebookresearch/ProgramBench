# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the license found in the
# LICENSE file in the root directory of this source tree.

from __future__ import annotations

import gzip
import io
import tarfile

from typer.testing import CliRunner

from programbench.cli.main import app
from programbench.utils.cleanroom_audit import (
    apply_tar_layers,
    audit_entries,
    parse_image_ref,
)


runner = CliRunner()


def _layer(entries: list[tuple[str, bytes | None]]) -> io.BytesIO:
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as tar:
        for name, content in entries:
            info = tarfile.TarInfo(name)
            if content is None:
                info.type = tarfile.DIRTYPE
                info.mode = 0o755
                tar.addfile(info)
            else:
                info.size = len(content)
                info.mode = 0o644
                tar.addfile(info, io.BytesIO(content))

    compressed = io.BytesIO()
    with gzip.GzipFile(fileobj=compressed, mode="wb") as gz:
        gz.write(raw.getvalue())
    compressed.seek(0)
    return compressed


def test_audit_detects_doc_directory_whiteout() -> None:
    base = _layer(
        [
            ("workspace/", None),
            ("workspace/docs/", None),
            ("workspace/docs/usage.md", b"usage"),
            ("workspace/README.md", b"readme"),
            ("workspace/LICENSE", b"license"),
        ]
    )
    cleanroom = _layer([("workspace/.wh.docs", b"")])

    tree = apply_tar_layers(
        [
            (base, "application/vnd.docker.image.rootfs.diff.tar.gzip"),
            (cleanroom, "application/vnd.oci.image.layer.v1.tar+gzip"),
        ]
    )
    audit = audit_entries("programbench/example:task_cleanroom", tree)

    assert not audit.ok
    assert audit.doc_entries == ()
    assert audit.readme_entries == ("README.md",)
    assert audit.license_entries == ("LICENSE",)
    assert len(audit.doc_whiteouts) == 1
    assert audit.doc_whiteouts[0].target == "workspace/docs"
    assert audit.doc_whiteouts[0].removed_paths == 2


def test_audit_passes_when_docs_remain() -> None:
    base = _layer(
        [
            ("workspace/", None),
            ("workspace/docs/", None),
            ("workspace/docs/usage.md", b"usage"),
            ("workspace/executable", b"#!/bin/sh\n"),
        ]
    )

    tree = apply_tar_layers([(base, "application/vnd.docker.image.rootfs.diff.tar.gzip")])
    audit = audit_entries("programbench/example:task_cleanroom", tree)

    assert audit.ok
    assert audit.doc_entries == ("docs/usage.md",)


def test_audit_fails_for_empty_doc_directory() -> None:
    base = _layer(
        [
            ("workspace/", None),
            ("workspace/docs/", None),
            ("workspace/executable", b"#!/bin/sh\n"),
        ]
    )

    tree = apply_tar_layers([(base, "application/vnd.docker.image.rootfs.diff.tar.gzip")])
    audit = audit_entries("programbench/example:task_cleanroom", tree)

    assert not audit.ok
    assert audit.doc_entries == ()


def test_parse_image_ref_keeps_explicit_tag() -> None:
    ref = parse_image_ref("programbench/zk-org_1776_zk.10d93d5:task_cleanroom")

    assert ref.repository == "programbench/zk-org_1776_zk.10d93d5"
    assert ref.tag == "task_cleanroom"


def test_audit_cleanroom_docs_cli_reports_failure(monkeypatch) -> None:
    seen_images = []

    def fake_audit(image: str, *, timeout: float):
        seen_images.append(image)
        tree = apply_tar_layers(
            [
                (
                    _layer(
                        [
                            ("workspace/", None),
                            ("workspace/docs/", None),
                            ("workspace/docs/usage.md", b"usage"),
                            ("workspace/.wh.docs", b""),
                        ]
                    ),
                    "application/vnd.docker.image.rootfs.diff.tar.gzip",
                )
            ]
        )
        return audit_entries(image, tree)

    monkeypatch.setattr("programbench.utils.cleanroom_audit.audit_cleanroom_image", fake_audit)

    result = runner.invoke(app, ["audit-cleanroom-docs", "programbench/example:task_cleanroom"])

    assert result.exit_code == 1
    assert seen_images == ["programbench/example:task_cleanroom"]
    assert "missing-docs" in result.output
    assert "doc whiteout" in result.output


def test_audit_cleanroom_docs_cli_accepts_instance_ids(monkeypatch) -> None:
    seen_images = []

    def fake_audit(image: str, *, timeout: float):
        seen_images.append(image)
        tree = apply_tar_layers(
            [
                (
                    _layer(
                        [
                            ("workspace/", None),
                            ("workspace/docs/", None),
                            ("workspace/docs/usage.md", b"usage"),
                        ]
                    ),
                    "application/vnd.docker.image.rootfs.diff.tar.gzip",
                )
            ]
        )
        return audit_entries(image, tree)

    monkeypatch.setattr("programbench.utils.cleanroom_audit.audit_cleanroom_image", fake_audit)

    result = runner.invoke(app, ["audit-cleanroom-docs", "zk-org__zk.10d93d5"])

    assert result.exit_code == 0
    assert seen_images == ["programbench/zk-org_1776_zk.10d93d5:task_cleanroom"]
    assert "ok" in result.output
