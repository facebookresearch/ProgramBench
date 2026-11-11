# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the license found in the
# LICENSE file in the root directory of this source tree.

import os
import subprocess
import sys
import time
from pathlib import Path

from programbench.eval.pytest_timeout_retry import PYTEST_TIMEOUT_RETRY_PLUGIN_SOURCE


def test_signal_timeout_rearms_for_each_rerun(tmp_path: Path) -> None:
    (tmp_path / "programbench_pytest_timeout.py").write_text(PYTEST_TIMEOUT_RETRY_PLUGIN_SOURCE)
    test_path = tmp_path / "test_hang.py"
    test_path.write_text(
        "import os\n"
        "import subprocess\n"
        "import sys\n"
        "import time\n"
        "from pathlib import Path\n"
        "\n"
        "def test_hang():\n"
        "    assert os.environ.get('PYTEST_ADDOPTS') == '--strict-markers'\n"
        "    subprocess.run([sys.executable, '-m', 'pytest', '--version'], check=True)\n"
        "    with Path('attempts').open('a') as attempts:\n"
        "        attempts.write('attempt\\n')\n"
        "    time.sleep(60)\n"
    )
    environment = dict(os.environ)
    environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    environment["PROGRAMBENCH_PYTEST_ORIGINAL_PYTHONPATH"] = "/original/pythonpath"
    environment["PROGRAMBENCH_PYTEST_ORIGINAL_ADDOPTS"] = "--strict-markers"
    environment["PYTHONPATH"] = f"{tmp_path}:/original/pythonpath"
    environment["PYTEST_ADDOPTS"] = "-p programbench_pytest_timeout"
    started_at = time.monotonic()
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "pytest_timeout",
            "-p",
            "pytest_rerunfailures",
            "--reruns=2",
            "--reruns-delay=0",
            "--timeout=1",
            "--timeout-method=signal",
            "--junitxml=results.xml",
            test_path.name,
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    assert result.returncode == 1, result.stdout + result.stderr
    assert time.monotonic() - started_at < 10
    assert "Timeout" in (tmp_path / "results.xml").read_text()
    assert (tmp_path / "attempts").read_text().splitlines() == ["attempt", "attempt", "attempt"]


def test_test_body_chdir_is_undone_but_fixture_chdir_kept(tmp_path: Path) -> None:
    (tmp_path / "programbench_pytest_timeout.py").write_text(PYTEST_TIMEOUT_RETRY_PLUGIN_SOURCE)
    (tmp_path / "fixture_dir").mkdir()
    (tmp_path / "conftest.py").write_text(
        "import os\n"
        "import pytest\n"
        "\n"
        "@pytest.fixture(scope='session', autouse=True)\n"
        "def into_fixture_dir():\n"
        "    original = os.getcwd()\n"
        "    os.chdir('fixture_dir')\n"
        "    yield\n"
        "    os.chdir(original)\n"
    )
    (tmp_path / "test_cwd.py").write_text(
        "import os\n"
        "import shutil\n"
        "import tempfile\n"
        "\n"
        "def test_a_leaks_deleted_cwd():\n"
        "    d = tempfile.mkdtemp()\n"
        "    os.chdir(d)\n"
        "    shutil.rmtree(d)\n"
        "\n"
        "def test_b_leaks_other_cwd():\n"
        "    os.chdir(tempfile.gettempdir())\n"
        "\n"
        "def test_c_keeps_fixture_cwd():\n"
        "    assert os.path.basename(os.getcwd()) == 'fixture_dir'\n"
    )
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(tmp_path)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "programbench_pytest_timeout", "test_cwd.py"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "3 passed" in result.stdout
