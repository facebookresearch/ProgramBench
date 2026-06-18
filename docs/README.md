# ProgramBench usage guide

> [!IMPORTANT]
> All images on dockerhub are built for `linux/amd64` (x86_64) only.
> They will not run natively on macOS or Windows hosts (and emulating them via QEMU is generally slow).
> Use a Linux x86_64 machine to run inference and evaluation.

## Inference

Please use the images with tag `task_cleanroom_v6` from `https://hub.docker.com/orgs/programbench/repositories`.
E.g., to solve the task `ffmpeg__ffmpeg.360a402`, use the following image:

```
https://hub.docker.com/repository/docker/programbench/ffmpeg_1776_ffmpeg.360a402/tags/task_cleanroom_v6/
```

(the `__` is replaced by `_1776_`).


> [!IMPORTANT]
> The agent MUST NOT have access to internet during inference.

The submission of the agent is the complete codebase that the agent produced.
To evaluate with `ProgramBench`, it needs to be extracted as a `.tar.gz` archive and put into the following format:

```
my-amazing-agent-run
├── abishekvashok__cmatrix.5c082c6
│   └── submission.tar.gz
├── agourlay__zip-password-finder.704700d
│   └── submission.tar.gz
├── ajeetdsouza__zoxide.67ca1bc
│   └── submission.tar.gz
├── alecthomas__chroma.8d04def
│   └── submission.tar.gz
├── ...
```

All of the baselines from our paper were obtained with [mini-swe-agent](https://github.com/swe-agent/mini-swe-agent/) with a framework similar to [swebench.py](https://github.com/SWE-agent/mini-swe-agent/blob/main/src/minisweagent/run/benchmarks/swebench.py).
We expect to release our baseline system in `mini-swe-agent` this week.

## Evaluation

Evaluating your agent run is the main function performed by the `ProgramBench` repository.

After following the installation instructions from the [README](../README.md#quickstart), you can run the evaluation with:

```
uv run programbench eval /path/to/my-amazing-agent-run
```

The evaluation will automatically pull all required docker containers (e.g., `ffmpeg_1776_ffmpeg.360a402:task_v6`).

> [!TIP]
> Test blobs (per-branch test archives) are downloaded on demand from HuggingFace during evaluation.
> To pre-download them, run `uv run programbench blob sync` (all instances) or
> `uv run programbench blob sync <instance_id>` (single instance).

The output of the evaluation are JSON files that are included in the `my-amazing-agent-run` directory:

```
my-amazing-agent-run
├── abishekvashok__cmatrix.5c082c6
│   └── submission.tar.gz
│   └── abishekvashok__cmatrix.5c082c6.eval.json
├── agourlay__zip-password-finder.704700d
│   └── submission.tar.gz
│   └── agourlay__zip-password-finder.704700d.eval.json
├── ajeetdsouza__zoxide.67ca1bc
│   └── submission.tar.gz
│   └── ajeetdsouza__zoxide.67ca1bc.eval.json
├── ...
```

`programbench eval` will also show a summary of all outputs.
You can later bring up the same summary with:

```
uv run programbench info /path/to/my-amazing-agent-run
```

### Understanding the output files

Each JSON file has the following structure:

```json
{
    "test_results": [
        {
        "name": "tests.test_foo.test_passes",
        "branch": "abc123def456",
        "status": "passed",
        "extra": { "time": 0.002 }
        },
        {
        "name": "tests.test_foo.test_fails",
        "branch": "abc123def456",
        "status": "failure",
        "extra": {
            "time": 0.008,
            "message": "AssertionError: expected 'X' but got 'Y'",
            "text": "executable_path=/workspace/build/foo ..."
        }
        }
    ],
    "error_code": null,
    "error_details": null,
    "log": [
        ...,
        {
        "step": "results_read",
        "branch": "abc123def456",
        "command": "cat eval/results.xml",
        "wall_time": 0.071,
        "output": "<?xml version=\"1.0\" ...?><testsuites>...</testsuites>",
        "returncode": 0,
        "exception_info": ""
        }
    ],
    "solution_branch": "submission",
    "test_branches": ["abc123def456", "fedcba654321"],
    "test_branch_errors": {},
    "executable_hash": "980ff4f78ca130cedceaa42cec78431184827154fbc4ef95d2df5c8fee948186",
    "warnings": []
}
```

> [!IMPORTANT]
> As described in the paper, some branches and individual tests are ignored because they are non-deterministic
> or have other flaws. For this, essentially check the `tests.json` file in the `ProgramBench` data folder.

We **strongly** recommend to use the logic from `programbench info` to get final scores.

Field reference

- `test_results` — list of `{name, branch, status, extra}`. status ∈ passed / failure (others possible). extra may contain time (seconds), and on failures message (assertion text) and text (longer captured output).
- `error_code` / `error_details` — top-level error info, null on a clean run.
- `log` — ordered list of pipeline steps `{step, command, wall_time, output, returncode, exception_info}`, optionally with branch when the step is per-test-branch.
- `solution_branch` — name of the branch/folder holding the candidate solution (e.g. `submission`).
- `test_branches` — list of test-branch identifiers run against the solution.
- `test_branch_errors` — dict mapping branch → error info; empty when all branches ran cleanly.
- `executable_hash` — sha256 of the built artifact under test.
- `warnings` — list of warning strings emitted by the harness.
