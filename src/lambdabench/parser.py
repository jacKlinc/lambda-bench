import logging
import re
from dataclasses import dataclass

from lambdabench.config import LOG_TRUNCATION_LIMIT

logger = logging.getLogger(__name__)

_REPORT_RE = re.compile(
    r"REPORT RequestId:\s*(?P<request_id>\S+)"
    r"\s+Duration:\s*(?P<duration>[0-9.]+)\s*ms"
    r"\s+Billed Duration:\s*(?P<billed_duration>[0-9]+)\s*ms"
    r"\s+Memory Size:\s*(?P<memory_size>[0-9]+)\s*MB"
    r"\s+Max Memory Used:\s*(?P<max_memory_used>[0-9]+)\s*MB"
    r"(?:\s+Init Duration:\s*(?P<init_duration>[0-9.]+)\s*ms)?",
)


@dataclass
class Report:
    request_id: str
    duration_ms: float
    billed_duration_ms: int
    memory_size_mb: int
    max_memory_used_mb: int
    init_duration_ms: float | None


def parse_report(log: str) -> Report | None:
    """Parse a Lambda REPORT line from log output.

    Returns None and warns if the log appears truncated or no REPORT line found.
    """
    if len(log) >= LOG_TRUNCATION_LIMIT:
        logger.warning(
            "Log appears truncated (%d chars >= %d limit); skipping parse.",
            len(log),
            LOG_TRUNCATION_LIMIT,
        )
        return None

    m = _REPORT_RE.search(log)
    if m is None:
        logger.warning("No REPORT line found in log.")
        return None

    init_raw = m.group("init_duration")
    return Report(
        request_id=m.group("request_id"),
        duration_ms=float(m.group("duration")),
        billed_duration_ms=int(m.group("billed_duration")),
        memory_size_mb=int(m.group("memory_size")),
        max_memory_used_mb=int(m.group("max_memory_used")),
        init_duration_ms=float(init_raw) if init_raw is not None else None,
    )
