import typer

app = typer.Typer(name="programbench", no_args_is_help=True)


@app.callback()
def _callback() -> None:
    """Evaluate whether LM-based SWE-agents can reverse-engineer black-box
    software systems."""


@app.command()
def hello() -> None:
    """Placeholder command."""
    typer.echo("programbench is alive")
