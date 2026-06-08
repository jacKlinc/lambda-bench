import csv
import json
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

from lambdabench.config import LambdaFn
from lambdabench.parser import Report
from lambdabench.stats import Summary

if TYPE_CHECKING:
    from lambdabench.runner import FnResult


def save_csv(reports: list[Report], path: Path) -> None:
    """Write reports to CSV. Creates parent directories if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not reports:
        path.write_text("")
        return
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(asdict(reports[0]).keys()))
        writer.writeheader()
        for r in reports:
            writer.writerow(asdict(r))


def save_json(reports: list[Report], summary: Summary, path: Path) -> None:
    """Write reports + summary as machine-readable JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {
        "reports": [asdict(r) for r in reports],
        "summary": asdict(summary),
    }
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def save_results_json(results: list[FnResult], path: Path) -> None:
    """Write all FnResult objects to executions.json format."""

    path.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {
        "version": 1,
        "results": [
            {
                "fn": asdict(r.fn),
                "cold_reports": [asdict(rep) for rep in r.cold_reports],
                "warm_reports": [asdict(rep) for rep in r.warm_reports],
            }
            for r in results
        ],
    }
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def load_results_json(path: Path) -> list[FnResult]:
    """Load FnResult objects from executions.json format."""
    from lambdabench.runner import FnResult

    raw = json.loads(path.read_text())
    entries = raw["results"] if isinstance(raw, dict) else raw
    results = []
    for entry in entries:
        fn = LambdaFn(**entry["fn"])
        cold = [Report(**r) for r in entry.get("cold_reports", [])]
        warm = [Report(**r) for r in entry.get("warm_reports", [])]
        results.append(FnResult(fn=fn, cold_reports=cold, warm_reports=warm))
    return results
