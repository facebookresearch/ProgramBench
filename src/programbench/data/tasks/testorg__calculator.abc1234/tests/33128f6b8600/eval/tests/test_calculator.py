import subprocess


def _run(a, op, b):
    result = subprocess.run(
        ["./executable", str(a), op, str(b)],
        capture_output=True, text=True, timeout=10,
    )
    return result.stdout.strip()


def test_addition():
    assert _run(2, "+", 3) == "5"
    assert _run(0, "+", 0) == "0"
    assert _run(-1, "+", 1) == "0"


def test_subtraction():
    assert _run(10, "-", 3) == "7"
    assert _run(0, "-", 5) == "-5"


def test_multiplication():
    assert _run(4, "*", 3) == "12"
    assert _run(0, "*", 100) == "0"
