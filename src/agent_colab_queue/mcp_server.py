"""FastMCP server exposing agent-colab-queue tools.

Tools exposed to the agent (prefix mcp__acq__ in Claude Code):
    list_workspaces()                          → ["waymo2panorama", ...]
    workspace_info(name)                       → {repo_url, repo_local, drive_workspace_title,
                                                  expected_drive_result_path_template,
                                                  drive_search_hint, jobs_dir_local, ...}
    register_workspace(name, repo_url, repo_local, drive_workspace_title, ...) → {ok, path}
    submit_job(workspace, job_id, cmd, done_marker, timeout_s?, env?,
               log_path?, cwd?, auto_push=True) → {ok, commit_sha, jobs_file_path,
                                                    expected_drive_result_path, message}
    cancel_job(workspace, job_id, auto_push=True) → {ok, message}
    list_jobs(workspace)                       → [{id, path, cmd_preview, created_at}, ...]

This server intentionally does NOT read from Drive — that's delegated to whichever
Drive MCP the agent already has (e.g. the claude.ai Google Drive connector). We
only emit Drive PATHS and search hints so the agent knows what to read.

Patched 2026-05-19 (v0.1.1): every tool body is wrapped in a broad try/except
so that an unexpected exception (subprocess timeout, decode error, etc.) returns
an error dict instead of killing the FastMCP server process.
"""
from __future__ import annotations

import argparse
import functools
import logging
import sys
import tempfile
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from fastmcp import FastMCP

from . import __version__
from .client import cancel_job as _cancel_job
from .client import list_jobs as _list_jobs
from .client import submit_job as _submit_job
from .config import (
    WorkspaceConfig,
    default_workspaces_path,
    load_workspace,
    load_workspaces,
    save_workspace,
)


mcp = FastMCP(name="agent-colab-queue")


def _safe_tool(fn: Callable[..., dict]) -> Callable[..., dict]:
    """Decorator: catch any exception in a tool body and return it as an error dict.
    Without this, an unhandled exception from a subprocess call etc. kills the
    FastMCP server process (observed once during W2P-005 E2E test).
    """
    @functools.wraps(fn)
    def wrapper(*args, **kwargs) -> dict[str, Any]:
        try:
            return fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001
            logging.exception("tool %s raised: %s", fn.__name__, e)
            return {
                "ok": False,
                "error": f"{type(e).__name__}: {e}",
                "traceback": traceback.format_exc(),
                "tool": fn.__name__,
            }
    return wrapper


# ---------- helpers ----------

def _workspace_to_info(ws: WorkspaceConfig) -> dict:
    """Return the workspace's info enriched with Drive search hints for the agent."""
    return {
        "name": ws.name,
        "repo_url": ws.repo_url,
        "repo_local": ws.repo_local,
        "drive_workspace_title": ws.drive_workspace_title,
        "jobs_dir_in_repo": ws.jobs_dir_in_repo,
        "jobs_dir_local": str(ws.jobs_dir_local()),
        "colab_repo_local": ws.colab_repo_local(),
        "colab_drive_workspace": ws.colab_drive_workspace(),
        "expected_drive_result_path_template": (
            f"{ws.colab_drive_workspace()}/results/<job_id>.json"
        ),
        "drive_search_hint": {
            "workspace_folder_title": ws.drive_workspace_title,
            "workspace_folder_query": (
                f"title = '{ws.drive_workspace_title}' and "
                "mimeType = 'application/vnd.google-apps.folder'"
            ),
            "after_finding_workspace": (
                "list its children to find 'results' and 'worker' subfolders, "
                "then list children of 'results' to find <job_id>.json"
            ),
            "result_file_search_query_template": "title = '<job_id>.json'",
        },
        "heartbeat_drive_path_template": (
            f"{ws.colab_drive_workspace()}/worker/heartbeat.json"
        ),
    }


# ---------- tools ----------

@mcp.tool(
    description=(
        "List the names of all configured workspaces. A workspace bundles a GitHub repo, "
        "a local clone path, and a Drive folder title where the Colab worker writes results."
    ),
)
@_safe_tool
def list_workspaces() -> dict:
    ws_map = load_workspaces()
    return {"workspaces": list(ws_map.keys()), "count": len(ws_map)}


@mcp.tool(
    description=(
        "Return full info for one workspace, including Drive search hints the agent "
        "should use with the Drive MCP to read result.json files. This tool is the "
        "agent's directory for navigating the queue."
    ),
)
@_safe_tool
def workspace_info(name: str) -> dict:
    try:
        ws = load_workspace(name)
    except KeyError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, **_workspace_to_info(ws)}


@mcp.tool(
    description=(
        "Register a new workspace (or overwrite an existing one) in the user's "
        "~/.agent-colab-queue/workspaces.yaml. After registering, the Colab worker can "
        "instantiate via Worker.from_workspace(name) and the agent can submit jobs by name."
    ),
)
@_safe_tool
def register_workspace(
    name: str,
    repo_url: str,
    repo_local: str,
    drive_workspace_title: str,
    jobs_dir_in_repo: str = "jobs",
    git_ssh_key: Optional[str] = None,
    git_user_name: Optional[str] = None,
    git_user_email: Optional[str] = None,
) -> dict:
    ws = WorkspaceConfig(
        name=name,
        repo_url=repo_url,
        repo_local=repo_local,
        drive_workspace_title=drive_workspace_title,
        jobs_dir_in_repo=jobs_dir_in_repo,
        git_ssh_key=git_ssh_key,
        git_user_name=git_user_name,
        git_user_email=git_user_email,
    )
    path = save_workspace(ws)
    return {"ok": True, "config_path": str(path), "workspace": _workspace_to_info(ws)}


@mcp.tool(
    description=(
        "Submit a job to a workspace's Colab queue. Writes jobs/<job_id>.json into the "
        "local repo, then (by default) git-commits and pushes so the Colab worker can "
        "git-pull it within ~10 seconds. Returns the commit SHA and the expected Drive "
        "path where the worker will write the result. The agent then reads that path "
        "via its Drive MCP — this tool does NOT poll Drive itself."
    ),
)
@_safe_tool
def submit_job(
    workspace: str,
    job_id: str,
    cmd: list,
    done_marker: str,
    timeout_s: Optional[int] = None,
    env: Optional[dict] = None,
    log_path: Optional[str] = None,
    cwd: Optional[str] = None,
    auto_push: bool = True,
    commit_message: Optional[str] = None,
) -> dict:
    try:
        ws = load_workspace(workspace)
    except KeyError as e:
        return {"ok": False, "error": str(e)}
    result = _submit_job(
        workspace=workspace,
        job_id=job_id,
        cmd=cmd,
        done_marker=done_marker,
        timeout_s=timeout_s,
        env=env,
        log_path=log_path,
        cwd=cwd,
        config=ws,
        auto_commit=True,
        auto_push=auto_push,
        commit_message=commit_message,
    )
    return dict(result)


@mcp.tool(
    description=(
        "Signal a running job to be cancelled (writes a .cancel sentinel; currently a "
        "no-op on the worker — use it as a forward-compatible API marker). For an "
        "immediate stop, touch worker/stop.flag on Drive to halt the whole worker."
    ),
)
@_safe_tool
def cancel_job(workspace: str, job_id: str, auto_push: bool = True) -> dict:
    try:
        ws = load_workspace(workspace)
    except KeyError as e:
        return {"ok": False, "error": str(e)}
    result = _cancel_job(
        workspace=workspace,
        job_id=job_id,
        config=ws,
        auto_commit=True,
        auto_push=auto_push,
    )
    return dict(result)


@mcp.tool(
    description=(
        "List all job specs in the workspace's local repo jobs/ directory. Returns ids "
        "and a preview of each command. NOTE: live state (running/done/crashed) lives on "
        "Drive — read it via your Drive MCP."
    ),
)
@_safe_tool
def list_jobs(workspace: str) -> dict:
    try:
        ws = load_workspace(workspace)
    except KeyError as e:
        return {"ok": False, "error": str(e), "jobs": []}
    return {"ok": True, "jobs": _list_jobs(workspace, config=ws)}


@mcp.tool(
    description=(
        "Return version + config paths. Useful first-call to confirm the MCP server is "
        "alive and to see where workspaces.yaml lives."
    ),
)
@_safe_tool
def server_info() -> dict:
    return {
        "name": "agent-colab-queue",
        "version": __version__,
        "workspaces_config_path": str(default_workspaces_path()),
        "workspaces_config_exists": default_workspaces_path().exists(),
        "tools": [
            "list_workspaces", "workspace_info", "register_workspace",
            "submit_job", "cancel_job", "list_jobs", "server_info",
        ],
    }


# ---------- entry point ----------

def _init_logger(logdir: Optional[str] = None) -> None:
    log_root = Path(logdir) if logdir else Path(tempfile.gettempdir()) / "agent-colab-queue-logs"
    log_root.mkdir(parents=True, exist_ok=True)
    log_path = log_root / datetime.now().strftime("agent-colab-queue.%Y-%m-%d_%H-%M-%S.log")
    logging.basicConfig(
        format="%(asctime)s %(levelname)s:%(message)s",
        datefmt="%H:%M:%S",
        filename=str(log_path),
        level=logging.INFO,
    )
    logging.info("agent-colab-queue v%s logging to %s", __version__, log_path)


def parse_args(argv) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="agent-colab-queue: MCP server for queuing long Colab jobs"
    )
    p.add_argument(
        "-l", "--log",
        default=None,
        help="logfile directory (default %TEMP%/agent-colab-queue-logs/)",
    )
    p.add_argument(
        "--version", action="store_true", help="print version and exit",
    )
    return p.parse_args(argv)


def main() -> None:
    args = parse_args(sys.argv[1:])
    if args.version:
        print(f"agent-colab-queue {__version__}")
        return
    _init_logger(args.log)
    mcp.run()


if __name__ == "__main__":
    main()
