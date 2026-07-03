import os
from pathlib import Path
from typing import Mapping

from pydantic import BaseModel, SecretStr


class PlatformSettings(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8787
    database_path: Path = Path("runs/platform.sqlite3")
    runs_dir: Path = Path("runs")
    max_active_runs: int = 1
    max_active_lanes: int = 2
    event_queue_size: int = 10_000
    event_batch_size: int = 100
    event_flush_interval_ms: int = 50
    cancel_grace_seconds: float = 10.0
    sse_heartbeat_seconds: float = 15.0
    auth_token: SecretStr | None = None
    allow_non_loopback: bool = False
    simulator_metadata_timeout_seconds: float = 15.0

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> "PlatformSettings":
        source = os.environ if environ is None else environ
        values: dict[str, object] = {}

        parsers = {
            "host": str,
            "port": int,
            "database_path": Path,
            "runs_dir": Path,
            "max_active_runs": int,
            "max_active_lanes": int,
            "event_queue_size": int,
            "event_batch_size": int,
            "event_flush_interval_ms": int,
            "cancel_grace_seconds": float,
            "sse_heartbeat_seconds": float,
            "auth_token": SecretStr,
            "allow_non_loopback": _parse_bool,
            "simulator_metadata_timeout_seconds": float,
        }

        for field_name, parser in parsers.items():
            env_name = f"TEST_PLATFORM_{field_name.upper()}"
            raw = source.get(env_name)
            if raw is None or raw == "":
                continue
            values[field_name] = parser(raw)

        return cls(**values)


def _parse_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    raise ValueError(f"Invalid boolean value: {value!r}")
