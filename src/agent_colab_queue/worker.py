"""Colab-side long-running worker.

Paste an instantiation into one Colab cell and call .run(). Loops forever (or
until worker/stop.flag), polling for new job specs and updating results.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .config import WorkspaceConfig, load_workspace


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _read_log_tail(log_path: str, n_bytes: int = 4000) -> tuple[int, str]:
    try:
        size = os.path.getsize(log_path)
    except OSError:
        return 0, ""
    try:
        with open(log_path, "rb") as f:
            if size > n_bytes:
                f.seek(-n_bytes, os.SEEK_END)
            data = f.read()
        return size, data.decode("utf-8", errors="replace")
    except OSError:
        return size, ""


class Worker:
    """Generalised drive-queue worker. Loops on jobs/ in a repo and writes results to Drive."""

    def __init__(
        self,
        repo_dir: str,
        drive_workspace_path: str,
        jobs_dir_in_repo: str = "jobs",
        poll_interval_s: float = 5.0,
        pull_interval_s: float = 10.0,
        result_update_s: float = 3.0,
        heartbeat_s: float = 5.0,
        log_dir: str = "/tmp/drive_queue_logs",
    ):
        self.repo_dir = Path(repo_dir)
        self.jobs_dir = self.repo_dir / jobs_dir_in_repo
        self.drive_base = Path(drive_workspace_path)
        self.results_dir = self.drive_base / "results"
        self.worker_dir = self.drive_base / "worker"
        self.heartbeat_path = self.worker_dir / "heartbeat.json"
        self.stop_flag = self.worker_dir / "stop.flag"
        self.log_dir = Path(log_dir)

        self.poll_interval_s = poll_interval_s
        self.pull_interval_s = pull_interval_s
        self.result_update_s = result_update_s
        self.heartbeat_s = heartbeat_s

        self.active_jobs: dict[str, dict[str, Any]] = {}

        for d in [self.results_dir, self.worker_dir, self.log_dir]:
            d.mkdir(parents=True, exist_ok=True)

    # ---- factories ----

    @classmethod
    def from_workspace(
        cls,
        name: str,
        config_path: Optional[Path] = None,
        **overrides,
    ) -> "Worker":
        """Build a Worker from a named workspace config (looks up workspaces.yaml)."""
        ws = load_workspace(name, config_path)
        return cls.from_config(ws, **overrides)

    @classmethod
    def from_config(cls, ws: WorkspaceConfig, **overrides) -> "Worker":
        repo_dir = overrides.pop("repo_dir", None) or ws.colab_repo_local()
        drive_workspace_path = overrides.pop("drive_workspace_path", None) or ws.colab_drive_workspace()
        jobs_dir_in_repo = overrides.pop("jobs_dir_in_repo", None) or ws.jobs_dir_in_repo
        return cls(
            repo_dir=repo_dir,
            drive_workspace_path=drive_workspace_path,
            jobs_dir_in_repo=jobs_dir_in_repo,
            **overrides,
        )

    # ---- helpers ----

    def _write_result(self, job_id: str, data: dict) -> None:
        path = self.results_dir / f"{job_id}.json"
        path.write_text(json.dumps(data, indent=2))

    def _git_pull(self) -> None:
        try:
            subprocess.run(
                ["git", "-C", str(self.repo_dir), "pull"],
                capture_output=True, text=True, timeout=30,
            )
        except Exception:  # noqa: BLE001
            pass

    def _write_heartbeat(self) -> None:
        try:
            self.heartbeat_path.write_text(json.dumps({
                "updated_at": _utcnow_iso(),
                "active_jobs": list(self.active_jobs.keys()),
                "poll_interval_s": self.poll_interval_s,
                "pull_interval_s": self.pull_interval_s,
                "repo_dir": str(self.repo_dir),
                "jobs_dir": str(self.jobs_dir),
                "results_dir": str(self.results_dir),
            }))
        except OSError:
            pass

    def _start_job(self, spec: dict) -> dict:
        job_id = spec["id"]
        log_path = spec.get("log_path") or str(self.log_dir / f"{job_id}.log")
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        log_fd = open(log_path, "wb", buffering=0)

        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env.update({str(k): str(v) for k, v in spec.get("env", {}).items()})

        cwd = spec.get("cwd") or str(self.repo_dir)

        proc = subprocess.Popen(
            spec["cmd"],
            stdout=log_fd,
            stderr=subprocess.STDOUT,
            bufsize=0,
            start_new_session=True,
            env=env,
            cwd=cwd,
        )
        return {
            "spec": spec,
            "proc": proc,
            "log_path": log_path,
            "started_at": _utcnow_iso(),
            "started_t": time.time(),
            "log_fd": log_fd,
        }

    def _refresh_result(self, job_id: str, active: dict) -> str:
        spec = active["spec"]
        proc = active["proc"]
        pid_alive = _is_pid_alive(proc.pid)
        marker = spec.get("done_marker")
        marker_exists = bool(marker) and os.path.exists(marker)
        log_size, log_tail = _read_log_tail(active["log_path"])

        exit_code = proc.poll()

        if marker_exists and not pid_alive:
            state = "done"
        elif marker_exists and pid_alive:
            state = "finishing"
        elif not marker_exists and pid_alive:
            state = "running"
        else:
            state = "crashed"

        timeout_s = spec.get("timeout_s")
        if state == "running" and timeout_s and (time.time() - active["started_t"]) > float(timeout_s):
            try:
                os.killpg(os.getpgid(proc.pid), 15)  # SIGTERM
            except (OSError, ProcessLookupError):
                pass
            state = "timeout"

        self._write_result(job_id, {
            "id": job_id,
            "state": state,
            "started_at": active["started_at"],
            "finished_at": _utcnow_iso() if state in ("done", "crashed", "timeout") else None,
            "elapsed_s": time.time() - active["started_t"],
            "pid": proc.pid,
            "pid_alive": pid_alive,
            "marker_exists": marker_exists,
            "log_size": log_size,
            "log_tail": log_tail,
            "exit_code": exit_code,
            "cmd": spec.get("cmd"),
            "done_marker": marker,
        })
        return state

    # ---- main loop ----

    def run(self, verbose: bool = True) -> None:
        last_pull = 0.0
        last_heartbeat = 0.0
        last_result_refresh: dict[str, float] = {}

        if verbose:
            print(f"[acq-worker] start @ {_utcnow_iso()}")
            print(f"[acq-worker] repo_dir={self.repo_dir}")
            print(f"[acq-worker] drive_base={self.drive_base}")
            print(f"[acq-worker] poll={self.poll_interval_s}s pull={self.pull_interval_s}s")
            print(f"[acq-worker] stop via: !touch {self.stop_flag}")

        try:
            while not self.stop_flag.exists():
                now = time.time()

                if now - last_heartbeat >= self.heartbeat_s:
                    self._write_heartbeat()
                    last_heartbeat = now

                if now - last_pull >= self.pull_interval_s:
                    self._git_pull()
                    last_pull = now

                if self.jobs_dir.exists():
                    for job_file in sorted(self.jobs_dir.glob("*.json")):
                        try:
                            spec = json.loads(job_file.read_text(encoding="utf-8"))
                            job_id = spec.get("id")
                        except (OSError, json.JSONDecodeError):
                            continue
                        if not job_id or job_id in self.active_jobs:
                            continue

                        res_path = self.results_dir / f"{job_id}.json"
                        if res_path.exists():
                            try:
                                prior = json.loads(res_path.read_text(encoding="utf-8"))
                                if prior.get("state") in ("done", "crashed", "timeout"):
                                    continue
                            except (OSError, json.JSONDecodeError):
                                pass

                        if verbose:
                            print(f"[acq-worker] claiming {job_id}")
                        self._write_result(job_id, {
                            "id": job_id,
                            "state": "claimed",
                            "claimed_at": _utcnow_iso(),
                            "cmd": spec.get("cmd"),
                        })
                        try:
                            self.active_jobs[job_id] = self._start_job(spec)
                            if verbose:
                                print(f"[acq-worker]   pid={self.active_jobs[job_id]['proc'].pid}")
                        except Exception as e:  # noqa: BLE001
                            if verbose:
                                print(f"[acq-worker]   start failed: {e}")
                            self._write_result(job_id, {
                                "id": job_id,
                                "state": "crashed",
                                "error": str(e),
                                "traceback": traceback.format_exc(),
                                "finished_at": _utcnow_iso(),
                            })

                for job_id in list(self.active_jobs.keys()):
                    if now - last_result_refresh.get(job_id, 0) < self.result_update_s:
                        continue
                    active = self.active_jobs[job_id]
                    state = self._refresh_result(job_id, active)
                    last_result_refresh[job_id] = now
                    if state in ("done", "crashed", "timeout"):
                        if verbose:
                            print(f"[acq-worker] {job_id} -> {state} (exit={active['proc'].poll()})")
                        try:
                            active["log_fd"].close()
                        except OSError:
                            pass
                        del self.active_jobs[job_id]
                        last_result_refresh.pop(job_id, None)

                time.sleep(self.poll_interval_s)

            if verbose:
                print(f"[acq-worker] stop flag detected, exiting at {_utcnow_iso()}")
        except KeyboardInterrupt:
            if verbose:
                print(f"[acq-worker] KeyboardInterrupt at {_utcnow_iso()}")
        finally:
            for active in self.active_jobs.values():
                try:
                    active["log_fd"].close()
                except OSError:
                    pass
