import base64
import json
import logging
import time
from dataclasses import dataclass
from typing import Any

import botocore.exceptions

from lambdabench.config import LOG_TRUNCATION_LIMIT

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3
_BASE_DELAY = 2.0
_RETRYABLE_CODES = frozenset({"TooManyRequestsException", "ServiceException"})
_OOM_LOG_PATTERNS = ("Runtime exited", "signal: killed", "MemoryError", "OutOfMemoryError")


@dataclass
class InvokeResult:
    log: str
    function_error: str | None
    is_oom: bool
    status_code: int | None = None
    """`statusCode` from the returned payload, for handlers that answer with an HTTP-shaped
    response. None when the payload isn't JSON or carries no `statusCode`. Note this is the
    *function's* status, not the invocation's: a handler returning 401 is still a successful
    invocation with no FunctionError, so this is the only way to notice it."""


def _extract_status_code(raw_payload: bytes) -> int | None:
    try:
        parsed = json.loads(raw_payload)
    except Exception:
        return None
    if not isinstance(parsed, dict):
        return None
    code = parsed.get("statusCode")
    return code if isinstance(code, int) else None


def invoke(
    client: Any,
    function_name: str,
    payload: bytes = b"{}",
) -> InvokeResult:
    last_error: botocore.exceptions.ClientError | None = None
    for attempt in range(_MAX_RETRIES + 1):
        if attempt > 0:
            delay = _BASE_DELAY * (2 ** (attempt - 1))
            logger.warning(
                "Retry %d/%d for %s after %.1fs (reason: %s)",
                attempt,
                _MAX_RETRIES,
                function_name,
                delay,
                last_error,
            )
            time.sleep(delay)
        try:
            response = client.invoke(
                FunctionName=function_name,
                LogType="Tail",
                Payload=payload,
            )
            log = base64.b64decode(response.get("LogResult", "")).decode(
                "utf-8", errors="replace"
            )
            if len(log) >= LOG_TRUNCATION_LIMIT:
                logger.warning(
                    "Log for %s is %d chars (>= truncation limit %d).",
                    function_name,
                    len(log),
                    LOG_TRUNCATION_LIMIT,
                )
            # The payload is a stream that can only be read once, so read it here and
            # reuse the bytes for both the OOM check and the status code.
            payload_stream = response.get("Payload")
            raw_payload = payload_stream.read() if payload_stream is not None else b""
            function_error: str | None = response.get("FunctionError")
            is_oom = False
            status_code: int | None = None
            if function_error:
                try:
                    error_payload = json.loads(raw_payload)
                    error_type = error_payload.get("errorType", "")
                    error_msg = error_payload.get("errorMessage", "")
                    is_oom = (
                        "Runtime.ExitError" in error_type
                        or "signal: killed" in error_msg
                    )
                except Exception:
                    is_oom = any(p in log for p in _OOM_LOG_PATTERNS)
                if not is_oom:
                    logger.warning(
                        "FunctionError on %s (non-OOM): %s",
                        function_name,
                        function_error,
                    )
            else:
                status_code = _extract_status_code(raw_payload)
            return InvokeResult(
                log=log,
                function_error=function_error,
                is_oom=is_oom,
                status_code=status_code,
            )
        except botocore.exceptions.ClientError as exc:
            if exc.response["Error"]["Code"] in _RETRYABLE_CODES:
                last_error = exc
                continue
            raise

    assert last_error is not None
    raise last_error
