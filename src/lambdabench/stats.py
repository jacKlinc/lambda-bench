import math
from dataclasses import dataclass

from lambdabench.parser import Report

GB_SECOND_PRICE: float = 0.0000166667  # USD per GB-second, x86 Lambda


@dataclass
class FieldStats:
    p50: float
    p95: float
    p99: float
    mean: float
    min: float
    max: float


@dataclass
class Summary:
    duration: FieldStats
    billed_duration: FieldStats
    memory_used: FieldStats
    cold_total: FieldStats | None  # init_duration_ms + duration_ms; None when no cold reports


def percentile(vals: list[float], p: float) -> float:
    """Nearest-rank percentile. p in [0, 100]. Pure Python, no numpy."""
    if not vals:
        raise ValueError("percentile of empty list")
    sorted_vals = sorted(vals)
    n = len(sorted_vals)
    rank = math.ceil(p / 100.0 * n)
    rank = max(1, min(rank, n))
    return sorted_vals[rank - 1]


def _field_stats(vals: list[float]) -> FieldStats:
    return FieldStats(
        p50=percentile(vals, 50),
        p95=percentile(vals, 95),
        p99=percentile(vals, 99),
        mean=sum(vals) / len(vals),
        min=min(vals),
        max=max(vals),
    )


def summarise(reports: list[Report]) -> Summary:
    if not reports:
        raise ValueError("no reports to summarise")

    cold_totals = [
        r.init_duration_ms + r.duration_ms
        for r in reports
        if r.init_duration_ms is not None
    ]

    return Summary(
        duration=_field_stats([r.duration_ms for r in reports]),
        billed_duration=_field_stats([float(r.billed_duration_ms) for r in reports]),
        memory_used=_field_stats([float(r.max_memory_used_mb) for r in reports]),
        cold_total=_field_stats(cold_totals) if cold_totals else None,
    )


def cost_proxy(billed_ms: float, memory_mb: int) -> float:
    """Cost in USD: (memory GB) × (billed seconds) × price_per_GB_second."""
    return (memory_mb / 1024) * (billed_ms / 1000) * GB_SECOND_PRICE
