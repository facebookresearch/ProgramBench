#!/usr/bin/env python3
"""Bump the programbench version in pyproject.toml."""

import re
import subprocess
import sys
from pathlib import Path

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"
VERSION_RE = re.compile(r'^version\s*=\s*"([^"]+)"', re.MULTILINE)


def main() -> None:
    text = PYPROJECT.read_text()
    match = VERSION_RE.search(text)
    if not match:
        print("Error: could not find version in", PYPROJECT)
        sys.exit(1)

    current = match.group(1)
    print(f"Current version: {current}")

    new = input("New version: ").strip()
    if not new:
        print("No version entered, aborting.")
        sys.exit(1)

    updated = text[: match.start(1)] + new + text[match.end(1) :]
    PYPROJECT.write_text(updated)
    print(f"Updated {PYPROJECT.relative_to(Path.cwd())}: {current} -> {new}")

    # Commit the changes
    try:
        subprocess.run(["git", "add", str(PYPROJECT)], check=True)
        subprocess.run(["git", "commit", "-m", "Bump version"], check=True)
        print("Changes committed with message 'Bump version'")
    except subprocess.CalledProcessError as e:
        print(f"Error committing changes: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
