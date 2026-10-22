# Available Tools

This document describes the tools that Claude Code can use when interacting through the Telegram bot. Tools are the operations Claude performs behind the scenes to read, write, search, and execute code on your behalf.

## Overview

By default, the bot allows **20 tools**, configured via the `CLAUDE_ALLOWED_TOOLS` environment variable. The list is enforced: a tool that is not on it is denied before it runs. The default includes names used by older Claude CLI versions (`Glob`, `Grep`, `LS`, `BashOutput`, `KillShell`, ...) so that it works across versions; a listed tool the CLI does not have is simply never called. Tool calls that touch the filesystem are validated before they run by the `can_use_tool` callback in [`src/claude/sdk_integration.py`](../src/claude/sdk_integration.py), which checks file paths and Bash commands against the `APPROVED_DIRECTORY`.

When Claude uses a tool during a conversation, the tool name appears in real-time if verbose output is enabled (`/verbose 1` or `/verbose 2`). A tool call that fails a boundary check is denied before it executes, and Claude is told why.

## Tool Reference

### File Operations

| Tool | Icon | Description |
|------|------|-------------|
| **Read** | 📖 | Read file contents from disk. Supports text files, images, PDFs, and Jupyter notebooks. |
| **Write** | ✏️ | Create a new file or overwrite an existing file with new contents. |
| **Edit** | ✏️ | Perform targeted string replacements within an existing file without rewriting the entire file. |
| **MultiEdit** | ✏️ | Apply multiple edits to a single file in one operation. Useful for making several changes at once. |

### Search & Navigation

| Tool | Icon | Description |
|------|------|-------------|
| **Glob** | 🔍 | Find files by name pattern (e.g., `**/*.py`, `src/**/*.ts`). Returns matching file paths sorted by modification time. |
| **Grep** | 🔍 | Search file contents using regular expressions. Supports filtering by file type or glob pattern, context lines, and multiple output modes. |
| **LS** | 📂 | List directory contents. |

### Execution

| Tool | Icon | Description |
|------|------|-------------|
| **Bash** | 💻 | Execute shell commands (e.g., `git`, `npm`, `pytest`, `make`). Subject to directory boundary enforcement and, in classic mode, dangerous-pattern blocking. |

### Notebooks

| Tool | Icon | Description |
|------|------|-------------|
| **NotebookRead** | 📓 | Read a Jupyter notebook (`.ipynb`) and return all cells with their outputs. |
| **NotebookEdit** | 📓 | Replace, insert, or delete cells in a Jupyter notebook. |

### Web

| Tool | Icon | Description |
|------|------|-------------|
| **WebFetch** | 🌐 | Fetch a URL and process its content. HTML is converted to markdown before analysis. |
| **WebSearch** | 🌐 | Search the web and return results. Useful for looking up documentation, current events, or information beyond Claude's training data. |

### Task Management

| Tool | Icon | Description |
|------|------|-------------|
| **TodoRead** | ☑️ | Read the current task list that Claude uses to track multi-step work. |
| **TodoWrite** | ☑️ | Create or update a task list to plan and track progress on complex operations. |

### Agent Orchestration

| Tool | Icon | Description |
|------|------|-------------|
| **Task** | 🧠 | Launch a sub-agent to handle complex, multi-step operations autonomously. The sub-agent runs with its own context and returns a result when finished. Newer CLIs call this tool **Agent**; either name in the list allows both. Sub-agent tool calls are checked against the same list. |
| **TaskOutput** | 🧠 | Read the output of a background sub-agent launched by **Task**. Required for retrieving results from agents that were run in the background. |
| **TaskStop** | 🧠 | Stop a running background task or shell. |
| **BashOutput** | 💻 | Read the output of a background shell (older CLI versions). |
| **KillShell** | 💻 | Stop a background shell (older CLI versions; newer ones use **TaskStop**). |

### Extensions

| Tool | Icon | Description |
|------|------|-------------|
| **Skill** | 🔧 | Run a skill defined for the project. |

## Verbose Output

When verbose output is enabled, each tool call is shown with its icon as Claude works:

```
You: Add type hints to utils.py

Bot: Working... (5s)
     📖 Read: utils.py
     💬 I'll add type annotations to all functions
     ✏️ Edit: utils.py
     💻 Bash: poetry run mypy src/utils.py
Bot: [Claude shows the changes and type-check results]
```

Control verbosity with `/verbose`:

| Level | Behavior |
|-------|----------|
| `/verbose 0` | Final response only (typing indicator stays active) |
| `/verbose 1` | Tool names + reasoning snippets (default) |
| `/verbose 2` | Tool names with input details + longer reasoning text |

## Configuration

### Allowing / Disallowing Tools

The default allowed tools list is defined in `src/config/settings.py` and can be overridden with environment variables:

```bash
# Allow only specific tools (comma-separated)
CLAUDE_ALLOWED_TOOLS=Read,Write,Edit,Bash,Glob,Grep,LS,Task,TaskOutput,TaskStop,BashOutput,KillShell,MultiEdit,NotebookRead,NotebookEdit,WebFetch,TodoRead,TodoWrite,WebSearch,Skill

# Explicitly block specific tools (comma-separated, takes precedence over allowed)
CLAUDE_DISALLOWED_TOOLS=Bash,Write
```

A tool that is not in `CLAUDE_ALLOWED_TOOLS` is denied, and the reply tells you
which calls were blocked. Removing a tool from the list disables it -- the
filesystem tools included, although they are withheld from the CLI's
pre-approval so their boundary checks run (see
[Security](../SECURITY.md#tool-call-enforcement)).

Things to know when trimming or extending the list:

- **MCP tools** (`mcp__<server>__<tool>`): with `ENABLE_MCP=true`, every tool of a
  server in `MCP_CONFIG_PATH` is allowed. MCP servers from anywhere else (such as
  a project's `.mcp.json`) are denied unless listed, by tool name or as
  `mcp__<server>` for the whole server.
- **Tools left out on purpose:** `AskUserQuestion`, `EnterPlanMode` and
  `ExitPlanMode` need an interactive Claude Code UI that the bot does not have,
  and newer CLI tools that schedule work, run commands in the background or spawn
  agents (`Monitor`, `CronCreate`, `Workflow`, `SendMessage`, ...) are not in the
  default list. Add one to the list to opt in.
- **New CLI tools** are denied until listed. If a CLI upgrade introduces a tool
  you want, add it to the list.
- An empty `CLAUDE_ALLOWED_TOOLS` means no allowlist, not "allow nothing".

`CLAUDE_DISALLOWED_TOOLS` blocks a tool even if it is in the allowed list.

To turn off tool validation entirely:

```bash
# WARNING: also disables the path and Bash boundary checks below.
# Trusted environments only.
DISABLE_TOOL_VALIDATION=true
```

### Security Layers

Even when a tool is allowed, additional security checks apply. The exact checks depend on the run mode:

1. **File path validation** (all modes) — `Read`, `Write`, `Edit`, `MultiEdit`, `NotebookEdit` and `NotebookRead` operations must target paths within the `APPROVED_DIRECTORY`. Path traversal attempts are blocked before the tool runs.

2. **Bash command validation** (classic mode only) — Dangerous patterns (`rm -rf`, `sudo`, `chmod 777`, pipes, redirections, subshells) are blocked by default. Filesystem-modifying commands (`mkdir`, `cp`, `mv`, `rm`, etc.) must target paths within the approved directory. This layer is **not active in agentic mode**, which relies on OS-level sandboxing instead.

3. **Bash directory boundary checks** (all modes) — Filesystem-modifying commands are checked before they run to ensure their target paths stay within the approved directory, regardless of run mode.

4. **Audit logging** (all modes) — All tool calls and security violations are recorded for review.

Layers 1 and 3, and enforcement of `CLAUDE_ALLOWED_TOOLS`, require
`DISABLE_TOOL_VALIDATION=false` (the default); setting it to `true` disables them
along with the allow/deny lists.

See [Security](../SECURITY.md) for the full security model.
