import csv
import json

import pytest

from lambdabench.export import save_csv, save_json
from lambdabench.parser import Report
from lambdabench.stats import summarise


def _report(
    duration: float = 100.0,
    billed: int = 100,
    memory: int = 512,
    max_mem: int = 80,
    init: float | None = None,
    rid: str = "req-1",
) -> Report:
    return Report(
        request_id=rid,
        duration_ms=duration,
        billed_duration_ms=billed,
        memory_size_mb=memory,
        max_memory_used_mb=max_mem,
        init_duration_ms=init,
    )


# ---------------------------------------------------------------------------
# save_csv
# ---------------------------------------------------------------------------

def test_save_csv_creates_file(tmp_path):
    path = tmp_path / "out.csv"
    save_csv([_report()], path)
    assert path.exists()


def test_save_csv_has_header(tmp_path):
    path = tmp_path / "out.csv"
    save_csv([_report()], path)
    content = path.read_text()
    assert "request_id" in content
    assert "billed_duration_ms" in content
    assert "init_duration_ms" in content


def test_save_csv_all_field_values(tmp_path):
    r = _report(duration=123.45, billed=200, memory=512, max_mem=78, init=302.5, rid="abc")
    path = tmp_path / "out.csv"
    save_csv([r], path)
    rows = list(csv.DictReader(open(path)))
    assert len(rows) == 1
    assert rows[0]["request_id"] == "abc"
    assert float(rows[0]["duration_ms"]) == pytest.approx(123.45)
    assert int(rows[0]["billed_duration_ms"]) == 200
    assert float(rows[0]["init_duration_ms"]) == pytest.approx(302.5)


def test_save_csv_none_init_duration(tmp_path):
    path = tmp_path / "out.csv"
    save_csv([_report(init=None)], path)
    rows = list(csv.DictReader(open(path)))
    assert rows[0]["init_duration_ms"] == ""


def test_save_csv_multiple_rows(tmp_path):
    reports = [_report(rid=f"r{i}", duration=float(i * 10)) for i in range(5)]
    path = tmp_path / "out.csv"
    save_csv(reports, path)
    rows = list(csv.DictReader(open(path)))
    assert len(rows) == 5


def test_save_csv_empty_reports(tmp_path):
    path = tmp_path / "out.csv"
    save_csv([], path)
    assert path.exists()
    assert path.read_text() == ""


def test_save_csv_creates_parent_dirs(tmp_path):
    path = tmp_path / "nested" / "dir" / "out.csv"
    save_csv([_report()], path)
    assert path.exists()


# ---------------------------------------------------------------------------
# save_json
# ---------------------------------------------------------------------------

def test_save_json_creates_file(tmp_path):
    r = _report()
    path = tmp_path / "out.json"
    save_json([r], summarise([r]), path)
    assert path.exists()


def test_save_json_valid_json(tmp_path):
    r = _report()
    path = tmp_path / "out.json"
    save_json([r], summarise([r]), path)
    data = json.loads(path.read_text())
    assert "reports" in data
    assert "summary" in data


def test_save_json_report_fields_round_trip(tmp_path):
    r = _report(duration=123.45, billed=200, rid="abc-123", init=302.5)
    path = tmp_path / "out.json"
    save_json([r], summarise([r]), path)
    data = json.loads(path.read_text())
    rep = data["reports"][0]
    assert rep["request_id"] == "abc-123"
    assert rep["duration_ms"] == pytest.approx(123.45)
    assert rep["billed_duration_ms"] == 200
    assert rep["init_duration_ms"] == pytest.approx(302.5)


def test_save_json_summary_has_percentiles(tmp_path):
    reports = [_report(duration=float(d)) for d in [100, 200, 300]]
    path = tmp_path / "out.json"
    save_json(reports, summarise(reports), path)
    data = json.loads(path.read_text())
    dur = data["summary"]["duration"]
    assert "p50" in dur
    assert "p95" in dur
    assert "p99" in dur
    assert "mean" in dur


def test_save_json_cold_total_none_serialises(tmp_path):
    r = _report(init=None)
    path = tmp_path / "out.json"
    save_json([r], summarise([r]), path)
    data = json.loads(path.read_text())
    assert data["summary"]["cold_total"] is None


def test_save_json_creates_parent_dirs(tmp_path):
    path = tmp_path / "a" / "b" / "out.json"
    r = _report()
    save_json([r], summarise([r]), path)
    assert path.exists()
