from typer.testing import CliRunner

from programbench.cli.main import app

runner = CliRunner()


def test_eval_help() -> None:
    result = runner.invoke(app, ["eval", "--help"])
    assert result.exit_code == 0
    assert "sources" in result.output.lower()
