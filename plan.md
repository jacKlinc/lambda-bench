# `lambda-bench` — Build Plan

## Tooling & Environment

- **uv** for package management, venv, and publishing (`uv init`, `uv add`, `uv publish`)
- **Python 3.14** — pinned via `uv python pin 3.14`
- **Claude Code** in VSCode for implementation
- `pyproject.toml` only — no `setup.py` or `requirements.txt`

---

## Project Structure

```
lambda-bench/
├── src/
│   └── lambdabench/
│       ├── __init__.py
│       ├── cli.py          # typer entrypoint
│       ├── config.py       # LambdaFn dataclass, VARIANT_COLOURS, constants
│       ├── invoker.py      # invoke(), retry/backoff, log truncation guard
│       ├── runner.py       # run_cold(), run_warm() with proper OOM/error handling
│       ├── parser.py       # parse_report(), Report dataclass
│       ├── stats.py        # percentiles, means, cost proxy calculation
│       ├── plot.py         # all matplotlib logic
│       └── export.py       # CSV + JSON output
├── tests/
│   ├── test_parser.py      # unit test parse_report() against fixture logs
│   ├── test_stats.py       # unit test percentile calculations
│   └── fixtures/
│       └── sample_logs.py  # hardcoded REPORT strings covering edge cases
├── pyproject.toml
├── .python-version         # 3.14
└── README.md
```

---

## Phase 0 — Scaffold

```bash
uv init lambda-bench
cd lambda-bench
uv python pin 3.14
uv add boto3 typer matplotlib
uv add --dev pytest ruff mypy
```

Prompt Claude Code to:

- Generate the full directory structure above with stub files
- Wire up the `typer` CLI entrypoint in `pyproject.toml` as `lambda-bench`
- Add `[tool.ruff]` and `[tool.mypy]` config blocks to `pyproject.toml`

---

## Phase 1 — Config & Data Model

### `config.py`
- `LambdaFn` dataclass: `label`, `function_name`, `memory_mb`, `variant`
- `VARIANT_COLOURS` dict
- Constants: `DEFAULT_COLD_ITERS`, `DEFAULT_WARM_ITERS`, `LOG_TRUNCATION_LIMIT = 4096`

### `parser.py`
- `Report` dataclass — all fields including `billed_duration_ms`
- `parse_report()` with a `log_truncated` flag: if `len(log) >= LOG_TRUNCATION_LIMIT`, emit a warning and return `None` rather than silently dropping
- **Write unit tests first** against fixture REPORT strings covering:
  - Normal cold start
  - Normal warm execution
  - OOM / truncated log
  - Missing init duration

---

## Phase 2 — Invoker & Runner

### `invoker.py`
- `invoke()` with exponential backoff retry on `TooManyRequestsException` and `ServiceException` — 3 retries, base delay 2s
- OOM detection from `FunctionError` response
- Log truncation check before returning

### `runner.py`
- `_wait_until_active()` — 60s timeout, 2s poll interval
- `set_bench_version()` — wrapped in `try/finally` to reset env var on crash
- `run_cold()` — `n` iterations, each with a fresh `BENCH_VERSION`
- `run_warm()` — prime invocation with full OOM/error check, then `n` warm invocations
- All functions accept a `progress_callback` so the CLI can render a progress bar

---

## Phase 3 — Stats & Export

### `stats.py`
- `percentile(vals, p)` — pure Python, no numpy dependency
- `summarise(reports)` — returns p50/p95/p99, mean, min, max for duration, billed duration, and memory used
- `cost_proxy(billed_ms, memory_mb)` — `(memory_mb / 1024) * (billed_ms / 1000) * GB_SECOND_PRICE`
- Clearly distinguish: `cold_total_ms = init_duration_ms + duration_ms` vs `init_duration_ms` alone

### `export.py`
- `save_csv(reports, path)` — all fields including `billed_duration_ms`
- `save_json(reports, summary, path)` — machine-readable, for use by `compare` subcommand

---

## Phase 4 — CLI

```bash
# Run a benchmark
lambda-bench run \
  --config functions.json \
  --memory 512 1024 1800 3000 \
  --cold-iters 15 \
  --warm-iters 15 \
  --region ca-west-1 \
  --output-dir results/

# Re-plot from saved results without re-invoking
lambda-bench plot results/executions.json \
  --output-dir results/plots/

# Diff two result files
lambda-bench compare results/before.json results/after.json
```

Three `typer` subcommands: `run`, `plot`, `compare`. Each validates IAM errors and surfaces the exact missing permission (e.g. `lambda:UpdateFunctionConfiguration`) with a fix hint.

---

## Phase 5 — Plots

Fix bugs from the existing script and extend:

| Plot | Status | Notes |
|---|---|---|
| Histogram per memory tier | Fix | Add 1024MB; derive tiers dynamically from data |
| Mean bar chart | Replace | p50 bars + p95 error bars |
| Billed duration vs memory | **New** | One line per variant — the cost story |
| Cost proxy vs memory | **New** | GB-seconds, annotated with Go/Python crossover point |
| Cold start breakdown | **New** | Stacked bar: init duration vs execution duration |

All plots saved as PNG. The `plot` subcommand regenerates all of them from a saved JSON without re-invoking.

---

## Phase 6 — Polish & Publish

- `README.md` with real Go vs Python output screenshots and the exact IAM policy JSON required
- `lambda-bench compare` renders a diff as a `rich` table in the terminal and saves to JSON
- GitHub Actions: lint (`ruff`), typecheck (`mypy`), unit tests (`pytest`) on push
- `uv publish` to PyPI

---

## Claude Code Session Strategy

Break into focused sessions — tight context per session, validate each layer before building on it:

| Session | Scope | Done when |
|---|---|---|
| 1 | Scaffold + `parser.py` + all unit tests | Tests green, no AWS calls needed |
| 2 | `invoker.py` + `runner.py` | boto3 mocked, retry logic tested |
| 3 | `stats.py` + `export.py` + `plot.py` | Fed fixture data, plots render correctly |
| 4 | `cli.py` wiring + smoke test | End-to-end run against real functions |
| 5 | Polish, README, CI, publish | PyPI package installable |

---

## Known Bugs to Fix from v0

| # | Bug | Fix |
|---|---|---|
| 1 | `Py-Vect 1800MB` and `3000MB` share the same function ARN | Verify and correct ARNs |
| 2 | `plot_histograms` skips 1024MB | Derive memory tiers dynamically from data |
| 3 | Warm prime invocation unchecked | Add OOM/error check to prime call |
| 4 | `means()` fallback uses `[1]` not `[0]` | Guard with `if vals`, skip or use `nan` |
| 5 | No retry on throttling | Exponential backoff in `invoker.py` |
| 6 | `_wait_until_active` timeout 30s, poll 1s | 60s timeout, 2s poll |
| 7 | `BENCH_VERSION` left dirty on crash | Wrap in `try/finally` |
| 8 | Log truncation silently drops data | Check `len(log) >= 4096`, warn and skip |
| 9 | `billed_duration_ms` never plotted | Add billed duration and cost proxy plots |
| 10 | Cold start metric is init only | Expose `cold_total_ms = init + duration` |
| 11 | Only means, no percentiles | p50/p95/p99 in `stats.py` |
| 12 | `COLD_ITERATIONS = 5` hardcoded | CLI arg, default 15 |
| 13 | `Patch` import inside function | Move to top-level imports |
| 14 | `print()` throughout | `logging` with `--verbose` / `--quiet` flags |
| 15 | `OUTPUT_DIR` hardcoded global | CLI `--output-dir` arg |