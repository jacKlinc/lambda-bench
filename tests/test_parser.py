import logging

import pytest

from lambdabench.parser import Report, parse_report
from tests.fixtures.sample_logs import (
    COLD_START,
    HIGH_MEMORY_COLD,
    NO_INIT_DURATION,
    NO_REPORT_LINE,
    TRUNCATED_LOG,
    TRUNCATED_WITH_REPORT,
    WARM_EXEC,
)


def test_cold_start_parses_all_fields():
    r = parse_report(COLD_START)
    assert isinstance(r, Report)
    assert r.request_id == "aaa-111"
    assert r.duration_ms == pytest.approx(123.45)
    assert r.billed_duration_ms == 200
    assert r.memory_size_mb == 512
    assert r.max_memory_used_mb == 78
    assert r.init_duration_ms == pytest.approx(302.50)


def test_warm_exec_has_no_init_duration():
    r = parse_report(WARM_EXEC)
    assert isinstance(r, Report)
    assert r.request_id == "bbb-222"
    assert r.duration_ms == pytest.approx(45.67)
    assert r.billed_duration_ms == 46
    assert r.memory_size_mb == 1024
    assert r.max_memory_used_mb == 95
    assert r.init_duration_ms is None


def test_truncated_log_returns_none_and_warns(caplog):
    with caplog.at_level(logging.WARNING, logger="lambdabench.parser"):
        result = parse_report(TRUNCATED_LOG)
    assert result is None
    assert any("truncated" in msg.lower() for msg in caplog.messages)


def test_truncated_log_with_embedded_report_returns_none():
    # Even if a REPORT line is present, truncation takes priority.
    assert len(TRUNCATED_WITH_REPORT) >= 4096
    result = parse_report(TRUNCATED_WITH_REPORT)
    assert result is None


def test_no_init_duration_field():
    r = parse_report(NO_INIT_DURATION)
    assert isinstance(r, Report)
    assert r.init_duration_ms is None


def test_no_report_line_returns_none_and_warns(caplog):
    with caplog.at_level(logging.WARNING, logger="lambdabench.parser"):
        result = parse_report(NO_REPORT_LINE)
    assert result is None
    assert any("no report" in msg.lower() for msg in caplog.messages)


def test_high_memory_cold_start():
    r = parse_report(HIGH_MEMORY_COLD)
    assert isinstance(r, Report)
    assert r.duration_ms == pytest.approx(5000.01)
    assert r.billed_duration_ms == 5001
    assert r.memory_size_mb == 10240
    assert r.max_memory_used_mb == 9876
    assert r.init_duration_ms == pytest.approx(1200.99)


def test_returns_report_dataclass_instance():
    r = parse_report(COLD_START)
    assert isinstance(r, Report)


def test_billed_duration_is_int():
    r = parse_report(WARM_EXEC)
    assert isinstance(r, Report)
    assert isinstance(r.billed_duration_ms, int)


def test_duration_is_float():
    r = parse_report(COLD_START)
    assert isinstance(r, Report)
    assert isinstance(r.duration_ms, float)
