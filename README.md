# Claude Code Telegram Bot

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

A Telegram bot that gives you remote access to [Claude Code](https://claude.ai/code). Chat naturally with Claude about your projects from anywhere -- no terminal commands needed.

## What is this?

This bot connects Telegram to Claude Code, providing a conversational AI interface for your codebase:

- **Chat naturally** -- ask Claude to analyze, edit, or explain your code in plain language
- **Maintain context** across conversations with automatic session persistence per project
- **Code on the go** from any device with Telegram
- **Receive proactive notifications** from webhooks, scheduled jobs, and CI/CD events
- **Stay secure** with built-in authentication, directory sandboxing, and audit logging

## Quick Start

### Demo

```
You: Can you help me add error handling to src/api.py?

Bot: I'll analyze src/api.py and add error handling...
     [Claude reads your code, suggests improvements, and can apply changes directly]

You: Looks good. Now run the tests to make sure nothing broke.

Bot: Running pytest...
     All 47 tests passed. The error handling changes are working correctly.
```

### 1. Prerequisites

- **Python 3.11+** -- [Download here](https://www.python.org/downloads/)
- **Claude Code CLI** -- [Install from here](https://claude.ai/code)
- **Telegram Bot Token** -- Get one from [@BotFather](https://t.me/botfather)

### 2. Install

Choose your preferred method:

#### Option A: Install from a release tag (Recommended)

```bash
# Using uv (recommended — installs in an isolated environment)
uv tool install git+https://github.com/overwirehq/claude-code-telegram@v1.3.0

# Or using pip
pip install git+https://github.com/overwirehq/claude-code-telegram@v1.3.0

# Track the latest stable release
pip install git+https://github.com/overwirehq/claude-code-telegram@latest
```

#### Option B: From source (for development)

```bash
git clone https://github.com/overwirehq/claude-code-telegram.git
cd claude-code-telegram
make dev  # requires Poetry
```

> **Note:** Always install from a tagged release (not `main`) for stability. See [Releases](https://github.com/overwirehq/claude-code-telegram/releases) for available versions.

### 3. Configure

```bash
cp .env.example .env
# Edit .env with your settings:
```

**Minimum required:**
```bash
TELEGRAM_BOT_TOKEN=1234567890:ABC-DEF1234ghIkl-zyx57W2v1u123ew11
TELEGRAM_BOT_USERNAME=my_claude_bot
APPROVED_DIRECTORY=/Users/yourname/projects
ALLOWED_USERS=123456789  # Your Telegram user ID
```

### 4. Run

```bash
make run          # Production
make run-debug    # With debug logging
```

Message your bot on Telegram to get started.

> **Detailed setup:** See [docs/setup.md](docs/setup.md) for Claude authentication options and troubleshooting.

## Modes

The bot supports two interaction modes:

### Agentic Mode (Default)

The default conversational mode. Just talk to Claude naturally -- no special commands required.

**Commands:** `/start`, `/new`, `/status`, `/verbose`, `/effort`, `/repo`
If `ENABLE_PROJECT_THREADS=true`: `/sync_threads`

```
You: What files are in this project?
Bot: Working... (3s)
     📖 Read
     📂 LS
     💬 Let me describe the project structure
Bot: [Claude describes the project structure]

You: Add a retry decorator to the HTTP client
Bot: Working... (8s)
     📖 Read: http_client.py
     💬 I'll add a retry decorator with exponential backoff
     ✏️ Edit: http_client.py
     💻 Bash: poetry run pytest tests/ -v
Bot: [Claude shows the changes and test results]

You: /verbose 0
Bot: Verbosity set to 0 (quiet)
```

Use `/verbose 0|1|2` to control how much background activity is shown:

| Level | Shows |
|-------|-------|
| **0** (quiet) | Final response only (typing indicator stays active) |
| **1** (normal, default) | Tool names + reasoning snippets in real-time |
| **2** (detailed) | Tool names with inputs + longer reasoning text |

Use `/effort low|medium|high|xhigh|max` to set Claude's reasoning effort for
the current conversation. `/effort` shows the current value, and
`/effort default` returns to the SDK default.

#### GitHub Workflow

Claude Code already knows how to use `gh` CLI and `git`. Authenticate on your server with `gh auth login`, then work with repos conversationally:

```
You: List my repos related to monitoring
Bot: [Claude runs gh repo list, shows results]

You: Clone the uptime one
Bot: [Claude runs gh repo clone, clones into workspace]

You: /repo
Bot: 📦 uptime-monitor/  ◀
     📁 other-project/

You: Show me the open issues
Bot: [Claude runs gh issue list]

You: Create a fix branch and push it
Bot: [Claude creates branch, commits, pushes]
```

Use `/repo` to list cloned repos in your workspace, or `/repo <name>` to switch directories (sessions auto-resume).

### Classic Mode

Set `AGENTIC_MODE=false` to enable the full terminal-like interface with directory navigation, inline keyboards, quick actions, git integration, and session export.

**Commands:** `/start`, `/help`, `/new`, `/continue`, `/end`, `/status`, `/effort`, `/cd`, `/ls`, `/pwd`, `/projects`, `/export`, `/actions`, `/git`
If `ENABLE_PROJECT_THREADS=true`: `/sync_threads`

```
You: /cd my-web-app
Bot: Directory changed to my-web-app/

You: /ls
Bot: src/  tests/  package.json  README.md

You: /actions
Bot: [Run Tests] [Install Deps] [Format Code] [Run Linter]
```

## Event-Driven Automation

Beyond direct chat, the bot can respond to external triggers:

- **Webhooks** -- Receive GitHub events (push, PR, issues) and route them through Claude for automated summaries or code review
- **Scheduler** -- Run recurring Claude tasks on a cron schedule (e.g., daily code health checks)
- **Notifications** -- Deliver agent responses to configured Telegram chats

Enable with `ENABLE_API_SERVER=true` and `ENABLE_SCHEDULER=true`. See [docs/setup.md](docs/setup.md) for configuration.

## Features

### Working Features

- Conversational agentic mode (default) with natural language interaction
- Classic terminal-like mode with 13 commands and inline keyboards
- Full Claude Code integration with SDK (primary) and CLI (fallback)
- Automatic session persistence per user/project directory
- Multi-layer authentication (whitelist + optional token-based)
- Rate limiting with token bucket algorithm
- Directory sandboxing with path traversal prevention
- File upload handling with archive extraction
- Image/screenshot upload with analysis
- Voice message transcription (Mistral Voxtral / OpenAI Whisper / [local whisper.cpp](docs/local-whisper-cpp.md))
- Git integration with safe repository operations
- Quick actions system with context-aware buttons
- Session export in Markdown, HTML, and JSON formats
- SQLite persistence with migrations
- Usage and cost tracking
- Audit logging and security event tracking
- Event bus for decoupled message routing
- Webhook API server (GitHub HMAC-SHA256, generic Bearer token auth)
- Job scheduler with cron expressions and persistent storage
- Notification service with per-chat rate limiting

- Tunable verbose output showing Claude's tool usage and reasoning in real-time
- Persistent typing indicator so users always know the bot is working
- 16 configurable tools with allowlist/disallowlist control (see [docs/tools.md](docs/tools.md))

### Planned Enhancements

- Plugin system for third-party extensions

## Configuration

### Required

```bash
TELEGRAM_BOT_TOKEN=...           # From @BotFather
TELEGRAM_BOT_USERNAME=...        # Your bot's username
APPROVED_DIRECTORY=...           # Base directory for project access
ALLOWED_USERS=123456789          # Comma-separated Telegram user IDs
```

### Common Options

```bash
# Claude
ANTHROPIC_API_KEY=sk-ant-...     # API key (optional if using CLI auth)
CLAUDE_MAX_COST_PER_USER=10.0    # Spending limit per user (USD)
CLAUDE_TIMEOUT_SECONDS=300       # Operation timeout

# Mode
AGENTIC_MODE=true                # Agentic (default) or classic mode
VERBOSE_LEVEL=1                  # 0=quiet, 1=normal (default), 2=detailed

# Rate Limiting
RATE_LIMIT_REQUESTS=10           # Requests per window
RATE_LIMIT_WINDOW=60             # Window in seconds

# Features (classic mode)
ENABLE_GIT_INTEGRATION=true
ENABLE_FILE_UPLOADS=true
ENABLE_QUICK_ACTIONS=true
```

### Agentic Platform

```bash
# Webhook API Server
ENABLE_API_SERVER=false          # Enable FastAPI webhook server
API_SERVER_PORT=8080             # Server port

# Webhook Authentication
GITHUB_WEBHOOK_SECRET=...        # GitHub HMAC-SHA256 secret
WEBHOOK_API_SECRET=...           # Bearer token for generic providers

# Scheduler
ENABLE_SCHEDULER=false           # Enable cron job scheduler

# Notifications
NOTIFICATION_CHAT_IDS=123,456    # Default chat IDs for proactive notifications
```

### Project Threads Mode

```bash
# Enable strict topic routing by project
ENABLE_PROJECT_THREADS=true

# Mode: private (default) or group
PROJECT_THREADS_MODE=private

# YAML registry file (see config/projects.example.yaml)
PROJECTS_CONFIG_PATH=config/projects.yaml

# Required only when PROJECT_THREADS_MODE=group
PROJECT_THREADS_CHAT_ID=-1001234567890

# Minimum delay (seconds) between Telegram API calls during topic sync
# Set 0 to disable pacing
PROJECT_THREADS_SYNC_ACTION_INTERVAL_SECONDS=1.1
```

In strict mode, only `/start` and `/sync_threads` work outside mapped project topics.
In private mode, `/start` auto-syncs project topics for your private bot chat.
To use topics with your bot, enable them in BotFather:
`Bot Settings -> Threaded mode`.

> **Full reference:** See [docs/configuration.md](docs/configuration.md) and [`.env.example`](.env.example).

### Finding Your Telegram User ID

Message [@userinfobot](https://t.me/userinfobot) on Telegram -- it will reply with your user ID number.

## Troubleshooting

**Bot doesn't respond:**
- Check your `TELEGRAM_BOT_TOKEN` is correct
- Verify your user ID is in `ALLOWED_USERS`
- Ensure Claude Code CLI is installed and accessible
- Check bot logs with `make run-debug`

**Claude integration not working:**
- SDK mode (default): Check `claude auth status` or verify `ANTHROPIC_API_KEY`
- CLI mode: Verify `claude --version` and `claude auth status`
- Check `CLAUDE_ALLOWED_TOOLS` includes necessary tools (see [docs/tools.md](docs/tools.md) for the full reference)

**High usage costs:**
- Adjust `CLAUDE_MAX_COST_PER_USER` to set spending limits
- Monitor usage with `/status`
- Use shorter, more focused requests

## Security

This bot implements defense-in-depth security:

- **Access Control** -- Whitelist-based user authentication
- **Directory Isolation** -- Sandboxing to approved directories
- **Rate Limiting** -- Request and cost-based limits
- **Input Validation** -- Injection and path traversal protection
- **Webhook Authentication** -- GitHub HMAC-SHA256 and Bearer token verification
- **Audit Logging** -- Complete tracking of all user actions

See [SECURITY.md](SECURITY.md) for details.

## Development

```bash
make dev           # Install all dependencies
make test          # Run tests with coverage
make lint          # Black + isort + flake8 + mypy
make format        # Auto-format code
make run-debug     # Run with debug logging
make run-watch     # Run with auto-restart on code changes
```

> **Full documentation:** See the [docs index](docs/README.md) for all guides and references.

### Version Management

The version is defined once in `pyproject.toml` and read at runtime via `importlib.metadata`. To cut a release:

```bash
make bump-patch    # 1.2.0 -> 1.2.1 (bug fixes)
make bump-minor    # 1.2.0 -> 1.3.0 (new features)
make bump-major    # 1.2.0 -> 2.0.0 (breaking changes)
```

Each command commits, tags, and pushes automatically, triggering CI tests and a GitHub Release with auto-generated notes.

### Contributing

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/amazing-feature`
3. Make changes with tests: `make test && make lint`
4. Submit a Pull Request (one feature or fix per PR, with tests)

**Code standards:** Python 3.11+, Black formatting (88 chars), type hints required, pytest.

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full process, [MAINTAINERS.md](MAINTAINERS.md) for who reviews and how to become a maintainer, [docs/ROADMAP-v2.md](docs/ROADMAP-v2.md) for what is planned, and [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) for community expectations.

## License

MIT License -- see [LICENSE](LICENSE).

## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=overwirehq/claude-code-telegram&type=Date)](https://star-history.com/#overwirehq/claude-code-telegram&Date)

## Acknowledgments

- [Claude](https://claude.ai) by Anthropic
- [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot)
