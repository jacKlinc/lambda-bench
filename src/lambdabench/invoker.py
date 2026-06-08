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
            function_error: str | None = response.get("FunctionError")
            is_oom = False
            if function_error:
                try:
                    payload = json.loads(response["Payload"].read())
                    error_type = payload.get("errorType", "")
                    error_msg = payload.get("errorMessage", "")
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
            return InvokeResult(log=log, function_error=function_error, is_oom=is_oom)
        except botocore.exceptions.ClientError as exc:
            if exc.response["Error"]["Code"] in _RETRYABLE_CODES:
                last_error = exc
                continue
            raise

    assert last_error is not None
    raise last_error
