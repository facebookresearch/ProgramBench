# CLAUDE.md

## Project Overview

ProgramBench evaluates whether LM-based SWE-agents can reverse-engineer black-box software systems. The workflow: take an open-source CLI tool (mostly Rust/Go), compile it into a Docker image with source removed, then have an LM agent re-implement it from scratch by interacting only with the binary. Behavioral tests (also LM-generated) score the re-implementation.

## Test ignore reasons

Some behavioral tests are unreliable and are excluded from scoring. Each excluded
test is recorded under `branches.<hash>.ignored_tests[]` in a task's `tests.json`,
with one or more `reasons[].id` explaining why. All ignored tests are excluded from
scoring regardless of reason; the id is informational.

- `gold_fail` — test fails **deterministically** on the reference (gold) solution, so it
  is defective rather than discriminating. Also covers golden-output drift (the gold
  binary is correct but the captured golden is stale/non-reproducible relative to the
  build toolchain, an embedded build-stamp, or an external resource).
- `gold_flaky` — test is **non-deterministic** on the gold solution: it passes in some
  runs and fails in others. These are timing/race/network/TUI-snapshot flakes, not real
  defects (distinct from the deterministic `gold_fail`).
- `dummy_pass` — test passes even on a trivial/dummy executable, so it fails to
  distinguish a real implementation from a stub.
- `outcome_dependent_presence` — test appears in some eval runs but not others.
- `slow_or_hang` — test hangs mid-call or exceeds a duration threshold.
- `dependency_ignored` — a kept test that `@pytest.mark.dependency(depends=[X])` on an
  *ignored* test `X`. Because `X` is deselected at collection, `pytest-dependency` skips
  the dependent, so it would count as unresolved through no fault of the submission. The
  reason `note` records the prerequisite (`depends on ignored test <X>`).
- `ignored_manual` — manually excluded.

## Quick reference

```bash
uv sync                  # install deps
uv run programbench      # run the CLI
uv run pytest            # run tests
```

## Project structure

```
src/programbench/
  cli/          # typer CLI (presentation layer — pretty printing, arg parsing)
  data/         # shipped data files (templates, configs, etc.)
  *.py          # core logic (no CLI concerns)
tests/
```

The CLI (`cli/`) and core logic are kept separate. All typer/rich/display code lives in `cli/`; everything else is importable without CLI dependencies.

## Build-time internet isolation

During eval, a submission's `compile.sh` always runs with internet **blocked** (`utils/internet_control.py`) so it can't smuggle `pip install`/download steps into the build. The block is an in-container DNS blackhole (overwrite `/etc/resolv.conf` with `nameserver 0.0.0.0`, restore after compile) — no host privileges, works under docker-in-docker. Test-execution containers are never touched (they may legitimately need network).

## Style guide

1. Target python 3.10 or higher
2. Use python with type annotations. Use `list` instead of `List`.
3. Use `pathlib` instead of `os.path`. Use `Path.read_text()` over `with ...open()` constructs.
4. Use `typer` to add interfaces
5. Keep code comments to a minimum and only highlight particularly logically challenging things
6. Do not append to the README unless specifically requested
7. Use `jinja` for formatting templates
8. Use `dataclass` for keeping track config
9. Do not catch exceptions unless explicitly told to.
10. Write concise, short, minimal code.
11. In most cases, avoid initializing variables just to pass them to a function. Instead just pass the expression to the function directly.
12. Not every exception has to be caught. Exceptions are a good way to show problems to a user.
13. This repository rewards minimal code. Try to be as concise as possible.
14. Do not catch error conditions explicitly if they would fail anyway.
15. Do not use overly defensive `.get()` calls. Prefer normal dictionary access.
    It's better to have a clear failure than to silently fall back to incorrect values.
16. Do not use `try`/`except` blocks just to reraise some other exception. Just let it fail, it's clearer to the user this way.
17. Do not factor out tiny functions

### Style examples

Rule 11 — pass expressions directly:
```python
# bad
a = func()
Class(a)

# good
Class(func())
```

Rule 14 — don't guard what would fail anyway:
```python
# bad
input = input()
if not "=" in input:
    raise ValueError("Input must be of form a=b")
x, y = input.split("=")

# good
x, y = input().split("=")
```

## Test style

1. Use `pytest`, not `unittest`.
2. **Do not mock/patch anything that you're not explicitly asked to.**
3. Avoid writing trivial tests. Every test should test for at least one, preferably multiple points of failure.
4. Avoid splitting up code in multiple lines like `a = func()\nassert a == b`. Instead, just do `assert func() == b`.
5. The first argument to `pytest.mark.parametrize` should be a tuple (not a string! not a list!), the second argument must be a list (not a tuple!).

### Test example

```python
# bad
result = func()
assert result == b

# good
assert func() == b
```

## Tooling

- **Package manager**: `uv`
- **CLI framework**: `typer`
- **Templating**: `jinja2`
- **Testing**: `pytest`
