"""Workspace configuration.

A workspace bundles together:
    - a GitHub repo (the inbound queue lives in repo/jobs/<id>.json)
    - a local path to that repo (where the agent writes specs)
    - a Drive folder title (where the Colab worker writes results)

Config file location (resolved in this order):
    1. $ACQ_WORKSPACES_FILE (full path)
    2. <repo_local>/.acq-workspaces.yaml  (per-project override)
    3. ~/.agent-colab-queue/workspaces.yaml (user-level)

The user-level file is the default install location.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

import yaml


@dataclass
class WorkspaceConfig:
    name: str
    repo_url: str                      # e.g. git@github.com:QiPan-Ronnie/Waymo2Panorama.git
    repo_local: str                     # local path to the cloned repo
    drive_workspace_title: str          # title of the MyDrive folder, e.g. koi_waymo2pano_colab
    jobs_dir_in_repo: str = "jobs"      # subdir within the repo where job specs live
    git_ssh_key: Optional[str] = None   # path to SSH key for git push (None = default agent)
    git_user_name: Optional[str] = None
    git_user_email: Optional[str] = None
    # Colab-side mount path conventions (worker uses these). Standard for Colab.
    colab_repo_path: str = "/content"   # where to clone the repo on Colab (worker will pick subdir)
    colab_drive_root: str = "/content/drive/MyDrive"

    def colab_repo_local(self) -> str:
        """Where the worker should clone this repo on Colab."""
        # /content/<repo_name>
        return f"{self.colab_repo_path}/{Path(self.repo_local).name}"

    def colab_drive_workspace(self) -> str:
        """/content/drive/MyDrive/<title>"""
        return f"{self.colab_drive_root}/{self.drive_workspace_title}"

    def jobs_dir_local(self) -> Path:
        return Path(self.repo_local) / self.jobs_dir_in_repo

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("name")  # name is the dict key, not in the body
        return d


def default_workspaces_path() -> Path:
    if path := os.environ.get("ACQ_WORKSPACES_FILE"):
        return Path(path)
    home = Path(os.path.expanduser("~"))
    return home / ".agent-colab-queue" / "workspaces.yaml"


def project_workspaces_path(repo_local: str) -> Path:
    return Path(repo_local) / ".acq-workspaces.yaml"


def load_workspaces(path: Optional[Path] = None) -> dict[str, WorkspaceConfig]:
    """Read the workspaces yaml. Returns {} if file doesn't exist."""
    if path is None:
        path = default_workspaces_path()
    if not path.exists():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    workspaces_raw = raw.get("workspaces", {}) or {}
    out: dict[str, WorkspaceConfig] = {}
    for name, body in workspaces_raw.items():
        if not isinstance(body, dict):
            continue
        out[name] = WorkspaceConfig(name=name, **{k: v for k, v in body.items() if k != "name"})
    return out


def load_workspace(name: str, path: Optional[Path] = None) -> WorkspaceConfig:
    """Look up one workspace by name. Raises KeyError if not found."""
    workspaces = load_workspaces(path)
    if name not in workspaces:
        raise KeyError(
            f"Workspace '{name}' not found. Available: {list(workspaces)}. "
            f"Config at {path or default_workspaces_path()}."
        )
    return workspaces[name]


def save_workspace(workspace: WorkspaceConfig, path: Optional[Path] = None) -> Path:
    """Add or update a workspace in the config file. Creates the file if missing."""
    if path is None:
        path = default_workspaces_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists():
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    else:
        raw = {}
    raw.setdefault("workspaces", {})
    raw["workspaces"][workspace.name] = workspace.to_dict()

    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return path
