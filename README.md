# agent-colab-queue

A reusable Drive-as-queue framework for connecting LLM agents (Claude Code, ChatGPT Operator, etc.) to long-running experiments on Google Colab — **without depending on fragile MCP stdio links during the experiment**.

Born out of the [Waymo2Panorama project](https://github.com/QiPan-Ronnie/Waymo2Panorama) where local MCP stdio kept dropping during multi-minute Colab cell runs. See `docs/architecture.md` for full design rationale.

## What it does

```
Agent (Claude Code / etc)                              Colab (any notebook)
       │                                                       │
       │  1. submit_job(workspace, cmd, done_marker)           │
       │     -> writes jobs/<id>.json + git push               │
       │                                                       │
       │                                git pull (every 10s)   │
       │  2. job appears in repo  ────────────────────────────▶│
       │                                                       │
       │                                claim + subprocess     │
       │                                update results/<id>.json
       │                                on Drive every ~3s     │
       │                                                       │
       │  3. read results/<id>.json via Drive MCP              │
       │     -> get state/log/exit_code                        │
       │                                                       │
       └───── 0 colab-mcp stdio in the data path ─────────────┘
```

**Reliability story**: agent uses `git push` (battle-tested) for outbound, Drive MCP (Anthropic-hosted connector) for inbound. Colab worker uses native Drive mount writes and `git pull`. Local stdio MCP is **optional convenience**, not part of the critical path. If your `colab-mcp` dies, this still works.

## Two ways to use it

### 1. As a Python library

```python
# Agent side — submit a job
from agent_colab_queue import submit_job

result = submit_job(
    workspace="waymo2panorama",
    job_id="phase2-pi3-frame0",
    cmd=["python", "scripts/phase2/run_pi3_one_frame.py", "--anchor-idx", "0"],
    done_marker="/content/drive/MyDrive/koi_waymo2pano_colab/outputs/phase2/pi3_one_frame/summary.json",
    timeout_s=1800,
)
print(result["commit_sha"], result["expected_drive_result_path"])

# Then read result via Drive MCP (separate concern)
```

```python
# Colab worker — paste this into one cell, run once per session
from agent_colab_queue import Worker
Worker.from_workspace("waymo2panorama").run()
```

### 2. As an MCP server

After adding to `~/.claude.json`:

```json
"mcpServers": {
  "agent-colab-queue": {
    "command": "uvx",
    "args": ["--from", "/path/to/agent-colab-queue", "agent-colab-queue"]
  }
}
```

The agent gets these MCP tools:

| Tool | Purpose |
|---|---|
| `mcp__acq__list_workspaces()` | enumerate configured workspaces |
| `mcp__acq__workspace_info(name)` | get repo/Drive paths + Drive search hints |
| `mcp__acq__register_workspace(...)` | add a new workspace to config |
| `mcp__acq__submit_job(workspace, job_id, cmd, done_marker, ...)` | write spec + git commit + git push |
| `mcp__acq__cancel_job(workspace, job_id)` | mark a running job for cancellation |
| `mcp__acq__list_jobs(workspace, state?)` | enumerate jobs by reading local repo |

For **reading results** the agent should use the existing Drive MCP (`mcp__claude_ai_Google_Drive__search_files` + `download_file_content`) — we don't duplicate it.

## Why not auth Drive ourselves?

Deliberate design choice. Anthropic's Drive MCP connector handles OAuth, token refresh, rate limits, and uptime. Duplicating that creates another stdio MCP that can die. Composing with their connector keeps our scope minimal.

## Workspace config

`~/.agent-colab-queue/workspaces.yaml`:

```yaml
workspaces:
  waymo2panorama:
    repo_url: git@github.com:QiPan-Ronnie/Waymo2Panorama.git
    repo_local: D:/BaiduSyncdisk/2024 to future/koi chen/experiments/Waymo2Panorama
    drive_workspace_title: koi_waymo2pano_colab     # title of MyDrive folder
    jobs_dir_in_repo: jobs/
    git_ssh_key: C:/Users/me/.ssh/id_ed25519_github_new   # optional, for SSH push
```

You can also register workspaces via `mcp__acq__register_workspace` from the agent — no manual yaml editing required.

## Status

- v0.1.0: extracted from Waymo2Panorama's `code/waymo2panorama/utils/drive_queue.py`, MCP server added
- Validated end-to-end against Waymo2Panorama (sleep-180 stability test, 187 s, exit 0, agent never used colab-mcp)

## License

MIT
