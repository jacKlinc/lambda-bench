# lambda-bench

[![CI](https://github.com/jacKlinc/lambda-bench/actions/workflows/ci.yml/badge.svg)](https://github.com/jacKlinc/lambda-bench/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/lambda-bench)](https://pypi.org/project/lambda-bench/)
[![Coverage](https://codecov.io/gh/jacKlinc/lambda-bench/branch/main/graph/badge.svg)](https://codecov.io/gh/jacKlinc/lambda-bench)

Benchmarks AWS Lambda cold and warm start performance across memory tiers and runtimes (Python vs Go). Produces percentile stats, cost-proxy calculations, and publication-ready plots.

> [!NOTE]
> **AI Transparency Disclosure:** This project utilizes AI coding assistants to generate boilerplates, optimize benchmarks, and refine documentation. All critical logic and performance calculations are human-reviewed and verified.

## Example output

![Cold start breakdown](examples/go-vs-python/results/plots/cold_start_breakdown.png)

Full example run → [examples/go-vs-python/](examples/go-vs-python/)


## Install

```bash
pip install lambda-bench
```

Or with uv:

```bash
uv tool install lambda-bench
```

## Usage

### Run a benchmark

```bash
lambda-bench run \
  --config functions.json \
  --cold-iters 15 \
  --warm-iters 15 \
  --region ca-west-1 \
  --output-dir results/
```

### Re-plot from saved results (no re-invocation)

```bash
lambda-bench plot results/executions.json --output-dir results/plots/
```

### Diff two result files

```bash
lambda-bench compare results/before.json results/after.json
```

## Config format

`functions.json` is a list of function descriptors:

```json
[
  { "label": "Python 512MB", "function_name": "my-func-python", "memory_mb": 512, "variant": "python" },
  { "label": "Go 512MB",     "function_name": "my-func-go",     "memory_mb": 512, "variant": "go"     }
]
```

## IAM policy

The invoking principal needs:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "lambda:InvokeFunction",
        "lambda:UpdateFunctionConfiguration",
        "lambda:GetFunctionConfiguration"
      ],
      "Resource": "arn:aws:lambda:*:*:function:*"
    }
  ]
}
```

## Outputs

| File | Contents |
|---|---|
| `executions.json` | Raw `Report` objects — feed to `lambda-bench plot` or `compare` |
| `executions.csv` | Flat CSV for spreadsheet analysis |
| `plots/*.png` | Histograms, p50/p95 bars, billed duration vs memory, cost proxy, cold-start breakdown |

## Development

```bash
uv sync --dev
uv run pytest
uv run ruff check src tests
uv run mypy src
```
