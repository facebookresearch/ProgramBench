# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the license found in the
# LICENSE file in the root directory of this source tree.

"""Smoke tests for CLI subcommands."""

from typer.testing import CliRunner

from programbench.cli.main import app

runner = CliRunner()


def test_top_level_help():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "eval" in result.output
    assert "blob" in result.output
    assert "info" in result.output


def test_info_help():
    result = runner.invoke(app, ["info", "--help"])
    assert result.exit_code == 0
    assert "run-dir" in result.output.lower() or "run_dir" in result.output.lower()


def test_blob_help():
    result = runner.invoke(app, ["blob", "--help"])
    assert result.exit_code == 0
    assert "sync" in result.output


def test_blob_sync_help():
    result = runner.invoke(app, ["blob", "sync", "--help"])
    assert result.exit_code == 0
    assert "instance" in result.output.lower()


def test_submit_help():
    result = runner.invoke(app, ["submit", "--help"])
    assert result.exit_code == 0
    assert all(cmd in result.output for cmd in ("package", "publish", "verify", "register", "recombine"))


def test_submit_package_help():
    result = runner.invoke(app, ["submit", "package", "--help"])
    assert result.exit_code == 0
    assert "upload" in result.output.lower()


def test_submit_register_help():
    result = runner.invoke(app, ["submit", "register", "--help"])
    assert result.exit_code == 0
    assert "registry" in result.output.lower()


def test_submit_publish_help():
    result = runner.invoke(app, ["submit", "publish", "--help"])
    assert result.exit_code == 0
    assert "owner" in result.output.lower()
