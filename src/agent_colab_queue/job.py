"""JobSpec + JobResult dataclasses."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class JobSpec:
    """A single unit of work for the Colab worker to execute.

    Required:
        id           — unique slug for this job (used in filenames)
        cmd          — argv list (e.g. ["python", "scripts/foo.py", "--arg", "1"])
        done_marker  — path that the command writes only on success (used to detect "done")

    Optional:
        log_path     — where to capture stdout+stderr (default /tmp/drive_queue_logs/<id>.log)
        cwd          — working directory (default = repo_local of the workspace)
        env          — extra environment variables
        timeout_s    — kill (SIGTERM) after this many seconds; None = no timeout
        priority     — higher runs first if worker has a queue (default 0; not yet implemented)
        labels       — for future filtering (e.g. {"requires": "gpu"})
    """

    id: str
    cmd: list[str]
    done_marker: str
    log_path: str | None = None
    cwd: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    timeout_s: int | None = None
    priority: int = 0
    labels: dict[str, str] = field(default_factory=dict)
    created_at: str = field(default_factory=_utcnow_iso)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # Drop empty optionals to keep the on-disk JSON tidy
        if not d.get("env"):
            d.pop("env")
        if not d.get("labels"):
            d.pop("labels")
        if d.get("log_path") is None:
            d.pop("log_path")
        if d.get("cwd") is None:
            d.pop("cwd")
        if d.get("timeout_s") is None:
            d.pop("timeout_s")
        if d.get("priority") == 0:
            d.pop("priority")
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "JobSpec":
        # Tolerate extra fields (forward compat); fill in defaults
        return cls(
            id=data["id"],
            cmd=list(data["cmd"]),
            done_marker=data["done_marker"],
            log_path=data.get("log_path"),
            cwd=data.get("cwd"),
            env={str(k): str(v) for k, v in (data.get("env") or {}).items()},
            timeout_s=data.get("timeout_s"),
            priority=int(data.get("priority", 0)),
            labels={str(k): str(v) for k, v in (data.get("labels") or {}).items()},
            created_at=data.get("created_at") or _utcnow_iso(),
        )


@dataclass
class JobResult:
    """Live status of a job as written to Drive results/<id>.json."""

    id: str
    state: str  # claimed | running | finishing | done | crashed | timeout
    started_at: str | None
    finished_at: str | None
    elapsed_s: float
    pid: int | None
    pid_alive: bool
    marker_exists: bool
    log_size: int
    log_tail: str
    exit_code: int | None
    cmd: list[str] | None = None
    done_marker: str | None = None
    error: str | None = None
    traceback: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
