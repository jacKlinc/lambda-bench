"""Hardcoded Lambda REPORT log strings covering edge cases for parser tests."""

COLD_START = (
    "START RequestId: aaa-111 Version: $LATEST\n"
    "END RequestId: aaa-111\n"
    "REPORT RequestId: aaa-111\t"
    "Duration: 123.45 ms\t"
    "Billed Duration: 200 ms\t"
    "Memory Size: 512 MB\t"
    "Max Memory Used: 78 MB\t"
    "Init Duration: 302.50 ms"
)

WARM_EXEC = (
    "START RequestId: bbb-222 Version: $LATEST\n"
    "END RequestId: bbb-222\n"
    "REPORT RequestId: bbb-222\t"
    "Duration: 45.67 ms\t"
    "Billed Duration: 46 ms\t"
    "Memory Size: 1024 MB\t"
    "Max Memory Used: 95 MB"
)

# Simulates a log that hit the 4096-byte CloudWatch truncation boundary.
TRUNCATED_LOG = "A" * 4096

# A log that has a REPORT but also happens to be exactly at the limit (should be skipped).
TRUNCATED_WITH_REPORT = (
    "REPORT RequestId: ccc-333\t"
    "Duration: 10.00 ms\t"
    "Billed Duration: 100 ms\t"
    "Memory Size: 256 MB\t"
    "Max Memory Used: 50 MB"
).ljust(4096, " ")

# Valid log but the Init Duration field is absent (normal warm execution variant).
NO_INIT_DURATION = (
    "REPORT RequestId: ddd-444\t"
    "Duration: 88.00 ms\t"
    "Billed Duration: 100 ms\t"
    "Memory Size: 3008 MB\t"
    "Max Memory Used: 210 MB"
)

# Log with no REPORT line at all (e.g. function crashed before emitting one).
NO_REPORT_LINE = "START RequestId: eee-555 Version: $LATEST\nTask timed out after 30.00 seconds"

# High-memory cold start — tests large numeric values round-trip correctly.
HIGH_MEMORY_COLD = (
    "REPORT RequestId: fff-666\t"
    "Duration: 5000.01 ms\t"
    "Billed Duration: 5001 ms\t"
    "Memory Size: 10240 MB\t"
    "Max Memory Used: 9876 MB\t"
    "Init Duration: 1200.99 ms"
)
