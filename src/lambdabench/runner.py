import logging
import time
import uuid
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import botocore.exceptions

from lambdabench.config import LambdaFn
from lambdabench.invoker import InvokeResult, invoke
from lambdabench.parser import Report, parse_report

logger = logging.getLogger(__name__)

_WAIT_TIMEOUT = 60.0
_WAIT_POLL = 2.0
_CONFLICT_RETRIES = 5
_CONFLICT_BASE_DELAY = 1.0


def _retry_on_conflict(fn: Callable[[], None]) -> None:
    """Call fn(), retrying on ResourceConflictException with exponential backoff."""
    for attempt in range(_CONFLICT_RETRIES + 1):
        try:
            fn()
            return
        except botocore.exceptions.ClientError as exc:
            if (
                exc.response["Error"]["Code"] == "ResourceConflictException"
                and attempt < _CONFLICT_RETRIES
            ):
                delay = _CONFLICT_BASE_DELAY * (2 ** attempt)
                logger.warning(
                    "ResourceConflictException (attempt %d/%d), retrying in %.1fs",
                    attempt + 1, _CONFLICT_RETRIES, delay,
                )
                time.sleep(delay)
                continue
            raise


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
        def _do() -> None:
            cfg = client.get_function_configuration(FunctionName=function_name)
            env_vars = dict(cfg.get("Environment", {}).get("Variables", {}))
            env_vars["BENCH_VERSION"] = val
            client.update_function_configuration(
                FunctionName=function_name,
                Environment={"Variables": env_vars},
            )
        _retry_on_conflict(_do)

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
        logger.warning("OOM on %s (%d MB) — skipping invocation.", fn.function_name, fn.memory_mb)
        return None
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
    if not reports:
        raise RuntimeError(
            f"all {n} cold invocations failed for {fn.function_name} ({fn.memory_mb} MB)"
            " — check logs above for details"
        )
    return reports


def run_warm(
    client: Any,
    fn: LambdaFn,
    n: int,
    progress_callback: Callable[[int], None] | None = None,
) -> list[Report]:
    prime = invoke(client, fn.function_name)
    if prime.is_oom:
        logger.warning(
            "Prime invocation OOMed for %s (%d MB) — warm results may be unreliable.",
            fn.function_name, fn.memory_mb,
        )
    elif prime.function_error:
        logger.warning(
            "Prime invocation errored for %s: %r — warm results may be unreliable.",
            fn.function_name, prime.function_error,
        )

    reports: list[Report] = []
    for i in range(n):
        result = invoke(client, fn.function_name)
        report = _check_result(result, fn)
        if report is not None:
            reports.append(report)
        if progress_callback is not None:
            progress_callback(i + 1)
    if not reports:
        raise RuntimeError(
            f"all {n} warm invocations failed for {fn.function_name} ({fn.memory_mb} MB)"
            " — check logs above for details"
        )
    return reports


def set_memory(client: Any, function_name: str, memory_mb: int) -> None:
    """Update function memory size and wait until the update is active."""
    _retry_on_conflict(
        lambda: client.update_function_configuration(FunctionName=function_name, MemorySize=memory_mb)
    )
    _wait_until_active(client, function_name)


@dataclass
class FnResult:
    fn: LambdaFn
    cold_reports: list[Report]
    warm_reports: list[Report]
