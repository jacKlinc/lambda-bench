import json
import logging
import re
import sys
from pathlib import Path
from typing import Annotated, Optional

import boto3
import botocore.exceptions
import typer
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

from lambdabench.config import DEFAULT_COLD_ITERS, DEFAULT_WARM_ITERS, LambdaFn
from lambdabench.export import load_results_json, save_csv, save_results_json
from lambdabench.plot import plot_all
from lambdabench.runner import FnResult, run_cold, run_warm, set_memory
from lambdabench.stats import summarise

app = typer.Typer(pretty_exceptions_show_locals=False, add_completion=False)
console = Console()
err = Console(stderr=True)


def _iam_guard(exc: botocore.exceptions.ClientError) -> None:
    code = exc.response["Error"]["Code"]
    if code in ("AccessDeniedException", "UnauthorizedException"):
        msg = exc.response["Error"]["Message"]
        m = re.search(r"perform:\s*(\S+)", msg)
        action = m.group(1) if m else "unknown"
        err.print(f"[bold red]IAM error:[/] Missing permission: [yellow]{action}[/]")
        err.print(f"  Fix: add [yellow]{action}[/] to your Lambda IAM policy.")
        raise typer.Exit(1)
    raise exc


@app.command()
def run(
    config: Annotated[Path, typer.Option("--config", help="JSON file listing function configs")],
    memory: Annotated[Optional[list[int]], typer.Option("--memory", help="Memory sizes to sweep (MB). Defaults to memory_mb in config if present.")] = None,
    cold_iters: Annotated[int, typer.Option("--cold-iters")] = DEFAULT_COLD_ITERS,
    warm_iters: Annotated[int, typer.Option("--warm-iters")] = DEFAULT_WARM_ITERS,
    region: Annotated[str, typer.Option("--region")] = "us-east-1",
    output_dir: Annotated[Path, typer.Option("--output-dir")] = Path("results"),
    verbose: Annotated[bool, typer.Option("--verbose/--quiet")] = False,
) -> None:
    """Run cold and warm benchmarks across Lambda functions and memory tiers."""
    logging.basicConfig(level=logging.DEBUG if verbose else logging.WARNING)

    raw = json.loads(config.read_text())
    base_fns: list[dict] = raw if isinstance(raw, list) else raw["functions"]

    client = boto3.client("lambda", region_name=region)
    results: list[FnResult] = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    ) as progress:
        for entry in base_fns:
            fn_name: str = entry["function_name"]
            variant: str = entry["variant"]

            if memory:
                # Explicit sweep — test every config entry at each memory tier
                tiers = [(mem, f"{variant.capitalize()} {mem}MB", True) for mem in memory]
            elif "memory_mb" in entry:
                # Config specifies memory — function is pre-configured, no set_memory needed
                mem = entry["memory_mb"]
                tiers = [(mem, entry.get("label", f"{variant.capitalize()} {mem}MB"), False)]
            else:
                # Fallback: default sweep, must update memory
                tiers = [(m, f"{variant.capitalize()} {m}MB", True) for m in [512, 1024, 1800, 3008]]

            for mem, label, should_set_memory in tiers:
                fn = LambdaFn(label=label, function_name=fn_name, memory_mb=mem, variant=variant)
                task = progress.add_task(f"{label}: benchmarking…", total=None)

                if should_set_memory:
                    progress.update(task, description=f"{label}: setting memory…")
                    try:
                        set_memory(client, fn_name, mem)
                    except botocore.exceptions.ClientError as exc:
                        _iam_guard(exc)

                progress.update(task, description=f"{label}: cold starts ({cold_iters}×)…")
                try:
                    cold = run_cold(
                        client, fn, cold_iters,
                        progress_callback=lambda i, t=task: progress.update(t),
                    )
                except botocore.exceptions.ClientError as exc:
                    _iam_guard(exc)
                except RuntimeError as exc:
                    progress.remove_task(task)
                    err.print(f"[bold red]✗[/] {label}: {exc}")
                    continue

                progress.update(task, description=f"{label}: warm runs ({warm_iters}×)…")
                try:
                    warm = run_warm(
                        client, fn, warm_iters,
                        progress_callback=lambda i, t=task: progress.update(t),
                    )
                except botocore.exceptions.ClientError as exc:
                    _iam_guard(exc)
                except RuntimeError as exc:
                    err.print(f"[bold red]✗[/] {label} (warm): {exc}")
                    warm = []

                results.append(FnResult(fn=fn, cold_reports=cold, warm_reports=warm))
                progress.remove_task(task)

    if not results:
        err.print("[bold red]Error:[/] No successful benchmark results. Check errors above.")
        raise typer.Exit(1)

    output_dir.mkdir(parents=True, exist_ok=True)
    save_results_json(results, output_dir / "executions.json")

    all_warm = [rep for r in results for rep in r.warm_reports]
    if all_warm:
        save_csv(all_warm, output_dir / "executions.csv")

    plot_all(results, output_dir / "plots")
    console.print(f"[green]✓[/] Results saved to [bold]{output_dir}/[/]")


@app.command()
def plot(
    results_json: Annotated[Path, typer.Argument(help="Path to executions.json")],
    output_dir: Annotated[Path, typer.Option("--output-dir")] = Path("results/plots"),
) -> None:
    """Re-generate all plots from a saved results file without re-invoking."""
    if not results_json.exists():
        err.print(f"[red]File not found:[/] {results_json}")
        raise typer.Exit(1)
    results = load_results_json(results_json)
    paths = plot_all(results, output_dir)
    console.print(f"[green]✓[/] {len(paths)} plots saved to [bold]{output_dir}/[/]")


@app.command()
def compare(
    before: Annotated[Path, typer.Argument(help="Before results JSON")],
    after: Annotated[Path, typer.Argument(help="After results JSON")],
    output: Annotated[Optional[Path], typer.Option("--output", help="Save diff to JSON")] = None,
) -> None:
    """Compare two benchmark result files and show a diff table."""
    for p in (before, after):
        if not p.exists():
            err.print(f"[red]File not found:[/] {p}")
            raise typer.Exit(1)

    before_map = {r.fn.label: r for r in load_results_json(before)}
    after_map = {r.fn.label: r for r in load_results_json(after)}

    table = Table(title=f"Comparison: {before.name} → {after.name}")
    table.add_column("Function", style="bold")
    table.add_column("Metric")
    table.add_column("Before", justify="right")
    table.add_column("After", justify="right")
    table.add_column("Δ", justify="right")

    diffs: list[dict] = []
    for label in sorted(set(before_map) | set(after_map)):
        br = before_map.get(label)
        ar = after_map.get(label)
        if br is None or ar is None:
            continue
        if not br.warm_reports or not ar.warm_reports:
            continue

        bs = summarise(br.warm_reports)
        as_ = summarise(ar.warm_reports)

        for metric, bval, aval in [
            ("p50 warm (ms)", bs.duration.p50, as_.duration.p50),
            ("p95 warm (ms)", bs.duration.p95, as_.duration.p95),
            ("billed p50 (ms)", bs.billed_duration.p50, as_.billed_duration.p50),
        ]:
            delta = aval - bval
            pct = (delta / bval * 100) if bval else 0.0
            colour = "green" if delta < 0 else ("red" if delta > 0 else "white")
            table.add_row(
                label, metric,
                f"{bval:.1f}", f"{aval:.1f}",
                f"[{colour}]{delta:+.1f} ({pct:+.0f}%)[/]",
            )
            diffs.append({"label": label, "metric": metric,
                          "before": bval, "after": aval, "delta": delta, "delta_pct": pct})

    console.print(table)

    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        payload = {"before": str(before), "after": str(after), "diffs": diffs}
        output.write_text(json.dumps(payload, indent=2))
        console.print(f"[green]✓[/] Diff saved to [bold]{output}[/]")
