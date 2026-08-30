import base64
import io
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


@pytest.mark.parametrize(
    ("code", "failures", "expected_delays"),
    [
        ("TooManyRequestsException", 2, [_BASE_DELAY * 1, _BASE_DELAY * 2]),
        ("ServiceException", 1, [_BASE_DELAY * 1]),
        (
            "TooManyRequestsException",
            3,
            [_BASE_DELAY * 1, _BASE_DELAY * 2, _BASE_DELAY * 4],
        ),
    ],
)
def test_invoke_retries_with_exponential_backoff(code, failures, expected_delays):
    client = MagicMock()
    client.invoke.side_effect = [_client_error(code)] * failures + [
        _make_response("ok")
    ]
    with patch("lambdabench.invoker.time.sleep") as mock_sleep:
        result = invoke(client, "my-fn")
    assert result.log == "ok"
    assert mock_sleep.call_args_list == [call(d) for d in expected_delays]


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


@pytest.mark.parametrize(
    ("log", "function_error", "expected_oom"),
    [
        (
            "START RequestId: x\nRuntime exited with error: signal: killed\nEND RequestId: x\n",
            "Unhandled",
            True,
        ),
        (
            "START RequestId: x\nException: something bad\nEND RequestId: x\n",
            "Unhandled",
            False,
        ),
        # An OOM-looking log without a FunctionError is a healthy invocation.
        ("signal: killed", None, False),
    ],
)
def test_invoke_oom_detection(log, function_error, expected_oom):
    client = MagicMock()
    client.invoke.return_value = _make_response(log, function_error=function_error)
    result = invoke(client, "my-fn")
    assert result.is_oom is expected_oom
    assert result.function_error == function_error


def test_invoke_warns_on_truncated_log(caplog):
    long_log = "x" * 4096
    client = MagicMock()
    client.invoke.return_value = _make_response(long_log)
    with caplog.at_level(logging.WARNING, logger="lambdabench.invoker"):
        invoke(client, "my-fn")
    assert any("truncation" in m.lower() for m in caplog.messages)


# ---------------------------------------------------------------------------
# status_code extraction
# ---------------------------------------------------------------------------

def _resp(payload: bytes, log: str = "REPORT RequestId: x") -> dict:
    return {
        "LogResult": base64.b64encode(log.encode()).decode(),
        "Payload": io.BytesIO(payload),
    }


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (b'{"statusCode":200,"body":"ok"}', 200),
        (b'{"statusCode":401,"body":"unauthorized"}', 401),
        (b'{"result":"fine"}', None),
        (b"not json at all", None),
        (b'["a","list"]', None),
        (b'{"statusCode":"200"}', None),
    ],
)
def test_invoke_status_code_extraction(payload, expected):
    client = MagicMock()
    client.invoke.return_value = _resp(payload)
    assert invoke(client, "my-fn").status_code == expected


def test_invoke_passes_payload_to_client():
    client = MagicMock()
    client.invoke.return_value = _resp(b"{}")
    invoke(client, "my-fn", b'{"rawPath":"/mcp"}')
    assert client.invoke.call_args.kwargs["Payload"] == b'{"rawPath":"/mcp"}'
