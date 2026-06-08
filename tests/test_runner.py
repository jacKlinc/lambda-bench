from unittest.mock import MagicMock, call, patch

import botocore.exceptions
import pytest

from lambdabench.config import LambdaFn
from lambdabench.invoker import InvokeResult
from lambdabench.runner import (
    _check_result,
    _wait_until_active,
    run_cold,
    run_warm,
    set_bench_version,
    set_memory,
)
from tests.fixtures.sample_logs import WARM_EXEC

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_fn(memory_mb: int = 512) -> LambdaFn:
    return LambdaFn(label="test", function_name="my-fn", memory_mb=memory_mb, variant="python")


def _cfg(state: str = "Active", update_status: str = "Successful") -> dict:
    return {"State": state, "LastUpdateStatus": update_status, "Environment": {"Variables": {}}}


def _conflict_error() -> botocore.exceptions.ClientError:
    return botocore.exceptions.ClientError(
        {"Error": {"Code": "ResourceConflictException", "Message": "Function update in progress"}},
        "UpdateFunctionConfiguration",
    )


def _ok_result(log: str = WARM_EXEC) -> InvokeResult:
    return InvokeResult(log=log, function_error=None, is_oom=False)


def _oom_result() -> InvokeResult:
    return InvokeResult(log="signal: killed", function_error="Unhandled", is_oom=True)


def _error_result() -> InvokeResult:
    return InvokeResult(log="oops", function_error="Unhandled", is_oom=False)


# ---------------------------------------------------------------------------
# _wait_until_active
# ---------------------------------------------------------------------------

def test_wait_until_active_returns_immediately():
    client = MagicMock()
    client.get_function_configuration.return_value = _cfg()
    _wait_until_active(client, "my-fn", timeout=5, poll=0)
    client.get_function_configuration.assert_called_once_with(FunctionName="my-fn")


def test_wait_until_active_polls_until_ready():
    client = MagicMock()
    client.get_function_configuration.side_effect = [
        _cfg("Active", "InProgress"),
        _cfg("Active", "InProgress"),
        _cfg("Active", "Successful"),
    ]
    with patch("lambdabench.runner.time.sleep"):
        _wait_until_active(client, "my-fn", timeout=60, poll=0)
    assert client.get_function_configuration.call_count == 3


def test_wait_until_active_raises_on_timeout():
    client = MagicMock()
    client.get_function_configuration.return_value = _cfg("Active", "InProgress")
    with patch("lambdabench.runner.time.sleep"):
        with pytest.raises(TimeoutError, match="my-fn"):
            _wait_until_active(client, "my-fn", timeout=0.0001, poll=0)


# ---------------------------------------------------------------------------
# set_bench_version
# ---------------------------------------------------------------------------

def test_set_bench_version_updates_and_resets():
    client = MagicMock()
    client.get_function_configuration.return_value = _cfg()
    with patch("lambdabench.runner._wait_until_active"):
        with set_bench_version(client, "my-fn", "v-abc"):
            pass

    update_calls = client.update_function_configuration.call_args_list
    assert len(update_calls) == 2
    set_val = update_calls[0].kwargs["Environment"]["Variables"]["BENCH_VERSION"]
    reset_val = update_calls[1].kwargs["Environment"]["Variables"]["BENCH_VERSION"]
    assert set_val == "v-abc"
    assert reset_val == ""


def test_set_bench_version_resets_even_on_exception():
    client = MagicMock()
    client.get_function_configuration.return_value = _cfg()
    with patch("lambdabench.runner._wait_until_active"):
        with pytest.raises(ValueError):
            with set_bench_version(client, "my-fn", "v-xyz"):
                raise ValueError("boom")

    update_calls = client.update_function_configuration.call_args_list
    assert len(update_calls) == 2
    reset_val = update_calls[1].kwargs["Environment"]["Variables"]["BENCH_VERSION"]
    assert reset_val == ""


def test_set_bench_version_preserves_existing_env_vars():
    client = MagicMock()
    client.get_function_configuration.return_value = {
        "State": "Active",
        "LastUpdateStatus": "Successful",
        "Environment": {"Variables": {"EXISTING": "keep-me"}},
    }
    with patch("lambdabench.runner._wait_until_active"):
        with set_bench_version(client, "my-fn", "v-1"):
            pass

    set_call = client.update_function_configuration.call_args_list[0]
    vars_ = set_call.kwargs["Environment"]["Variables"]
    assert vars_["EXISTING"] == "keep-me"
    assert vars_["BENCH_VERSION"] == "v-1"


# ---------------------------------------------------------------------------
# ResourceConflictException retry
# ---------------------------------------------------------------------------

def test_set_bench_version_retries_on_conflict():
    client = MagicMock()
    client.get_function_configuration.return_value = _cfg()
    client.update_function_configuration.side_effect = [
        _conflict_error(),  # first set attempt conflicts
        None,               # retry succeeds
        None,               # reset in finally
    ]
    with (
        patch("lambdabench.runner._wait_until_active"),
        patch("lambdabench.runner.time.sleep"),
    ):
        with set_bench_version(client, "my-fn", "v-retry"):
            pass
    # 3 calls: failed attempt, retry success, reset
    assert client.update_function_configuration.call_count == 3


def test_set_bench_version_raises_after_max_conflict_retries():
    client = MagicMock()
    client.get_function_configuration.return_value = _cfg()
    client.update_function_configuration.side_effect = _conflict_error()
    with (
        patch("lambdabench.runner._wait_until_active"),
        patch("lambdabench.runner.time.sleep"),
    ):
        with pytest.raises(botocore.exceptions.ClientError) as exc_info:
            with set_bench_version(client, "my-fn", "v-fail"):
                pass  # pragma: no cover
    assert exc_info.value.response["Error"]["Code"] == "ResourceConflictException"


def test_set_memory_retries_on_conflict():
    client = MagicMock()
    client.get_function_configuration.return_value = _cfg()
    client.update_function_configuration.side_effect = [_conflict_error(), None]
    with (
        patch("lambdabench.runner._wait_until_active"),
        patch("lambdabench.runner.time.sleep") as mock_sleep,
    ):
        set_memory(client, "my-fn", 1024)
    assert client.update_function_configuration.call_count == 2
    mock_sleep.assert_called_once()


def test_conflict_retry_uses_exponential_backoff():
    client = MagicMock()
    client.get_function_configuration.return_value = _cfg()
    client.update_function_configuration.side_effect = [
        _conflict_error(), _conflict_error(), None
    ]
    with (
        patch("lambdabench.runner._wait_until_active"),
        patch("lambdabench.runner.time.sleep") as mock_sleep,
    ):
        set_memory(client, "my-fn", 512)
    delays = [c.args[0] for c in mock_sleep.call_args_list]
    assert delays == [1.0, 2.0]


# ---------------------------------------------------------------------------
# _check_result
# ---------------------------------------------------------------------------

def test_check_result_skips_oom_and_warns(caplog):
    import logging
    fn = _make_fn()
    with caplog.at_level(logging.WARNING, logger="lambdabench.runner"):
        result = _check_result(_oom_result(), fn)
    assert result is None
    assert any("oom" in m.lower() for m in caplog.messages)


def test_check_result_parses_log_on_function_error():
    fn = _make_fn()
    # Non-OOM function errors: _check_result tries to parse the log (Lambda always has REPORT)
    result = _check_result(_error_result(), fn)
    # "oops" log has no REPORT line, so parse_report returns None — not an error skip
    assert result is None


def test_check_result_returns_report_on_success():
    fn = _make_fn()
    report = _check_result(_ok_result(WARM_EXEC), fn)
    assert report is not None
    assert report.init_duration_ms is None


# ---------------------------------------------------------------------------
# run_cold
# ---------------------------------------------------------------------------

def test_run_cold_invokes_n_times():
    fn = _make_fn()
    with (
        patch("lambdabench.runner.set_bench_version"),
        patch("lambdabench.runner.invoke", return_value=_ok_result()) as mock_invoke,
    ):
        reports = run_cold(MagicMock(), fn, n=3)
    assert mock_invoke.call_count == 3
    assert len(reports) == 3


def test_run_cold_calls_progress_callback():
    fn = _make_fn()
    cb = MagicMock()
    with (
        patch("lambdabench.runner.set_bench_version"),
        patch("lambdabench.runner.invoke", return_value=_ok_result()),
    ):
        run_cold(MagicMock(), fn, n=3, progress_callback=cb)
    assert cb.call_args_list == [call(1), call(2), call(3)]


def test_run_cold_raises_when_all_fail(caplog):
    import logging
    fn = _make_fn()
    with (
        patch("lambdabench.runner.set_bench_version"),
        patch("lambdabench.runner.invoke", return_value=_oom_result()),
    ):
        with caplog.at_level(logging.WARNING, logger="lambdabench.runner"):
            with pytest.raises(RuntimeError, match="all.*cold invocations failed"):
                run_cold(MagicMock(), fn, n=3)
    assert any("oom" in m.lower() for m in caplog.messages)


def test_run_cold_skips_partial_failures_and_returns_successes():
    fn = _make_fn()
    with (
        patch("lambdabench.runner.set_bench_version"),
        patch(
            "lambdabench.runner.invoke",
            side_effect=[_oom_result(), _ok_result(), _oom_result()],
        ),
    ):
        reports = run_cold(MagicMock(), fn, n=3)
    assert len(reports) == 1


def test_run_cold_raises_when_all_truncated(caplog):
    import logging
    fn = _make_fn()
    truncated_result = InvokeResult(log="x" * 4096, function_error=None, is_oom=False)
    with (
        patch("lambdabench.runner.set_bench_version"),
        patch("lambdabench.runner.invoke", return_value=truncated_result),
    ):
        with caplog.at_level(logging.WARNING):
            with pytest.raises(RuntimeError, match="all.*cold invocations failed"):
                run_cold(MagicMock(), fn, n=2)


# ---------------------------------------------------------------------------
# run_warm
# ---------------------------------------------------------------------------

def test_run_warm_primes_then_warms():
    fn = _make_fn()
    with patch("lambdabench.runner.invoke", return_value=_ok_result()) as mock_invoke:
        reports = run_warm(MagicMock(), fn, n=3)
    # 1 prime + 3 warm
    assert mock_invoke.call_count == 4
    assert len(reports) == 3


def test_run_warm_tolerates_prime_oom_and_continues(caplog):
    import logging
    fn = _make_fn()
    # prime OOMs, then warm runs succeed
    with patch(
        "lambdabench.runner.invoke",
        side_effect=[_oom_result(), _ok_result(), _ok_result(), _ok_result()],
    ):
        with caplog.at_level(logging.WARNING, logger="lambdabench.runner"):
            reports = run_warm(MagicMock(), fn, n=3)
    assert len(reports) == 3
    assert any("prime" in m.lower() for m in caplog.messages)


def test_run_warm_tolerates_prime_function_error_and_continues(caplog):
    import logging
    fn = _make_fn()
    with patch(
        "lambdabench.runner.invoke",
        side_effect=[_error_result(), _ok_result(), _ok_result()],
    ):
        with caplog.at_level(logging.WARNING, logger="lambdabench.runner"):
            reports = run_warm(MagicMock(), fn, n=2)
    assert len(reports) == 2
    assert any("prime" in m.lower() for m in caplog.messages)


def test_run_warm_raises_when_all_warm_fail():
    fn = _make_fn()
    with patch(
        "lambdabench.runner.invoke",
        side_effect=[_ok_result(), _oom_result(), _oom_result(), _oom_result()],
    ):
        with pytest.raises(RuntimeError, match="all.*warm invocations failed"):
            run_warm(MagicMock(), fn, n=3)


def test_run_warm_skips_oom_warm_iteration(caplog):
    import logging
    fn = _make_fn()
    with patch(
        "lambdabench.runner.invoke",
        side_effect=[_ok_result(), _ok_result(), _oom_result()],
    ):
        with caplog.at_level(logging.WARNING, logger="lambdabench.runner"):
            reports = run_warm(MagicMock(), fn, n=2)
    # prime ok, warm[0] ok → 1 report; warm[1] OOM → skipped
    assert len(reports) == 1


def test_run_warm_calls_progress_callback():
    fn = _make_fn()
    cb = MagicMock()
    with patch("lambdabench.runner.invoke", return_value=_ok_result()):
        run_warm(MagicMock(), fn, n=4, progress_callback=cb)
    assert cb.call_count == 4
    assert cb.call_args_list == [call(1), call(2), call(3), call(4)]
