import math
import os
from collections import defaultdict
from pathlib import Path

import matplotlib
if not os.environ.get("MPLBACKEND"):
    matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from lambdabench.config import VARIANT_COLOURS
from lambdabench.runner import FnResult
from lambdabench.stats import cost_proxy, summarise


def _colour(variant: str) -> str:
    return VARIANT_COLOURS.get(variant, "#888888")


def plot_histograms(results: list[FnResult], output_dir: Path) -> Path:
    """One histogram per memory tier, all variants overlaid, warm durations."""
    tiers: dict[int, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for r in results:
        for rep in r.warm_reports:
            tiers[rep.memory_size_mb][r.fn.variant].append(rep.duration_ms)

    sorted_tiers = sorted(tiers)
    n = len(sorted_tiers)
    if n == 0:
        raise ValueError("no warm reports to plot")

    fig, axes = plt.subplots(1, n, figsize=(5 * n, 4), squeeze=False)
    for ax, memory_mb in zip(axes[0], sorted_tiers):
        for variant, durations in sorted(tiers[memory_mb].items()):
            ax.hist(durations, bins=20, alpha=0.7, color=_colour(variant), label=variant)
        ax.set_title(f"{memory_mb} MB")
        ax.set_xlabel("Duration (ms)")
        ax.set_ylabel("Count")
        ax.legend()

    fig.suptitle("Execution Time Distribution by Memory Tier")
    fig.tight_layout()
    output_dir.mkdir(parents=True, exist_ok=True)
    out = output_dir / "histogram.png"
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return out


def plot_p50_p95_bars(results: list[FnResult], output_dir: Path) -> Path:
    """p50 warm duration bars with p95 error bars, grouped by memory tier."""
    variant_data: dict[str, dict[int, tuple[float, float]]] = defaultdict(dict)
    for r in results:
        if not r.warm_reports:
            continue
        s = summarise(r.warm_reports)
        variant_data[r.fn.variant][r.fn.memory_mb] = (s.duration.p50, s.duration.p95)

    all_memories = sorted({r.fn.memory_mb for r in results})
    variants = sorted(variant_data)
    n_variants = len(variants)
    x = np.arange(len(all_memories))
    width = 0.8 / max(n_variants, 1)

    fig, ax = plt.subplots(figsize=(max(6, len(all_memories) * 2), 5))
    for i, variant in enumerate(variants):
        p50s = [variant_data[variant].get(m, (float("nan"), float("nan")))[0] for m in all_memories]
        p95s = [variant_data[variant].get(m, (float("nan"), float("nan")))[1] for m in all_memories]
        yerr = [
            p95 - p50 if not (math.isnan(p50) or math.isnan(p95)) else 0
            for p50, p95 in zip(p50s, p95s)
        ]
        offset = (i - n_variants / 2 + 0.5) * width
        ax.bar(
            x + offset, p50s, width, yerr=yerr,
            label=variant, color=_colour(variant), alpha=0.85,
            error_kw={"capsize": 4},
        )

    ax.set_xticks(x)
    ax.set_xticklabels([f"{m} MB" for m in all_memories])
    ax.set_xlabel("Memory")
    ax.set_ylabel("Duration (ms)")
    ax.set_title("p50 Duration with p95 Error Bars (Warm)")
    ax.legend()
    fig.tight_layout()

    output_dir.mkdir(parents=True, exist_ok=True)
    out = output_dir / "p50_p95_bars.png"
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return out


def plot_billed_vs_memory(results: list[FnResult], output_dir: Path) -> Path:
    """p50 billed duration vs memory, one line per variant."""
    data: dict[str, list[tuple[int, float]]] = defaultdict(list)
    for r in results:
        if not r.warm_reports:
            continue
        s = summarise(r.warm_reports)
        data[r.fn.variant].append((r.fn.memory_mb, s.billed_duration.p50))

    fig, ax = plt.subplots(figsize=(8, 5))
    for variant, points in sorted(data.items()):
        points.sort()
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        ax.plot(xs, ys, marker="o", label=variant, color=_colour(variant))

    ax.set_xlabel("Memory (MB)")
    ax.set_ylabel("p50 Billed Duration (ms)")
    ax.set_title("Billed Duration vs Memory")
    ax.legend()
    fig.tight_layout()

    output_dir.mkdir(parents=True, exist_ok=True)
    out = output_dir / "billed_vs_memory.png"
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return out


def plot_cost_proxy_vs_memory(results: list[FnResult], output_dir: Path) -> Path:
    """Cost proxy (USD) vs memory, one line per variant, crossover annotated."""
    data: dict[str, list[tuple[int, float]]] = defaultdict(list)
    for r in results:
        if not r.warm_reports:
            continue
        s = summarise(r.warm_reports)
        c = cost_proxy(s.billed_duration.mean, r.fn.memory_mb)
        data[r.fn.variant].append((r.fn.memory_mb, c))

    fig, ax = plt.subplots(figsize=(8, 5))
    variant_lines: dict[str, tuple[list[int], list[float]]] = {}
    for variant, points in sorted(data.items()):
        points.sort()
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        ax.plot(xs, ys, marker="o", label=variant, color=_colour(variant))
        variant_lines[variant] = (xs, ys)

    # Annotate crossover between exactly two variants
    if len(variant_lines) == 2:
        (v0, (xs0, ys0)), (v1, (xs1, ys1)) = list(variant_lines.items())
        common = sorted(set(xs0) & set(xs1))
        ys0_map = dict(zip(xs0, ys0))
        ys1_map = dict(zip(xs1, ys1))
        for i in range(len(common) - 1):
            xa, xb = common[i], common[i + 1]
            da = ys0_map[xa] - ys1_map[xa]
            db = ys0_map[xb] - ys1_map[xb]
            if da * db < 0:
                cross_x = xa + (xb - xa) * abs(da) / (abs(da) + abs(db))
                cross_y = (ys0_map[xa] + ys0_map[xb]) / 2
                ax.axvline(cross_x, color="gray", linestyle="--", alpha=0.5)
                ax.annotate(
                    f"crossover ~{cross_x:.0f} MB",
                    xy=(cross_x, cross_y),
                    xytext=(10, 10),
                    textcoords="offset points",
                    fontsize=8,
                )
                break

    ax.set_xlabel("Memory (MB)")
    ax.set_ylabel("Cost Proxy (USD)")
    ax.set_title("Cost Proxy vs Memory")
    ax.legend()
    fig.tight_layout()

    output_dir.mkdir(parents=True, exist_ok=True)
    out = output_dir / "cost_proxy_vs_memory.png"
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return out


def plot_cold_start_breakdown(results: list[FnResult], output_dir: Path) -> Path:
    """Stacked bar: mean init duration + mean execution duration per function."""
    cold_data: list[tuple[str, float, float]] = []
    for r in results:
        pairs = [
            (rep.init_duration_ms, rep.duration_ms)
            for rep in r.cold_reports
            if rep.init_duration_ms is not None
        ]
        if not pairs:
            continue
        mean_init = sum(init for init, _ in pairs) / len(pairs)
        mean_exec = sum(exec_ for _, exec_ in pairs) / len(pairs)
        cold_data.append((r.fn.label, mean_init, mean_exec))

    fig, ax = plt.subplots(figsize=(max(6, len(cold_data) * 1.5 + 2), 5))
    if not cold_data:
        ax.text(0.5, 0.5, "No cold start data", ha="center", va="center", transform=ax.transAxes)
    else:
        labels = [d[0] for d in cold_data]
        inits = [d[1] for d in cold_data]
        execs = [d[2] for d in cold_data]
        x = np.arange(len(labels))
        ax.bar(x, inits, label="Init duration", color="#e07b54")
        ax.bar(x, execs, bottom=inits, label="Execution duration", color="#5b9bd5")
        ax.set_xticks(x)
        ax.set_xticklabels(labels, rotation=15, ha="right")
        ax.set_ylabel("Duration (ms)")
        ax.set_title("Cold Start Breakdown (Init vs Execution)")
        ax.legend()

    fig.tight_layout()
    output_dir.mkdir(parents=True, exist_ok=True)
    out = output_dir / "cold_start_breakdown.png"
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return out


def plot_all(results: list[FnResult], output_dir: Path) -> list[Path]:
    """Generate all 5 plots. Returns list of created PNG paths."""
    return [
        plot_histograms(results, output_dir),
        plot_p50_p95_bars(results, output_dir),
        plot_billed_vs_memory(results, output_dir),
        plot_cost_proxy_vs_memory(results, output_dir),
        plot_cold_start_breakdown(results, output_dir),
    ]
