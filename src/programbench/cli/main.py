from pathlib import Path

import typer

from programbench.cli.blob import app as blob_app
from programbench.constants import DOCKER_CPUS

app = typer.Typer(
    name="programbench",
    no_args_is_help=True,
    add_completion=False,
    context_settings={"help_option_names": ["-h", "--help"]},
)
app.add_typer(blob_app, name="blob")


@app.callback()
def _callback() -> None:
    """Evaluate whether LM-based SWE-agents can reverse-engineer black-box
    software systems."""


@app.command()
def eval(
    sources: list[str] = typer.Argument(..., help="Path(s) to run directories"),
    workers: int = typer.Option(1, "-w", "--workers", help="Number of instances to evaluate in parallel"),
    branch_workers: int = typer.Option(
        1,
        "-b",
        "--branch-workers",
        help="Number of test branches to run in parallel within an instance "
        "(each branch runs in its own container off the post-compile image).",
    ),
    docker_cpus: int = typer.Option(
        DOCKER_CPUS,
        "--docker-cpus",
        help="CPU cores allotted per docker container. Also exported as "
        "PYTEST_XDIST_AUTO_NUM_WORKERS inside the container.",
    ),
    branch_retries: int = typer.Option(
        1,
        "--branch-retries",
        help="On a test branch whose JUnit XML reports a pytest-xdist worker "
        "crash, re-run the branch up to this many times in a fresh container. "
        "The attempt with the fewest crashes wins. Pass 0 to disable.",
    ),
    force: bool = typer.Option(False, "-f", "--force", help="Re-evaluate even if results exist"),
    filter_spec: str = typer.Option("", "--filter", help="Filter instance IDs by regex"),
    slice_spec: str = typer.Option("", "--slice", help="Slice specification (e.g. '0:5')"),
    summarize_only: bool = typer.Option(False, "--summarize-only", help="Skip evaluation; just read existing results"),
    image_tag: str = typer.Option("task", "--image-tag", help="Docker image tag to evaluate"),
    output: str = typer.Option(
        "",
        "-o",
        "--output",
        help="Write results under this directory instead of in-place. "
        "For each source S, eval.json files land at <output>/<S.name>/<instance_id>/.",
    ),
) -> None:
    """Evaluate submissions against test suites.

    Accepts one or more paths to run directories containing
    <instance_id>/submission.tar.gz.

    \b
    Examples:
        programbench eval output/run_name
        programbench eval output/run_a output/run_b
        programbench eval output/run_name --workers 4 --force
        programbench eval output/run_name -w 4 -b 2 --docker-cpus 8
        programbench eval output/run_name --filter 'eradman__entr.*'
        programbench eval output/run_name --slice 0:5
        programbench eval output/run_name --summarize-only
        programbench eval ~/gold -o ~/gold-eval-out
    """
    from programbench.eval.eval_batch import run_eval_batch

    run_eval_batch(
        sources=sources,
        force=force,
        workers=workers,
        branch_workers=branch_workers,
        docker_cpus=docker_cpus,
        filter_spec=filter_spec,
        slice_spec=slice_spec,
        summarize_only=summarize_only,
        image_tag=image_tag,
        output=output,
        branch_retries=branch_retries,
    )


@app.command()
def info(
    run_dir: Path = typer.Argument(..., help="Run directory containing <instance_id>/<instance_id>.eval.json"),
) -> None:
    """Print scores and warnings for an evaluated run directory.

    \b
    Examples:
        programbench info ~/gold-eval-9/gold
        programbench info output/run_name
    """
    from rich.console import Console

    from programbench.eval.eval import EvaluationResult
    from programbench.eval.eval_batch import BatchEvalSummary, InstanceEvalSummary

    eval_paths = sorted(run_dir.glob("*/*.eval.json"))
    if not eval_paths:
        raise typer.BadParameter(f"No <iid>/<iid>.eval.json files found under {run_dir}")

    summaries: list[InstanceEvalSummary] = []
    for p in eval_paths:
        result = EvaluationResult.model_validate_json(p.read_text())
        summaries.append(InstanceEvalSummary.from_eval_result(p.parent.name, result))

    console = Console()
    console.print(BatchEvalSummary(summaries=summaries).summary())
