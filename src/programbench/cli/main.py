import typer

app = typer.Typer(name="programbench", no_args_is_help=True, add_completion=False)


@app.callback()
def _callback() -> None:
    """Evaluate whether LM-based SWE-agents can reverse-engineer black-box
    software systems."""


@app.command()
def eval(
    sources: list[str] = typer.Argument(..., help="'gold' and/or path(s) to run directories"),
    workers: int = typer.Option(1, "-w", "--workers", help="Number of parallel workers"),
    force: bool = typer.Option(False, "-f", "--force", help="Re-evaluate even if results exist"),
    filter_spec: str = typer.Option("", "--filter", help="Filter instance IDs by regex"),
    slice_spec: str = typer.Option("", "--slice", help="Slice specification (e.g. '0:5')"),
    summarize_only: bool = typer.Option(False, "--summarize-only", help="Skip evaluation; just read existing results"),
    image_tag: str = typer.Option("task", "--image-tag", help="Docker image tag to evaluate"),
) -> None:
    """Evaluate submissions against test suites.

    Accepts one or more sources: paths to run directories containing
    <instance_id>/submission.zip, or 'gold' for gold-solution evaluation.

    \b
    Examples:
        programbench eval output/run_name
        programbench eval gold
        programbench eval output/run_a output/run_b gold
        programbench eval output/run_name --workers 4 --force
        programbench eval output/run_name --filter 'eradman__entr.*'
        programbench eval output/run_name --slice 0:5
        programbench eval output/run_name --summarize-only
    """
    from programbench.eval.eval_batch import run_eval_batch

    run_eval_batch(
        sources=sources,
        force=force,
        workers=workers,
        filter_spec=filter_spec,
        slice_spec=slice_spec,
        summarize_only=summarize_only,
        image_tag=image_tag,
    )
