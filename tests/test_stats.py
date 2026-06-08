import pytest

from lambdabench.parser import Report
from lambdabench.stats import (
    GB_SECOND_PRICE,
    FieldStats,
    Summary,
    cost_proxy,
    percentile,
    summarise,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _report(
    duration: float,
    billed: int,
    memory: int = 512,
    max_mem: int = 100,
    init: float | None = None,
) -> Report:
    return Report(
        request_id="test",
        duration_ms=duration,
        billed_duration_ms=billed,
        memory_size_mb=memory,
        max_memory_used_mb=max_mem,
        init_duration_ms=init,
    )


# ---------------------------------------------------------------------------
# percentile
# ---------------------------------------------------------------------------

def test_percentile_p50_odd_length():
    assert percentile([1.0, 2.0, 3.0, 4.0, 5.0], 50) == 3.0


def test_percentile_p50_even_length():
    # nearest rank: ceil(50/100*4) = 2 → vals[1] = 2.0
    assert percentile([1.0, 2.0, 3.0, 4.0], 50) == 2.0


def test_percentile_p95_twenty_elements():
    vals = [float(i) for i in range(1, 21)]
    # ceil(95/100*20) = ceil(19) = 19 → vals[18] = 19.0
    assert percentile(vals, 95) == 19.0


def test_percentile_p99():
    vals = [float(i) for i in range(1, 101)]
    assert percentile(vals, 99) == 99.0


def test_percentile_p100():
    assert percentile([1.0, 2.0, 3.0], 100) == 3.0


def test_percentile_p0_clamps_to_first():
    # ceil(0) = 0 → clamped to rank 1 → vals[0]
    assert percentile([5.0, 10.0, 15.0], 0) == 5.0


def test_percentile_single_element():
    assert percentile([42.0], 50) == 42.0
    assert percentile([42.0], 99) == 42.0


def test_percentile_unsorted_input():
    assert percentile([5.0, 1.0, 3.0], 50) == 3.0


def test_percentile_empty_raises():
    with pytest.raises(ValueError, match="empty"):
        percentile([], 50)


# ---------------------------------------------------------------------------
# summarise — basic fields
# ---------------------------------------------------------------------------

def test_summarise_duration_fields():
    reports = [_report(100.0, 100), _report(200.0, 200), _report(300.0, 300)]
    s = summarise(reports)
    assert isinstance(s.duration, FieldStats)
    assert s.duration.min == pytest.approx(100.0)
    assert s.duration.max == pytest.approx(300.0)
    assert s.duration.mean == pytest.approx(200.0)
    assert s.duration.p50 == pytest.approx(200.0)


def test_summarise_billed_duration_fields():
    reports = [_report(100.0, 100), _report(200.0, 200)]
    s = summarise(reports)
    assert s.billed_duration.mean == pytest.approx(150.0)


def test_summarise_memory_used_fields():
    reports = [_report(100.0, 100, max_mem=80), _report(100.0, 100, max_mem=120)]
    s = summarise(reports)
    assert s.memory_used.min == pytest.approx(80.0)
    assert s.memory_used.max == pytest.approx(120.0)


# ---------------------------------------------------------------------------
# summarise — cold_total
# ---------------------------------------------------------------------------

def test_summarise_cold_total_with_init():
    r1 = _report(100.0, 100, init=200.0)  # total = 300
    r2 = _report(150.0, 150, init=250.0)  # total = 400
    s = summarise([r1, r2])
    assert s.cold_total is not None
    assert s.cold_total.min == pytest.approx(300.0)
    assert s.cold_total.max == pytest.approx(400.0)
    assert s.cold_total.mean == pytest.approx(350.0)


def test_summarise_cold_total_none_when_no_init():
    reports = [_report(100.0, 100), _report(200.0, 200)]
    s = summarise(reports)
    assert s.cold_total is None


def test_summarise_cold_total_partial_init():
    r1 = _report(100.0, 100, init=200.0)
    r2 = _report(200.0, 200)  # no init
    s = summarise([r1, r2])
    assert s.cold_total is not None
    assert s.cold_total.min == pytest.approx(300.0)  # only r1 contributes


def test_summarise_empty_raises():
    with pytest.raises(ValueError, match="no reports"):
        summarise([])


def test_summarise_returns_summary_type():
    assert isinstance(summarise([_report(100.0, 100)]), Summary)


# ---------------------------------------------------------------------------
# cost_proxy
# ---------------------------------------------------------------------------

def test_cost_proxy_one_gb_one_second():
    # 1024MB * 1000ms = 1 GB-second
    result = cost_proxy(1000.0, 1024)
    assert result == pytest.approx(GB_SECOND_PRICE)


def test_cost_proxy_scales_with_memory():
    c512 = cost_proxy(1000.0, 512)
    c1024 = cost_proxy(1000.0, 1024)
    assert c1024 == pytest.approx(c512 * 2)


def test_cost_proxy_scales_with_duration():
    c1s = cost_proxy(1000.0, 512)
    c2s = cost_proxy(2000.0, 512)
    assert c2s == pytest.approx(c1s * 2)


def test_cost_proxy_zero_billed():
    assert cost_proxy(0.0, 512) == pytest.approx(0.0)
