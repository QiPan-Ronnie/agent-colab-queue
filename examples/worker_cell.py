"""Template Colab cell for running the agent-colab-queue worker.

USAGE:
    1. Make sure ~/.agent-colab-queue/workspaces.yaml has your workspace registered
       (the agent can do this via mcp__acq__register_workspace).
    2. In Colab, mount Drive (any way you like).
    3. git-clone the workspace's repo to /content/<repo_name>.
    4. Paste WORKER_CELL_CODE into one cell, replace WORKSPACE_NAME, run.
    5. The cell loops forever (or until worker/stop.flag is touched).

The worker is project-agnostic — point WORKSPACE_NAME at any registered workspace.
"""

WORKER_CELL_CODE = r'''
# Cell — agent-colab-queue worker (W2P-005 / acq v0.1)
# Paste once per Colab session. Loops forever; does NOT use colab-mcp.

import sys, os, subprocess

# ====== EDIT THIS LINE ======
WORKSPACE_NAME = "waymo2panorama"
# ============================

# Install acq if missing (one-time per Colab kernel)
try:
    import agent_colab_queue  # noqa: F401
except ImportError:
    print("Installing agent-colab-queue...")
    subprocess.check_call([
        sys.executable, "-m", "pip", "install", "-q",
        "git+https://github.com/QiPan-Ronnie/agent-colab-queue.git",
    ])
    import agent_colab_queue  # noqa: F401

from agent_colab_queue import Worker, load_workspace
ws = load_workspace(WORKSPACE_NAME)

# Ensure the workspace's repo is cloned locally on Colab
repo_local = ws.colab_repo_local()  # e.g. /content/Waymo2Panorama
if not os.path.exists(f"{repo_local}/.git"):
    print(f"Cloning {ws.repo_url} into {repo_local}...")
    subprocess.check_call(["git", "clone", ws.repo_url, repo_local])
else:
    subprocess.run(["git", "-C", repo_local, "pull"], capture_output=True)

# Ensure Drive workspace exists
drive_ws = ws.colab_drive_workspace()  # e.g. /content/drive/MyDrive/koi_waymo2pano_colab
os.makedirs(drive_ws, exist_ok=True)

worker = Worker.from_workspace(WORKSPACE_NAME)
worker.run(verbose=True)
'''


if __name__ == "__main__":
    print(WORKER_CELL_CODE)
