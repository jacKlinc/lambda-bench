import logging
import time
import uuid
from collections.abc import Callable, Generator
from contextlib import contextmanager
from typing import Any

from lambdabench.config import LambdaFn
from lambdabench.invoker import InvokeResult, invoke
from lambdabench.parser import Report, parse_report

logger = logging.getLogger(__name__)

_WAIT_TIMEOUT = 60.0
_WAIT_POLL = 2.0


def _wait_until_active(
    client: Any,
    function_name: str,
    timeout: float = _WAIT_TIMEOUT,
    poll: float = _WAIT_POLL,
) -> None:
    deadline = time.monotonic() + timeout
    while True:
        cfg = client.get_function_configuration(FunctionName=function_name)
        if cfg.get("State") == "Active" and cfg.get("LastUpdateStatus") == "Successful":
            return
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"{function_name} not active after {timeout:.0f}s "
                f"(State={cfg.get('State')}, LastUpdateStatus={cfg.get('LastUpdateStatus')})"
            )
        time.sleep(poll)


@contextmanager
def set_bench_version(
    client: Any,
    function_name: str,
    version: str,
) -> Generator[None, None, None]:
    """Set BENCH_VERSION env var and wait until active; always reset on exit."""

    def _patch_env(val: str) -> None:
        cfg = client.get_function_configuration(FunctionName=function_name)
        env_vars = dict(cfg.get("Environment", {}).get("Variables", {}))
        env_vars["BENCH_VERSION"] = val
        client.update_function_configuration(
            FunctionName=function_name,
            Environment={"Variables": env_vars},
        )

    _patch_env(version)
    _wait_until_active(client, function_name)
    try:
        yield
    finally:
        try:
            _patch_env("")
        except Exception:
            logger.warning("Failed to reset BENCH_VERSION on %s", function_name)


def _check_result(result: InvokeResult, fn: LambdaFn) -> Report | None:
    if result.is_oom:
        raise RuntimeError(f"OOM detected on {fn.function_name} ({fn.memory_mb} MB)")
    if result.function_error:
        raise RuntimeError(
            f"FunctionError={result.function_error!r} on {fn.function_name}"
        )
    return parse_report(result.log)


def run_cold(
    client: Any,
    fn: LambdaFn,
    n: int,
    progress_callback: Callable[[int], None] | None = None,
) -> list[Report]:
    reports: list[Report] = []
    for i in range(n):
        with set_bench_version(client, fn.function_name, str(uuid.uuid4())):
            result = invoke(client, fn.function_name)
        report = _check_result(result, fn)
        if report is not None:
            reports.append(report)
        if progress_callback is not None:
            progress_callback(i + 1)
    return reports


def run_warm(
    client: Any,
    fn: LambdaFn,
    n: int,
    progress_callback: Callable[[int], None] | None = None,
) -> list[Report]:
    prime = invoke(client, fn.function_name)
    _check_result(prime, fn)  # raises on OOM or function error

    reports: list[Report] = []
    for i in range(n):
        result = invoke(client, fn.function_name)
        report = _check_result(result, fn)
        if report is not None:
            reports.append(report)
        if progress_callback is not None:
            progress_callback(i + 1)
    return reports
