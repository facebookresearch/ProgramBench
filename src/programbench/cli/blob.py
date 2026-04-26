import typer

app = typer.Typer(name="blob", no_args_is_help=True, add_completion=False)


@app.command()
def sync(
    instance_id: str = typer.Argument(None, help="Instance ID to sync (omit for all)"),
) -> None:
    """Download blob files from the HuggingFace repo into the local cache."""
    from programbench.utils.blob_store import get_blob_dir, sync_all

    if instance_id:
        path = get_blob_dir(instance_id)
    else:
        path = sync_all()
    if path is None:
        typer.echo("Blob store is disabled (HF_REVISION is empty).")
        raise typer.Exit(1)
    typer.echo(f"Blobs cached at {path}")
