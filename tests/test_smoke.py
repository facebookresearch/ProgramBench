from typer.testing import CliRunner

from programbench.cli.main import app

runner = CliRunner()


def test_hello() -> None:
    result = runner.invoke(app, ["hello"])
    assert result.exit_code == 0
    assert "alive" in result.output
