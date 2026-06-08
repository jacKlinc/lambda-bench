import base64
import logging
from unittest.mock import MagicMock, call, patch

import botocore.exceptions
import pytest

from lambdabench.invoker import _BASE_DELAY, _MAX_RETRIES, InvokeResult, invoke

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _encode_log(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


def _make_response(log: str = "", function_error: str | None = None) -> dict:
    r: dict = {"StatusCode": 200, "LogResult": _encode_log(log)}
    if function_error is not None:
        r["FunctionError"] = function_error
    return r


def _client_error(code: str) -> botocore.exceptions.ClientError:
    return botocore.exceptions.ClientError(
        {"Error": {"Code": code, "Message": code}}, "Invoke"
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_invoke_success_returns_result():
    client = MagicMock()
    client.invoke.return_value = _make_response("some log")
    result = invoke(client, "my-fn")
    assert isinstance(result, InvokeResult)
    assert result.log == "some log"
    assert result.function_error is None
    assert result.is_oom is False


def test_invoke_decodes_base64_log():
    log_text = "REPORT RequestId: abc\tDuration: 10 ms"
    client = MagicMock()
    client.invoke.return_value = _make_response(log_text)
    result = invoke(client, "my-fn")
    assert result.log == log_text


def test_invoke_retries_on_throttle():
    client = MagicMock()
    client.invoke.side_effect = [
        _client_error("TooManyRequestsException"),
        _client_error("TooManyRequestsException"),
        _make_response("ok"),
    ]
    with patch("lambdabench.invoker.time.sleep") as mock_sleep:
        result = invoke(client, "my-fn")
    assert result.log == "ok"
    assert mock_sleep.call_count == 2
    assert mock_sleep.call_args_list == [
        call(_BASE_DELAY * 1),
        call(_BASE_DELAY * 2),
    ]


def test_invoke_retries_on_service_exception():
    client = MagicMock()
    client.invoke.side_effect = [
        _client_error("ServiceException"),
        _make_response("recovered"),
    ]
    with patch("lambdabench.invoker.time.sleep"):
        result = invoke(client, "my-fn")
    assert result.log == "recovered"


def test_invoke_raises_after_max_retries():
    client = MagicMock()
    client.invoke.side_effect = _client_error("TooManyRequestsException")
    with patch("lambdabench.invoker.time.sleep"):
        with pytest.raises(botocore.exceptions.ClientError) as exc_info:
            invoke(client, "my-fn")
    assert exc_info.value.response["Error"]["Code"] == "TooManyRequestsException"
    assert client.invoke.call_count == _MAX_RETRIES + 1


def test_invoke_does_not_retry_non_retryable():
    client = MagicMock()
    client.invoke.side_effect = _client_error("ResourceNotFoundException")
    with patch("lambdabench.invoker.time.sleep") as mock_sleep:
        with pytest.raises(botocore.exceptions.ClientError):
            invoke(client, "my-fn")
    mock_sleep.assert_not_called()
    assert client.invoke.call_count == 1


def test_invoke_detects_oom():
    oom_log = "START RequestId: x\nRuntime exited with error: signal: killed\nEND RequestId: x\n"
    client = MagicMock()
    client.invoke.return_value = _make_response(oom_log, function_error="Unhandled")
    result = invoke(client, "my-fn")
    assert result.is_oom is True
    assert result.function_error == "Unhandled"


def test_invoke_function_error_without_oom_pattern():
    error_log = "START RequestId: x\nException: something bad\nEND RequestId: x\n"
    client = MagicMock()
    client.invoke.return_value = _make_response(error_log, function_error="Unhandled")
    result = invoke(client, "my-fn")
    assert result.function_error == "Unhandled"
    assert result.is_oom is False


def test_invoke_no_function_error_is_not_oom():
    oom_like_log = "signal: killed"
    client = MagicMock()
    client.invoke.return_value = _make_response(oom_like_log, function_error=None)
    result = invoke(client, "my-fn")
    assert result.is_oom is False


def test_invoke_warns_on_truncated_log(caplog):
    long_log = "x" * 4096
    client = MagicMock()
    client.invoke.return_value = _make_response(long_log)
    with caplog.at_level(logging.WARNING, logger="lambdabench.invoker"):
        invoke(client, "my-fn")
    assert any("truncation" in m.lower() for m in caplog.messages)


def test_invoke_exponential_backoff_delays():
    client = MagicMock()
    client.invoke.side_effect = [
        _client_error("TooManyRequestsException"),
        _client_error("TooManyRequestsException"),
        _client_error("TooManyRequestsException"),
        _make_response("ok"),
    ]
    with patch("lambdabench.invoker.time.sleep") as mock_sleep:
        invoke(client, "my-fn")
    delays = [c.args[0] for c in mock_sleep.call_args_list]
    assert delays == [_BASE_DELAY * 1, _BASE_DELAY * 2, _BASE_DELAY * 4]
