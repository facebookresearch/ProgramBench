# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the license found in the
# LICENSE file in the root directory of this source tree.

"""Provide the pytest plugin injected into test containers (timeout re-arm on reruns, cwd restore after each test body)."""

PYTEST_TIMEOUT_RETRY_PLUGIN_MODULE = "programbench_pytest_timeout"
PYTEST_TIMEOUT_RETRY_PLUGIN_DIR = "/tmp/programbench-grader-plugins"
PYTEST_TIMEOUT_RETRY_PLUGIN_PATH = f"{PYTEST_TIMEOUT_RETRY_PLUGIN_DIR}/{PYTEST_TIMEOUT_RETRY_PLUGIN_MODULE}.py"

PYTEST_TIMEOUT_RETRY_PLUGIN_SOURCE = r"""from __future__ import annotations

import os
import signal
import threading

import pytest

# Branches whose run.sh never installs pytest-timeout have no timeouts to re-arm;
# without this guard the injected `-p` plugin would crash pytest at startup.
try:
    import pytest_timeout
except ImportError:
    pytest_timeout = None

_ORIGINAL_PYTHONPATH_ENV = "PROGRAMBENCH_PYTEST_ORIGINAL_PYTHONPATH"
_ORIGINAL_PYTEST_ADDOPTS_ENV = "PROGRAMBENCH_PYTEST_ORIGINAL_ADDOPTS"
_SETTINGS_ATTR = "_programbench_timeout_settings"


def _restore_environment_variable(snapshot_name, target_name):
    if snapshot_name not in os.environ:
        return
    original_value = os.environ.pop(snapshot_name)
    if original_value:
        os.environ[target_name] = original_value
    else:
        os.environ.pop(target_name, None)


def _restore_pytest_environment():
    _restore_environment_variable(_ORIGINAL_PYTHONPATH_ENV, "PYTHONPATH")
    _restore_environment_variable(_ORIGINAL_PYTEST_ADDOPTS_ENV, "PYTEST_ADDOPTS")


def _getcwd():
    try:
        return os.getcwd()
    except FileNotFoundError:
        return None


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_protocol(item):
    _restore_pytest_environment()
    yield


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_call(item):
    # Some generated tests chdir in their body (often into a temp dir that is then
    # deleted) without changing back, so later tests on the same xdist worker start
    # elsewhere (`./executable` not found, os.getcwd() raising). Undo a chdir made
    # by the test body; directory changes made by fixtures are left alone.
    before = _getcwd()
    yield
    if before is not None and _getcwd() != before and os.path.isdir(before):
        os.chdir(before)


def _cancel_timer(item):
    cancel = getattr(item, "cancel_timeout", None)
    if cancel is not None:
        cancel()


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_setup(item):
    settings = getattr(item, _SETTINGS_ATTR, None)
    if settings is not None and settings.func_only is False:
        _cancel_timer(item)
        _install_signal_timer(item, settings)
    yield


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_teardown(item):
    try:
        yield
    finally:
        settings = getattr(item, _SETTINGS_ATTR, None)
        if settings is not None and settings.func_only is False:
            _cancel_timer(item)


def _raise_timeout(item, settings):
    if hasattr(settings, "disable_debugger_detection"):
        pytest_timeout.timeout_sigalrm(item, settings)
    else:
        pytest_timeout.timeout_sigalrm(item, settings.timeout)


def _install_signal_timer(item, settings):
    def handler(signum, frame):
        __tracebackhide__ = True
        disable_debugger_detection = getattr(
            settings, "disable_debugger_detection", False
        )
        if not disable_debugger_detection and pytest_timeout.is_debugging():
            return
        _raise_timeout(item, settings)

    def cancel():
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, signal.SIG_DFL)

    item.cancel_timeout = cancel
    signal.signal(signal.SIGALRM, handler)
    signal.setitimer(signal.ITIMER_REAL, settings.timeout)


@pytest.hookimpl(tryfirst=True, optionalhook=True)
def pytest_timeout_set_timer(item, settings):
    if settings.method != "signal":
        return None
    if threading.current_thread() is not threading.main_thread():
        return None

    setattr(item, _SETTINGS_ATTR, settings)
    _install_signal_timer(item, settings)
    return True
"""
