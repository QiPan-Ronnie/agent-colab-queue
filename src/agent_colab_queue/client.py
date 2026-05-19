"""Agent-side helpers: write a job spec into the local repo, then commit + push.

The MCP server in mcp_server.py wraps these. Library users can call them directly.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Optional

from .config import WorkspaceConfig, load_workspace
from .job import JobSpec


class SubmitResult(dict):
    """Dict-like return type with keys: ok, commit_sha, jobs_file_path,
    expected_drive_result_path, message."""


def _run_git(
    repo_local: str,
    args: list[str],
    ssh_key: Optional[str] = None,
    user_name: Optional[str] = None,
    user_email: Optional[str] = None,
) -> tuple[int, str, str]:
    env = os.environ.copy()
    if ssh_key:
        env["GIT_SSH_COMMAND"] = (
            f'ssh -i "{ssh_key}" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new'
        )
    cmd = ["git", "-C", repo_local]
    # Add config overrides per-command via -c flags
    if user_name:
        cmd += ["-c", f"user.name={user_name}"]
    if user_email:
        cmd += ["-c", f"user.email={user_email}"]
    cmd += args
    r = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=60)
    return r.returncode, r.stdout, r.stderr


def submit_job(
    workspace: str,
    job_id: str,
    cmd: list[str],
    done_marker: str,
    timeout_s: Optional[int] = None,
    env: Optional[dict] = None,
    log_path: Optional[str] = None,
    cwd: Optional[str] = None,
    config: Optional[WorkspaceConfig] = None,
    auto_commit: bool = True,
    auto_push: bool = True,
    commit_message: Optional[str] = None,
) -> SubmitResult:
    """Write a job spec to <repo_local>/<jobs_dir>/<id>.json and (optionally)
    commit + push it so the Colab worker can pick it up.

    Returns a SubmitResult dict (also accessible as attrs):
        ok                          : bool
        commit_sha                  : str | None
        jobs_file_path              : str (local path to the spec file)
        expected_drive_result_path  : str (where worker will write results/<id>.json on Drive)
        message                     : str (human-readable status)
    """
    ws = config or load_workspace(workspace)

    spec = JobSpec(
        id=job_id,
        cmd=list(cmd),
        done_marker=done_marker,
        log_path=log_path,
        cwd=cwd,
        env=dict(env or {}),
        timeout_s=timeout_s,
    )

    jobs_dir = ws.jobs_dir_local()
    jobs_dir.mkdir(parents=True, exist_ok=True)
    spec_path = jobs_dir / f"{job_id}.json"
    spec_path.write_text(json.dumps(spec.to_dict(), indent=2), encoding="utf-8")

    commit_sha: Optional[str] = None
    msgs: list[str] = [f"wrote {spec_path}"]

    if auto_commit:
        # stage just this file
        rc, out, err = _run_git(
            ws.repo_local,
            ["add", str(spec_path.relative_to(ws.repo_local))],
            ssh_key=ws.git_ssh_key,
        )
        if rc != 0:
            return SubmitResult(
                ok=False,
                commit_sha=None,
                jobs_file_path=str(spec_path),
                expected_drive_result_path=f"{ws.colab_drive_workspace()}/results/{job_id}.json",
                message=f"git add failed: {err.strip() or out.strip()}",
            )
        rc, out, err = _run_git(
            ws.repo_local,
            ["commit", "-m", commit_message or f"acq: submit job {job_id}"],
            ssh_key=ws.git_ssh_key,
            user_name=ws.git_user_name,
            user_email=ws.git_user_email,
        )
        if rc == 0:
            # capture sha
            rc2, sha_out, _ = _run_git(
                ws.repo_local, ["rev-parse", "HEAD"], ssh_key=ws.git_ssh_key,
            )
            commit_sha = sha_out.strip() if rc2 == 0 else None
            msgs.append(f"committed {commit_sha}")
        else:
            # If nothing-to-commit, still proceed (idempotent re-submit)
            combined = (err + out).lower()
            if "nothing to commit" not in combined:
                return SubmitResult(
                    ok=False,
                    commit_sha=None,
                    jobs_file_path=str(spec_path),
                    expected_drive_result_path=f"{ws.colab_drive_workspace()}/results/{job_id}.json",
                    message=f"git commit failed: {err.strip() or out.strip()}",
                )
            msgs.append("commit skipped (nothing to commit)")

    if auto_push and auto_commit:
        rc, out, err = _run_git(
            ws.repo_local,
            ["push", "origin", "HEAD"],
            ssh_key=ws.git_ssh_key,
        )
        if rc != 0:
            return SubmitResult(
                ok=False,
                commit_sha=commit_sha,
                jobs_file_path=str(spec_path),
                expected_drive_result_path=f"{ws.colab_drive_workspace()}/results/{job_id}.json",
                message=f"git push failed: {err.strip() or out.strip()}",
            )
        msgs.append("pushed")

    return SubmitResult(
        ok=True,
        commit_sha=commit_sha,
        jobs_file_path=str(spec_path),
        expected_drive_result_path=f"{ws.colab_drive_workspace()}/results/{job_id}.json",
        message="; ".join(msgs),
    )


def cancel_job(
    workspace: str,
    job_id: str,
    config: Optional[WorkspaceConfig] = None,
    auto_commit: bool = True,
    auto_push: bool = True,
) -> SubmitResult:
    """Signal a running job to be cancelled by committing a *.cancel sentinel.

    NOTE: the current worker doesn't yet honor cancel sentinels; this is a forward-
    compatible API placeholder. For now, manual cancellation = touch
    `<drive_base>/worker/stop.flag` (which stops the entire worker).
    """
    ws = config or load_workspace(workspace)
    jobs_dir = ws.jobs_dir_local()
    cancel_path = jobs_dir / f"{job_id}.cancel"
    cancel_path.write_text("")

    msgs = [f"wrote {cancel_path}"]
    commit_sha: Optional[str] = None

    if auto_commit:
        _run_git(ws.repo_local, ["add", str(cancel_path.relative_to(ws.repo_local))],
                 ssh_key=ws.git_ssh_key)
        rc, out, err = _run_git(ws.repo_local, ["commit", "-m", f"acq: cancel {job_id}"],
                                ssh_key=ws.git_ssh_key)
        if rc == 0:
            rc2, sha_out, _ = _run_git(ws.repo_local, ["rev-parse", "HEAD"],
                                       ssh_key=ws.git_ssh_key)
            commit_sha = sha_out.strip() if rc2 == 0 else None
            msgs.append(f"committed {commit_sha}")

    if auto_push and auto_commit and commit_sha:
        _run_git(ws.repo_local, ["push", "origin", "HEAD"], ssh_key=ws.git_ssh_key)
        msgs.append("pushed")

    return SubmitResult(
        ok=True,
        commit_sha=commit_sha,
        jobs_file_path=str(cancel_path),
        expected_drive_result_path=f"{ws.colab_drive_workspace()}/results/{job_id}.json",
        message="; ".join(msgs),
    )


def list_jobs(
    workspace: str,
    config: Optional[WorkspaceConfig] = None,
) -> list[dict]:
    """Enumerate job specs in the local repo's jobs_dir. Returns a list of dicts
    with id + path + cmd_preview. Status (running/done) is on Drive — separate call.
    """
    ws = config or load_workspace(workspace)
    jobs_dir = ws.jobs_dir_local()
    if not jobs_dir.exists():
        return []
    out: list[dict] = []
    for p in sorted(jobs_dir.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            out.append({
                "id": data.get("id", p.stem),
                "path": str(p),
                "cmd_preview": " ".join((data.get("cmd") or [])[:3])[:80],
                "created_at": data.get("created_at"),
                "done_marker": data.get("done_marker"),
            })
        except (OSError, json.JSONDecodeError):
            continue
    return out
