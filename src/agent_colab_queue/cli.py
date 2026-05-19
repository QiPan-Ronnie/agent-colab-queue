"""Tiny CLI for local debugging — `acq` command.

Not intended for production use; the MCP server is the recommended interface.
"""
from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .client import cancel_job, list_jobs, submit_job
from .config import WorkspaceConfig, load_workspaces, save_workspace


def cmd_list_workspaces(_args) -> int:
    ws_map = load_workspaces()
    for name, ws in ws_map.items():
        print(f"{name}\n  repo: {ws.repo_url}\n  local: {ws.repo_local}\n  drive: {ws.drive_workspace_title}\n")
    return 0


def cmd_register(args) -> int:
    ws = WorkspaceConfig(
        name=args.name,
        repo_url=args.repo_url,
        repo_local=args.repo_local,
        drive_workspace_title=args.drive_workspace_title,
        jobs_dir_in_repo=args.jobs_dir or "jobs",
        git_ssh_key=args.ssh_key,
    )
    path = save_workspace(ws)
    print(f"registered '{args.name}' in {path}")
    return 0


def cmd_submit(args) -> int:
    result = submit_job(
        workspace=args.workspace,
        job_id=args.id,
        cmd=args.cmd,
        done_marker=args.done_marker,
        timeout_s=args.timeout,
        auto_push=not args.no_push,
    )
    print(json.dumps(dict(result), indent=2))
    return 0 if result.get("ok") else 1


def cmd_list(args) -> int:
    jobs = list_jobs(args.workspace)
    for j in jobs:
        print(f"{j['id']}  {j.get('created_at') or '-'}  {j['cmd_preview']}")
    return 0


def cmd_cancel(args) -> int:
    result = cancel_job(args.workspace, args.id, auto_push=not args.no_push)
    print(json.dumps(dict(result), indent=2))
    return 0 if result.get("ok") else 1


def main() -> int:
    p = argparse.ArgumentParser(prog="acq", description=f"agent-colab-queue v{__version__}")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("workspaces").set_defaults(func=cmd_list_workspaces)

    reg = sub.add_parser("register")
    reg.add_argument("name")
    reg.add_argument("--repo-url", required=True)
    reg.add_argument("--repo-local", required=True)
    reg.add_argument("--drive-workspace-title", required=True)
    reg.add_argument("--jobs-dir", default=None)
    reg.add_argument("--ssh-key", default=None)
    reg.set_defaults(func=cmd_register)

    sub_submit = sub.add_parser("submit")
    sub_submit.add_argument("workspace")
    sub_submit.add_argument("--id", required=True)
    sub_submit.add_argument("--done-marker", required=True)
    sub_submit.add_argument("--timeout", type=int, default=None)
    sub_submit.add_argument("--no-push", action="store_true")
    sub_submit.add_argument("cmd", nargs="+")
    sub_submit.set_defaults(func=cmd_submit)

    sub_list = sub.add_parser("list")
    sub_list.add_argument("workspace")
    sub_list.set_defaults(func=cmd_list)

    sub_cancel = sub.add_parser("cancel")
    sub_cancel.add_argument("workspace")
    sub_cancel.add_argument("id")
    sub_cancel.add_argument("--no-push", action="store_true")
    sub_cancel.set_defaults(func=cmd_cancel)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
