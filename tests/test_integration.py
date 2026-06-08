"""
Integration tests — call real AWS Lambda functions.

Run with:
    LAMBDA_BENCH_INTEGRATION=1 uv run pytest tests/test_integration.py -v -s

Optional env vars:
    LAMBDA_BENCH_REGION   AWS region (default: ca-west-1)
    LAMBDA_BENCH_CONFIG   Path to functions.json (default: repo root functions.json)

Expected runtime: ~3-5 minutes (cold starts require waiting for execution-env recycling).
"""

import json
import os
from pathlib import Path

import boto3
import pytest
from typer.testing import CliRunner

from lambdabench.cli import app
from lambdabench.config import LambdaFn
from lambdabench.export import load_results_json, save_results_json
from lambdabench.invoker import invoke
from lambdabench.parser import parse_report
from lambdabench.runner import FnResult, run_cold, run_warm
from lambdabench.stats import cost_proxy, summarise

os.environ.setdefault("MPLBACKEND", "Agg")

# ---------------------------------------------------------------------------
# Guard — skip the whole module unless explicitly opted in
# ---------------------------------------------------------------------------

INTEGRATION = os.environ.get("LAMBDA_BENCH_INTEGRATION")
REGION = os.environ.get("LAMBDA_BENCH_REGION", "ca-west-1")
_DEFAULT_CONFIG = Path(__file__).parent.parent / "functions.json"
CONFIG_PATH = Path(os.environ.get("LAMBDA_BENCH_CONFIG", str(_DEFAULT_CONFIG)))

pytestmark = pytest.mark.skipif(
    not INTEGRATION,
    reason="Set LAMBDA_BENCH_INTEGRATION=1 to run integration tests",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def client():
    return boto3.client("lambda", region_name=REGION)


@pytest.fixture(scope="module")
def all_fns() -> list[LambdaFn]:
    raw: list[dict] = json.loads(CONFIG_PATH.read_text())
    return [
        LambdaFn(
            label=e.get("label", e["function_name"]),
            function_name=e["function_name"],
            memory_mb=e["memory_mb"],
            variant=e["variant"],
        )
        for e in raw
    ]


@pytest.fixture(scope="module")
def first_fn(all_fns: list[LambdaFn]) -> LambdaFn:
    return all_fns[0]


# ---------------------------------------------------------------------------
# Layer 1: invoker — single real invocation
# ---------------------------------------------------------------------------

class TestInvoker:
    def test_invoke_returns_invocation_result(self, client, first_fn):
        result = invoke(client, first_fn.function_name)
        assert result.log, "Expected non-empty log"
        assert result.function_error is None, f"FunctionError: {result.function_error}\n{result.log}"
        assert result.is_oom is False

    def test_invoke_log_contains_report_line(self, client, first_fn):
        result = invoke(client, first_fn.function_name)
        assert "REPORT RequestId:" in result.log, f"No REPORT in log:\n{result.log}"

    def test_invoke_log_contains_duration(self, client, first_fn):
        result = invoke(client, first_fn.function_name)
        assert "Duration:" in result.log
        assert "Billed Duration:" in result.log
        assert "Memory Size:" in result.log


# ---------------------------------------------------------------------------
# Layer 2: parser — parse a real REPORT line
# ---------------------------------------------------------------------------

class TestParser:
    def test_parse_report_from_real_log(self, client, first_fn):
        result = invoke(client, first_fn.function_name)
        report = parse_report(result.log)
        assert report is not None, f"parse_report returned None for:\n{result.log}"
        assert report.duration_ms > 0
        assert report.billed_duration_ms > 0
        assert report.memory_size_mb == first_fn.memory_mb
        assert report.max_memory_used_mb > 0
        assert report.max_memory_used_mb <= first_fn.memory_mb

    def test_parse_report_request_id_is_populated(self, client, first_fn):
        result = invoke(client, first_fn.function_name)
        report = parse_report(result.log)
        assert report is not None
        assert report.request_id, "Expected non-empty request_id"


# ---------------------------------------------------------------------------
# Layer 3: runner — warm runs (no config changes, fastest)
# ---------------------------------------------------------------------------

class TestRunnerWarm:
    def test_run_warm_returns_reports(self, client, first_fn):
        reports = run_warm(client, first_fn, n=3)
        assert len(reports) >= 1, "Expected at least 1 warm report"

    def test_run_warm_reports_have_no_init_duration(self, client, first_fn):
        reports = run_warm(client, first_fn, n=3)
        assert reports, "No warm reports returned"
        for r in reports:
            assert r.init_duration_ms is None, (
                f"Warm report should not have init_duration_ms, got {r.init_duration_ms}"
            )

    def test_run_warm_durations_are_positive(self, client, first_fn):
        reports = run_warm(client, first_fn, n=3)
        assert reports
        for r in reports:
            assert r.duration_ms > 0
            assert r.billed_duration_ms > 0

    def test_run_warm_memory_matches_config(self, client, first_fn):
        reports = run_warm(client, first_fn, n=2)
        assert reports
        for r in reports:
            assert r.memory_size_mb == first_fn.memory_mb

    def test_run_warm_progress_callback_fires(self, client, first_fn):
        fired: list[int] = []
        run_warm(client, first_fn, n=3, progress_callback=lambda i: fired.append(i))
        assert fired == [1, 2, 3]

    def test_run_warm_all_functions(self, client, all_fns):
        """Smoke-test every function in config with a single warm invoke."""
        for fn in all_fns:
            reports = run_warm(client, fn, n=1)
            assert len(reports) >= 1, f"No warm reports for {fn.label}"


# ---------------------------------------------------------------------------
# Layer 4: runner — cold runs (slow: each iteration updates config and waits)
# ---------------------------------------------------------------------------

class TestRunnerCold:
    def test_run_cold_returns_reports(self, client, first_fn):
        reports = run_cold(client, first_fn, n=1)
        assert len(reports) >= 1, "Expected at least 1 cold report"

    def test_run_cold_reports_have_init_duration(self, client, first_fn):
        reports = run_cold(client, first_fn, n=1)
        assert reports, "No cold reports returned"
        for r in reports:
            assert r.init_duration_ms is not None, (
                "Cold report missing init_duration_ms — may not have been a true cold start"
            )
            assert r.init_duration_ms > 0

    def test_run_cold_init_plus_duration_matches_total(self, client, first_fn):
        """cold_total_ms = init_duration_ms + duration_ms (the true cold start cost)."""
        reports = run_cold(client, first_fn, n=1)
        assert reports
        for r in reports:
            assert r.init_duration_ms is not None
            cold_total = r.init_duration_ms + r.duration_ms
            assert cold_total > r.duration_ms  # init adds overhead

    def test_run_cold_two_iters_both_have_init(self, client, first_fn):
        """Each cold iteration forces a new execution environment via BENCH_VERSION."""
        reports = run_cold(client, first_fn, n=2)
        assert len(reports) == 2, f"Expected 2 cold reports, got {len(reports)}"
        for r in reports:
            assert r.init_duration_ms is not None


# ---------------------------------------------------------------------------
# Layer 5: stats — computed from real reports
# ---------------------------------------------------------------------------

class TestStats:
    def test_summarise_warm_reports(self, client, first_fn):
        reports = run_warm(client, first_fn, n=5)
        assert reports
        s = summarise(reports)
        assert s.duration.p50 > 0
        assert s.duration.p95 >= s.duration.p50
        assert s.duration.p99 >= s.duration.p95
        assert s.duration.mean > 0
        assert s.cold_total is None  # warm reports have no init_duration_ms

    def test_summarise_cold_reports(self, client, first_fn):
        reports = run_cold(client, first_fn, n=2)
        assert reports
        s = summarise(reports)
        assert s.cold_total is not None
        assert s.cold_total.min > 0

    def test_cost_proxy_is_positive(self, client, first_fn):
        reports = run_warm(client, first_fn, n=3)
        assert reports
        s = summarise(reports)
        c = cost_proxy(s.billed_duration.mean, first_fn.memory_mb)
        assert c > 0


# ---------------------------------------------------------------------------
# Layer 6: CLI — end-to-end (run → files on disk, plot, compare)
# ---------------------------------------------------------------------------

class TestCLI:
    def test_cli_run_exit_zero(self, tmp_path):
        result = CliRunner().invoke(app, [
            "run",
            "--config", str(CONFIG_PATH),
            "--cold-iters", "1",
            "--warm-iters", "3",
            "--region", REGION,
            "--output-dir", str(tmp_path / "results"),
        ])
        assert result.exit_code == 0, f"CLI exited {result.exit_code}:\n{result.output}"

    def test_cli_run_creates_executions_json(self, tmp_path):
        CliRunner().invoke(app, [
            "run", "--config", str(CONFIG_PATH),
            "--cold-iters", "1", "--warm-iters", "3",
            "--region", REGION, "--output-dir", str(tmp_path / "results"),
        ])
        assert (tmp_path / "results" / "executions.json").exists()

    def test_cli_run_executions_json_is_loadable(self, tmp_path):
        out = tmp_path / "results"
        CliRunner().invoke(app, [
            "run", "--config", str(CONFIG_PATH),
            "--cold-iters", "1", "--warm-iters", "3",
            "--region", REGION, "--output-dir", str(out),
        ])
        loaded = load_results_json(out / "executions.json")
        assert len(loaded) == len(json.loads(CONFIG_PATH.read_text()))
        for fn_result in loaded:
            assert fn_result.fn.function_name
            assert len(fn_result.warm_reports) >= 1

    def test_cli_run_cold_reports_have_init_duration(self, tmp_path):
        out = tmp_path / "results"
        CliRunner().invoke(app, [
            "run", "--config", str(CONFIG_PATH),
            "--cold-iters", "1", "--warm-iters", "2",
            "--region", REGION, "--output-dir", str(out),
        ])
        loaded = load_results_json(out / "executions.json")
        for fn_result in loaded:
            assert fn_result.cold_reports, f"No cold reports for {fn_result.fn.label}"
            for r in fn_result.cold_reports:
                assert r.init_duration_ms is not None, (
                    f"Cold report for {fn_result.fn.label} missing init_duration_ms"
                )

    def test_cli_run_creates_csv(self, tmp_path):
        out = tmp_path / "results"
        CliRunner().invoke(app, [
            "run", "--config", str(CONFIG_PATH),
            "--cold-iters", "1", "--warm-iters", "2",
            "--region", REGION, "--output-dir", str(out),
        ])
        assert (out / "executions.csv").exists()
        lines = (out / "executions.csv").read_text().splitlines()
        assert len(lines) >= 2  # header + at least 1 data row

    def test_cli_run_creates_plots(self, tmp_path):
        out = tmp_path / "results"
        CliRunner().invoke(app, [
            "run", "--config", str(CONFIG_PATH),
            "--cold-iters", "1", "--warm-iters", "2",
            "--region", REGION, "--output-dir", str(out),
        ])
        pngs = list((out / "plots").glob("*.png"))
        assert len(pngs) == 5, f"Expected 5 PNGs, got {[p.name for p in pngs]}"

    def test_cli_plot_from_saved_results(self, client, first_fn, tmp_path):
        """plot subcommand regenerates PNGs from executions.json without invoking."""
        warm = run_warm(client, first_fn, n=3)
        cold = run_cold(client, first_fn, n=1)
        fn_result = FnResult(fn=first_fn, cold_reports=cold, warm_reports=warm)
        executions = tmp_path / "executions.json"
        save_results_json([fn_result], executions)

        result = CliRunner().invoke(app, [
            "plot", str(executions),
            "--output-dir", str(tmp_path / "plots"),
        ])
        assert result.exit_code == 0, result.output
        pngs = list((tmp_path / "plots").glob("*.png"))
        assert len(pngs) == 5

    def test_cli_compare_two_runs(self, client, first_fn, tmp_path):
        """compare subcommand diffs two executions.json files."""
        def _save(path: Path, n_warm: int) -> None:
            warm = run_warm(client, first_fn, n=n_warm)
            save_results_json([FnResult(fn=first_fn, cold_reports=[], warm_reports=warm)], path)

        before = tmp_path / "before.json"
        after = tmp_path / "after.json"
        _save(before, n_warm=3)
        _save(after, n_warm=3)

        diff_out = tmp_path / "diff.json"
        result = CliRunner().invoke(app, [
            "compare", str(before), str(after),
            "--output", str(diff_out),
        ])
        assert result.exit_code == 0, result.output
        assert first_fn.label in result.output
        assert diff_out.exists()
