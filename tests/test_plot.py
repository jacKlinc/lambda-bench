import os

import pytest

os.environ.setdefault("MPLBACKEND", "Agg")

from lambdabench.config import LambdaFn
from lambdabench.parser import Report
from lambdabench.plot import (
    plot_all,
    plot_billed_vs_memory,
    plot_cold_start_breakdown,
    plot_cost_proxy_vs_memory,
    plot_histograms,
    plot_p50_p95_bars,
)
from lambdabench.runner import FnResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _rep(duration: float, billed: int, memory: int, max_mem: int, init: float | None = None) -> Report:
    return Report(
        request_id="t",
        duration_ms=duration,
        billed_duration_ms=billed,
        memory_size_mb=memory,
        max_memory_used_mb=max_mem,
        init_duration_ms=init,
    )


def _fn(memory: int, variant: str) -> LambdaFn:
    return LambdaFn(
        label=f"{variant.capitalize()} {memory}MB",
        function_name=f"fn-{variant}-{memory}",
        memory_mb=memory,
        variant=variant,
    )


def _make_results() -> list[FnResult]:
    """Two variants × two memory tiers = 4 FnResult objects with cold + warm data."""
    configs = [(512, "python"), (512, "go"), (1024, "python"), (1024, "go")]
    results = []
    for memory, variant in configs:
        cold = [_rep(100 + i, 100, memory, 80, 200.0 + i) for i in range(5)]
        warm = [_rep(40 + i, 100, memory, 80) for i in range(10)]
        results.append(FnResult(fn=_fn(memory, variant), cold_reports=cold, warm_reports=warm))
    return results


# ---------------------------------------------------------------------------
# Individual plot functions
# ---------------------------------------------------------------------------

def test_plot_histograms_creates_png(tmp_path):
    out = plot_histograms(_make_results(), tmp_path)
    assert out.exists()
    assert out.suffix == ".png"
    assert out.name == "histogram.png"


def test_plot_p50_p95_bars_creates_png(tmp_path):
    out = plot_p50_p95_bars(_make_results(), tmp_path)
    assert out.exists()
    assert out.suffix == ".png"


def test_plot_billed_vs_memory_creates_png(tmp_path):
    out = plot_billed_vs_memory(_make_results(), tmp_path)
    assert out.exists()
    assert out.name == "billed_vs_memory.png"


def test_plot_cost_proxy_vs_memory_creates_png(tmp_path):
    out = plot_cost_proxy_vs_memory(_make_results(), tmp_path)
    assert out.exists()
    assert out.name == "cost_proxy_vs_memory.png"


def test_plot_cold_start_breakdown_creates_png(tmp_path):
    out = plot_cold_start_breakdown(_make_results(), tmp_path)
    assert out.exists()
    assert out.name == "cold_start_breakdown.png"


# ---------------------------------------------------------------------------
# plot_all
# ---------------------------------------------------------------------------

def test_plot_all_returns_five_paths(tmp_path):
    paths = plot_all(_make_results(), tmp_path)
    assert len(paths) == 5


def test_plot_all_all_pngs_exist(tmp_path):
    paths = plot_all(_make_results(), tmp_path)
    for p in paths:
        assert p.exists(), f"{p} missing"
        assert p.suffix == ".png"


def test_plot_all_creates_output_dir(tmp_path):
    out_dir = tmp_path / "new" / "subdir"
    plot_all(_make_results(), out_dir)
    assert out_dir.exists()


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_plot_cold_start_breakdown_no_cold_data(tmp_path):
    """Should create a PNG with 'No cold start data' text rather than crashing."""
    results = [
        FnResult(
            fn=_fn(512, "python"),
            cold_reports=[_rep(100, 100, 512, 80, init=None)],  # no init_duration
            warm_reports=[_rep(50, 50, 512, 80)],
        )
    ]
    out = plot_cold_start_breakdown(results, tmp_path)
    assert out.exists()


def test_plot_histograms_single_tier(tmp_path):
    results = [
        FnResult(fn=_fn(512, "python"), cold_reports=[], warm_reports=[_rep(50, 50, 512, 80)]),
    ]
    out = plot_histograms(results, tmp_path)
    assert out.exists()


def test_plot_cost_proxy_annotates_crossover(tmp_path):
    """Two variants that cross over — should not raise."""
    results = [
        FnResult(fn=_fn(512, "python"),  cold_reports=[], warm_reports=[_rep(200, 200, 512, 80)]),
        FnResult(fn=_fn(1024, "python"), cold_reports=[], warm_reports=[_rep(50, 50, 1024, 80)]),
        FnResult(fn=_fn(512, "go"),      cold_reports=[], warm_reports=[_rep(50, 50, 512, 60)]),
        FnResult(fn=_fn(1024, "go"),     cold_reports=[], warm_reports=[_rep(300, 300, 1024, 60)]),
    ]
    out = plot_cost_proxy_vs_memory(results, tmp_path)
    assert out.exists()
