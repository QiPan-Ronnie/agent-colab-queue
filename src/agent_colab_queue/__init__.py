"""agent-colab-queue: Drive-as-queue framework for LLM agents + Colab.

Public API:
    from agent_colab_queue import (
        Worker,           # Colab-side long loop
        submit_job,       # Agent-side: write spec + git push
        cancel_job,       # Agent-side: signal a job to stop
        JobSpec,          # dataclass for a job request
        load_workspace,   # config lookup helper
        load_workspaces,  # config bulk load
    )

See README.md for the architecture and usage examples.
"""

from .job import JobSpec, JobResult
from .config import WorkspaceConfig, load_workspaces, load_workspace, save_workspace
from .worker import Worker
from .client import submit_job, cancel_job, list_jobs

__version__ = "0.1.0"

__all__ = [
    "JobSpec",
    "JobResult",
    "WorkspaceConfig",
    "Worker",
    "submit_job",
    "cancel_job",
    "list_jobs",
    "load_workspaces",
    "load_workspace",
    "save_workspace",
    "__version__",
]
