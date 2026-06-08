import json
import os
from pathlib import Path
from unittest.mock import patch

import botocore.exceptions
import pytest
from typer.testing import CliRunner

from lambdabench.cli import app
from lambdabench.config import LambdaFn
from lambdabench.export import save_results_json
from lambdabench.parser import Report
from lambdabench.runner import FnResult

os.environ.setdefault("MPLBACKEND", "Agg")

runner = CliRunner()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_COLD_REPORT = Report("c1", 120.0, 200, 512, 80, 300.0)
_WARM_REPORT = Report("w1", 45.0, 100, 512, 80, None)

_CONFIG = [{"function_name": "fn-test", "variant": "python", "memory_mb": 512}]


def _make_fn_results(memory: int = 512) -> list[FnResult]:
    fn = LambdaFn(f"Python {memory}MB", "fn-test", memory, "python")
    return [FnResult(fn=fn, cold_reports=[_COLD_REPORT], warm_reports=[_WARM_REPORT])]


# ---------------------------------------------------------------------------
# run subcommand
# ---------------------------------------------------------------------------

def test_run_creates_executions_json(tmp_path):
    config_file = tmp_path / "fns.json"
    config_file.write_text(json.dumps(_CONFIG))
    out = tmp_path / "out"

    with (
        patch("lambdabench.cli.boto3.client"),
        patch("lambdabench.cli.run_cold", return_value=[_COLD_REPORT]),
        patch("lambdabench.cli.run_warm", return_value=[_WARM_REPORT]),
        patch("lambdabench.cli.plot_all"),
    ):
        result = runner.invoke(app, [
            "run", "--config", str(config_file),
            "--cold-iters", "1", "--warm-iters", "1",
            "--output-dir", str(out),
        ])

    assert result.exit_code == 0, result.output
    assert (out / "executions.json").exists()


def test_run_creates_csv(tmp_path):
    config_file = tmp_path / "fns.json"
    config_file.write_text(json.dumps(_CONFIG))
    out = tmp_path / "out"

    with (
        patch("lambdabench.cli.boto3.client"),
        patch("lambdabench.cli.run_cold", return_value=[_COLD_REPORT]),
        patch("lambdabench.cli.run_warm", return_value=[_WARM_REPORT]),
        patch("lambdabench.cli.plot_all"),
    ):
        runner.invoke(app, [
            "run", "--config", str(config_file),
            "--cold-iters", "1", "--warm-iters", "1",
            "--output-dir", str(out),
        ])

    assert (out / "executions.csv").exists()


def test_run_calls_plot_all(tmp_path):
    config_file = tmp_path / "fns.json"
    config_file.write_text(json.dumps(_CONFIG))

    with (
        patch("lambdabench.cli.boto3.client"),
        patch("lambdabench.cli.run_cold", return_value=[_COLD_REPORT]),
        patch("lambdabench.cli.run_warm", return_value=[_WARM_REPORT]),
        patch("lambdabench.cli.plot_all") as mock_plot,
    ):
        runner.invoke(app, [
            "run", "--config", str(config_file),
            "--cold-iters", "1", "--warm-iters", "1",
            "--output-dir", str(tmp_path / "out"),
        ])

    mock_plot.assert_called_once()


def test_run_executions_json_content(tmp_path):
    config_file = tmp_path / "fns.json"
    config_file.write_text(json.dumps(_CONFIG))
    out = tmp_path / "out"

    with (
        patch("lambdabench.cli.boto3.client"),
        patch("lambdabench.cli.run_cold", return_value=[_COLD_REPORT]),
        patch("lambdabench.cli.run_warm", return_value=[_WARM_REPORT]),
        patch("lambdabench.cli.plot_all"),
    ):
        runner.invoke(app, [
            "run", "--config", str(config_file),
            "--cold-iters", "1", "--warm-iters", "1",
            "--output-dir", str(out),
        ])

    data = json.loads((out / "executions.json").read_text())
    assert data["version"] == 1
    assert len(data["results"]) == 1
    assert data["results"][0]["fn"]["memory_mb"] == 512
    assert len(data["results"][0]["cold_reports"]) == 1


def test_run_missing_memory_mb_exits_nonzero(tmp_path):
    """Config entry without memory_mb should exit 1 with a helpful message."""
    config = [{"function_name": "fn-test", "variant": "python"}]
    config_file = tmp_path / "fns.json"
    config_file.write_text(json.dumps(config))

    with patch("lambdabench.cli.boto3.client"):
        result = runner.invoke(app, [
            "run", "--config", str(config_file), "--output-dir", str(tmp_path / "out"),
        ])

    assert result.exit_code == 1
    assert "memory_mb" in result.output


def test_run_uses_memory_mb_from_config(tmp_path):
    """memory_mb from the config is passed through to FnResult correctly."""
    config = [
        {"function_name": "fn-py", "variant": "python", "memory_mb": 3000, "label": "Python 3GB"}
    ]
    config_file = tmp_path / "fns.json"
    config_file.write_text(json.dumps(config))

    with (
        patch("lambdabench.cli.boto3.client"),
        patch("lambdabench.cli.run_cold", return_value=[_COLD_REPORT]),
        patch("lambdabench.cli.run_warm", return_value=[_WARM_REPORT]),
        patch("lambdabench.cli.plot_all"),
    ):
        result = runner.invoke(app, [
            "run", "--config", str(config_file),
            "--cold-iters", "1", "--warm-iters", "1",
            "--output-dir", str(tmp_path / "out"),
        ])

    assert result.exit_code == 0, result.output
    data = json.loads((tmp_path / "out" / "executions.json").read_text())
    assert data["results"][0]["fn"]["memory_mb"] == 3000


def test_run_cold_failure_skips_function_continues_to_next(tmp_path):
    """If run_cold raises RuntimeError for one function, the CLI continues to the next."""
    config = [
        {"function_name": "fn-fail", "variant": "python", "memory_mb": 128},
        {"function_name": "fn-ok", "variant": "python", "memory_mb": 1024},
    ]
    config_file = tmp_path / "fns.json"
    config_file.write_text(json.dumps(config))
    out = tmp_path / "out"

    def cold_side_effect(client, fn, n, **kw):
        if fn.function_name == "fn-fail":
            raise RuntimeError("all cold invocations failed")
        return [_COLD_REPORT]

    with (
        patch("lambdabench.cli.boto3.client"),
        patch("lambdabench.cli.run_cold", side_effect=cold_side_effect),
        patch("lambdabench.cli.run_warm", return_value=[_WARM_REPORT]),
        patch("lambdabench.cli.plot_all"),
    ):
        result = runner.invoke(app, [
            "run", "--config", str(config_file),
            "--cold-iters", "1", "--warm-iters", "1",
            "--output-dir", str(out),
        ])

    assert result.exit_code == 0, result.output
    data = json.loads((out / "executions.json").read_text())
    assert len(data["results"]) == 1
    assert data["results"][0]["fn"]["function_name"] == "fn-ok"


def test_run_exits_nonzero_when_all_functions_fail(tmp_path):
    """If every function raises RuntimeError, the CLI exits 1 with no files written."""
    config_file = tmp_path / "fns.json"
    config_file.write_text(json.dumps(_CONFIG))
    out = tmp_path / "out"

    with (
        patch("lambdabench.cli.boto3.client"),
        patch("lambdabench.cli.run_cold", side_effect=RuntimeError("all cold invocations failed")),
        patch("lambdabench.cli.plot_all"),
    ):
        result = runner.invoke(app, [
            "run", "--config", str(config_file),
            "--cold-iters", "1", "--warm-iters", "1",
            "--output-dir", str(out),
        ])

    assert result.exit_code == 1
    assert not (out / "executions.json").exists()


def test_run_warm_failure_saves_cold_results(tmp_path):
    """If run_warm raises RuntimeError, cold results are still saved."""
    config_file = tmp_path / "fns.json"
    config_file.write_text(json.dumps(_CONFIG))
    out = tmp_path / "out"

    with (
        patch("lambdabench.cli.boto3.client"),
        patch("lambdabench.cli.run_cold", return_value=[_COLD_REPORT]),
        patch(
            "lambdabench.cli.run_warm",
            side_effect=RuntimeError("all warm invocations failed"),
        ),
        patch("lambdabench.cli.plot_all"),
    ):
        result = runner.invoke(app, [
            "run", "--config", str(config_file),
            "--cold-iters", "1", "--warm-iters", "1",
            "--output-dir", str(out),
        ])

    assert result.exit_code == 0, result.output
    data = json.loads((out / "executions.json").read_text())
    assert len(data["results"][0]["cold_reports"]) == 1
    assert data["results"][0]["warm_reports"] == []


def test_run_iam_error_exits_nonzero(tmp_path):
    """An IAM error on invocation surfaces a helpful message and exits 1."""
    config_file = tmp_path / "fns.json"
    config_file.write_text(json.dumps(_CONFIG))

    iam_error = botocore.exceptions.ClientError(
        {"Error": {"Code": "AccessDeniedException",
                   "Message": "not authorized to perform: lambda:InvokeFunction"}},
        "Invoke",
    )
    with (
        patch("lambdabench.cli.boto3.client"),
        patch("lambdabench.cli.run_cold", side_effect=iam_error),
    ):
        result = runner.invoke(app, [
            "run", "--config", str(config_file), "--output-dir", str(tmp_path / "out"),
        ])

    assert result.exit_code == 1


# ---------------------------------------------------------------------------
# plot subcommand
# ---------------------------------------------------------------------------

def test_plot_creates_pngs(tmp_path):
    executions = tmp_path / "executions.json"
    save_results_json(_make_fn_results(), executions)
    plots_dir = tmp_path / "plots"

    result = runner.invoke(app, ["plot", str(executions), "--output-dir", str(plots_dir)])

    assert result.exit_code == 0, result.output
    assert any(plots_dir.glob("*.png"))


def test_plot_missing_file_exits_nonzero(tmp_path):
    result = runner.invoke(app, ["plot", str(tmp_path / "nonexistent.json")])
    assert result.exit_code == 1


# ---------------------------------------------------------------------------
# compare subcommand
# ---------------------------------------------------------------------------

def _write_executions(path: Path, memory: int, duration: float) -> None:
    report = Report("r1", duration, int(duration), memory, 80, None)
    fn = LambdaFn(f"Python {memory}MB", "fn-test", memory, "python")
    results = [FnResult(fn=fn, cold_reports=[], warm_reports=[report] * 5)]
    save_results_json(results, path)


def test_compare_shows_table(tmp_path):
    before = tmp_path / "before.json"
    after = tmp_path / "after.json"
    _write_executions(before, 512, 100.0)
    _write_executions(after, 512, 80.0)

    result = runner.invoke(app, ["compare", str(before), str(after)])

    assert result.exit_code == 0, result.output
    assert "Python 512MB" in result.output


def test_compare_saves_json_output(tmp_path):
    before = tmp_path / "before.json"
    after = tmp_path / "after.json"
    diff_out = tmp_path / "diff.json"
    _write_executions(before, 512, 100.0)
    _write_executions(after, 512, 80.0)

    runner.invoke(app, ["compare", str(before), str(after), "--output", str(diff_out)])

    assert diff_out.exists()
    data = json.loads(diff_out.read_text())
    assert "diffs" in data
    assert len(data["diffs"]) > 0


def test_compare_missing_file_exits_nonzero(tmp_path):
    before = tmp_path / "before.json"
    _write_executions(before, 512, 100.0)
    result = runner.invoke(app, ["compare", str(before), str(tmp_path / "missing.json")])
    assert result.exit_code == 1


def test_compare_delta_sign(tmp_path):
    before = tmp_path / "before.json"
    after = tmp_path / "after.json"
    _write_executions(before, 512, 200.0)  # slow
    _write_executions(after, 512, 100.0)   # fast → improvement

    result = runner.invoke(app, ["compare", str(before), str(after)])

    assert result.exit_code == 0
    assert "-" in result.output


# ---------------------------------------------------------------------------
# load_results_json round-trip
# ---------------------------------------------------------------------------

def test_save_load_results_round_trip(tmp_path):
    from lambdabench.export import load_results_json

    original = _make_fn_results()
    path = tmp_path / "executions.json"
    save_results_json(original, path)
    loaded = load_results_json(path)

    assert len(loaded) == 1
    assert loaded[0].fn.label == original[0].fn.label
    assert loaded[0].fn.memory_mb == 512
    assert len(loaded[0].cold_reports) == 1
    assert loaded[0].cold_reports[0].init_duration_ms == pytest.approx(300.0)
