# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the license found in the
# LICENSE file in the root directory of this source tree.

"""Submission lifecycle commands: package an eval run, verify a submission, recombine eval.json."""

from pathlib import Path

import typer

app = typer.Typer(no_args_is_help=True, help="Prepare, check, and reassemble leaderboard submissions.")


@app.command()
def package(
    run_dir: Path = typer.Argument(
        ..., help="A `programbench eval` run directory (<run_dir>/<iid>/submission.tar.gz)."
    ),
    upload_to: str = typer.Option(
        "",
        "--upload-to",
        metavar="ORG[/DATASET]",
        help="Upload submission.tar.gz and the heavy eval.log.json to a HuggingFace dataset, "
        "replacing each with a .url + .sha256. A bare org (e.g. 'programbench') creates a "
        "per-submission dataset org/<run-dir-name>; pass 'org/name' to use an exact dataset.",
    ),
    overwrite: bool = typer.Option(
        False, "--overwrite", help="With --upload-to, re-upload files already present on HF (default: skip them)."
    ),
) -> None:
    """Turn an evaluated run directory into a leaderboard submission, in place.

    Writes a submission.yaml manifest and _stats/score.json, and splits each large
    eval.json into a light eval.json (kept) + a heavy <iid>.eval.log.json (raw log +
    failure text) so the repo stays git-pushable. With --upload-to, the heavy files and
    the submission.tar.gz artifacts are uploaded to HuggingFace. System metadata and
    trajectories are left as TODO.

    \b
    Examples:
        programbench submit package output/my-run
        programbench submit package output/my-run --upload-to programbench
    """
    from rich.console import Console

    from programbench.package import package_run

    result = package_run(run_dir, upload_to=upload_to or None, overwrite=overwrite)
    console = Console()
    console.print(
        f"Packaged [bold]{len(result.packaged)}[/bold] instance(s) in [bold]{result.run_dir}[/bold] "
        f"(skipped {len(result.skipped)} unknown). "
        f"mean_score={result.headline.mean_score * 100:.1f} resolved={result.headline.resolved_pct:.1f}%"
    )
    console.print(
        "[dim]Each eval.json was split into eval.json + <iid>.eval.log.json (recombine with "
        "`programbench submit recombine`). Next: fill in submission.yaml + add traj.json files.[/dim]"
    )


@app.command()
def verify(
    submission_dir: Path = typer.Argument(..., help="A packaged submission directory (contains submission.yaml)."),
    tier1: bool = typer.Option(
        False, "--tier1", help="Also re-run `programbench eval` and check artifacts reproduce the results (Docker)."
    ),
    workers: int = typer.Option(1, "-w", "--workers", help="Instance workers for the Tier-1 re-eval."),
    filter_spec: str = typer.Option(
        "", "--filter", help="Restrict Tier-1 re-eval to instance IDs matching this regex."
    ),
) -> None:
    """Verify a submission against its own claimed results.

    Tier 0 (default, no Docker) recomputes the headline from the submission's eval.json
    files and checks it matches submission.yaml. Tier 1 (--tier1) additionally resolves
    each submission.tar.gz and re-runs evaluation to confirm the artifacts reproduce the
    reported scores.

    \b
    Examples:
        programbench submit verify ./their-submission
        programbench submit verify ./their-submission --tier1 -w 4
    """
    from rich.console import Console
    from rich.table import Table

    from programbench.verify import verify_tier0, verify_tier1

    result = (
        verify_tier1(submission_dir, workers=workers, filter_spec=filter_spec)
        if tier1
        else verify_tier0(submission_dir)
    )

    table = Table(title=f"Tier-{result.tier} verification", box=None)
    table.add_column("Check", style="bold")
    table.add_column("Claimed", justify="right")
    table.add_column("Computed", justify="right")
    table.add_column("", justify="center")
    for c in result.checks:
        table.add_row(c.name, str(c.claimed), str(c.computed), "✅" if c.ok else "❌")
    console = Console()
    console.print(table)
    if result.ok:
        console.print("[bold green]PASS[/bold green] — submission is consistent with its reported results.")
    else:
        console.print("[bold red]FAIL[/bold red] — discrepancies found above.")
        raise typer.Exit(1)


@app.command()
def register(
    submission_dir: Path = typer.Argument(..., help="A packaged submission directory (contains submission.yaml)."),
    registry: str = typer.Option(
        "", "--registry", help="Registry repo to PR against (default: ProgramBench/submissions)."
    ),
    source: str = typer.Option(
        "", "--source", help="Public URL of this submission's repo (default: autodetected from its git remote)."
    ),
    commit: str = typer.Option(
        "", "--commit", help="Commit SHA that was scored (default: autodetected from its git HEAD)."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Build the registry entry locally and print the plan; touch no network."
    ),
    verify: bool = typer.Option(
        True, "--verify/--no-verify", help="Run a Tier-0 verify gate before registering (default: on)."
    ),
) -> None:
    """Register a packaged submission on the leaderboard by opening a PR to the registry.

    The PR adds a small submissions/<id>/ entry: a pointer.yaml (the submission repo URL +
    the exact commit scored) plus the submission.yaml and _stats/ copied from this run. The
    source URL and commit are read from the run directory's own git remote/HEAD. With `gh`
    installed the registry is forked and the PR opened for you; otherwise the entry is left
    committed on a branch and the steps to push + open the PR are printed.

    \b
    Examples:
        programbench submit register ./my-run --dry-run
        programbench submit register ./my-run
    """
    import tempfile

    from rich.console import Console

    from programbench.register import REGISTRY_DEFAULT, build_plan, register_submission, write_entry

    console = Console()
    registry = registry or REGISTRY_DEFAULT

    if verify:
        from programbench.verify import verify_tier0

        if not verify_tier0(submission_dir).ok:
            console.print(
                "[bold red]FAIL[/bold red] — Tier-0 verification failed; fix the submission (or pass "
                "--no-verify) before registering. Run `programbench submit verify .` to see the mismatch."
            )
            raise typer.Exit(1)

    plan = build_plan(submission_dir, registry)
    if source:
        plan.source = source
    if commit:
        plan.commit = commit

    if dry_run:
        with tempfile.TemporaryDirectory() as tmp:
            entry = write_entry(plan, submission_dir, Path(tmp))
            files = sorted(str(p.relative_to(entry)) for p in entry.rglob("*") if p.is_file())
        console.print(f"[bold]Would register[/bold] [cyan]{plan.submission_id}[/cyan] to {plan.registry}")
        console.print(f"  branch: {plan.branch}")
        console.print(f"  source: {plan.source}\n  commit: {plan.commit}")
        console.print("  files:  " + ", ".join(f"submissions/{plan.submission_id}/{f}" for f in files))
        console.print(f"\n[dim]pointer.yaml:[/dim]\n{plan.pointer.rstrip()}")
        console.print(f"\n[dim]PR title:[/dim] {plan.title}\n[dim]PR body:[/dim]\n{plan.body}")
        console.print("\n[dim]Dry run — nothing cloned, pushed, or opened. Drop --dry-run to register.[/dim]")
        return

    result = register_submission(submission_dir, registry)
    if result.pr_url:
        console.print(f"[bold green]Opened PR[/bold green] for {plan.submission_id}: {result.pr_url}")
    else:
        console.print(f"[bold]Prepared[/bold] registry entry for {plan.submission_id}.\n{result.next_steps}")


@app.command()
def recombine(
    run_dir: Path = typer.Argument(..., help="A packaged run/submission directory."),
) -> None:
    """Reverse `package`'s eval split: fold each <iid>.eval.log.json back into its
    eval.json, restoring the original full eval output.

    The heavy file is read locally, or downloaded from its .url if it was uploaded to HF.

    \b
    Examples:
        programbench submit recombine ./their-submission
    """
    from rich.console import Console

    from programbench.submission import recombine_eval_json

    n = sum(recombine_eval_json(d, d.name) for d in sorted(p for p in run_dir.iterdir() if p.is_dir()))
    Console().print(f"Recombined [bold]{n}[/bold] eval.json file(s) in {run_dir}")
