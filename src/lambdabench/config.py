from dataclasses import dataclass

LOG_TRUNCATION_LIMIT = 4096
DEFAULT_COLD_ITERS = 15
DEFAULT_WARM_ITERS = 15

VARIANT_COLOURS: dict[str, str] = {
    "python": "#3572A5",
    "go": "#00ADD8",
}


@dataclass
class LambdaFn:
    label: str
    function_name: str
    memory_mb: int
    variant: str
