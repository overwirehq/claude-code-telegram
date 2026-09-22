# Configuration Guide

This document provides comprehensive information about configuring the Claude Code Telegram Bot.

## Overview

The bot uses a configuration system built with Pydantic Settings v2 that provides:

- **Type Safety**: All configuration values are validated and type-checked
- **Environment Support**: Automatic environment-specific overrides
- **Feature Flags**: Dynamic enabling/disabling of functionality
- **Validation**: Cross-field validation and runtime checks

## Configuration Sources

Configuration is loaded in this order (later sources override earlier ones):

1. **Default values** defined in the Settings class
2. **Environment variables**
3. **`.env` file** (if present)
4. **Environment-specific overrides** (development/testing/production)

## Environment Variables

### Required Settings

```bash
# Telegram Bot Configuration
TELEGRAM_BOT_TOKEN=1234567890:ABC-DEF1234ghIkl-zyx57W2v1u123ew11
TELEGRAM_BOT_USERNAME=your_bot_name

# Security
APPROVED_DIRECTORY=/path/to/your/projects
```

### Optional Settings

#### User Access Control

```bash
# Comma-separated list of allowed Telegram user IDs
ALLOWED_USERS=123456789,987654321

# Enable token-based authentication (requires AUTH_TOKEN_SECRET)
ENABLE_TOKEN_AUTH=false
AUTH_TOKEN_SECRET=your-secret-key-here
```

#### Security Relaxation (Trusted Environments Only)

```bash
# Disable dangerous pattern validation in SecurityValidator (default: false)
# WARNING: This allows characters such as pipes and redirections in validated paths.
DISABLE_SECURITY_PATTERNS=false

# Disable Claude tool validation (default: false)
# WARNING: This skips the tool allow/disallow lists AND the pre-execution path and
# Bash directory boundary checks on Claude's own tool calls. Trusted environments only.
DISABLE_TOOL_VALIDATION=false
```

#### Interactive Tool Approval

```bash
# Require interactive Telegram Allow/Deny approval before executing risky tool calls (default: false)
INTERACTIVE_TOOL_APPROVAL=false

# Comma-separated tool names gated behind approval when enabled (default: Bash,Write,Edit)
INTERACTIVE_TOOL_APPROVAL_TOOLS=Bash,Write,Edit

# Seconds to wait for a response before applying the timeout action (default: 60)
INTERACTIVE_TOOL_APPROVAL_TIMEOUT_SECONDS=60

# Decision applied automatically on timeout: deny (fail-closed) or allow (default: deny)
INTERACTIVE_TOOL_APPROVAL_TIMEOUT_ACTION=deny
```

#### Claude Configuration

```bash
# Authentication
ANTHROPIC_API_KEY=sk-ant-api03-...    # Optional: API key for SDK (uses CLI auth if omitted)

# Maximum conversation turns before requiring new session
CLAUDE_MAX_TURNS=10

# Timeout for Claude operations in seconds
CLAUDE_TIMEOUT_SECONDS=300

# Maximum cost per user in USD (lifetime budget for rate limiter)
CLAUDE_MAX_COST_PER_USER=10.0

# Maximum cost per individual request in USD (SDK-level hard cap)
CLAUDE_MAX_COST_PER_REQUEST=5.0

# Allowed Claude tools (comma-separated list; see docs/tools.md for descriptions)
CLAUDE_ALLOWED_TOOLS=Read,Write,Edit,Bash,Glob,Grep,LS,Task,TaskOutput,MultiEdit,NotebookRead,NotebookEdit,WebFetch,TodoRead,TodoWrite,WebSearch
```

#### Rate Limiting

```bash
# Number of requests allowed per window
RATE_LIMIT_REQUESTS=10

# Rate limit window in seconds
RATE_LIMIT_WINDOW=60

# Burst capacity for rate limiting
RATE_LIMIT_BURST=20
```

#### Storage & Database

```bash
# Database URL (SQLite by default)
DATABASE_URL=sqlite:///data/bot.db

# Session management
SESSION_TIMEOUT_HOURS=24           # Session timeout in hours
MAX_SESSIONS_PER_USER=5            # Max concurrent sessions per user

# Data retention
DATA_RETENTION_DAYS=90            # Days to keep old data
AUDIT_LOG_RETENTION_DAYS=365     # Days to keep audit logs
```

#### Mode Selection

```bash
# Agentic mode (default: true)
# true = conversational mode with a compact command set
# false = classic terminal mode with navigation and inline keyboards
AGENTIC_MODE=true
```

#### Feature Flags

```bash
# Enable Model Context Protocol
ENABLE_MCP=false
MCP_CONFIG_PATH=/path/to/mcp/config.json

# Enable Git integration (classic mode)
ENABLE_GIT_INTEGRATION=true

# Enable file upload handling
ENABLE_FILE_UPLOADS=true

# Enable quick action buttons (classic mode)
ENABLE_QUICK_ACTIONS=true

# Enable voice message transcription
ENABLE_VOICE_MESSAGES=true
VOICE_PROVIDER=mistral              # 'mistral', 'openai', or 'local'
MISTRAL_API_KEY=                     # Required when VOICE_PROVIDER=mistral
OPENAI_API_KEY=                      # Required when VOICE_PROVIDER=openai
VOICE_TRANSCRIPTION_MODEL=           # Default: voxtral-mini-latest (Mistral) or whisper-1 (OpenAI)
VOICE_MAX_FILE_SIZE_MB=20            # Max Telegram voice file size to download (1-200MB)

# Local whisper.cpp settings (only used when VOICE_PROVIDER=local)
WHISPER_CPP_BINARY_PATH=             # Path to whisper.cpp binary (auto-detected from PATH if unset)
WHISPER_CPP_MODEL_PATH=base          # Path to GGML model file or model name (base, small, medium, large)
```

#### Agentic Platform

```bash
# Webhook API Server
ENABLE_API_SERVER=false               # Enable FastAPI webhook server
API_SERVER_PORT=8080                  # Server port (default: 8080)

# Webhook Authentication
GITHUB_WEBHOOK_SECRET=your-secret    # GitHub HMAC-SHA256 secret
WEBHOOK_API_SECRET=your-secret       # Bearer token for generic providers

# Job Scheduler
ENABLE_SCHEDULER=false                # Enable cron job scheduler

# Notifications
NOTIFICATION_CHAT_IDS=123456,789012  # Default Telegram chat IDs for proactive notifications
```

#### Project Thread Mode

```bash
# Strict project routing via Telegram project topics
ENABLE_PROJECT_THREADS=false

# Mode: private (default) or group
PROJECT_THREADS_MODE=private

# YAML registry file with project slugs/names/paths
PROJECTS_CONFIG_PATH=config/projects.yaml

# Required only for PROJECT_THREADS_MODE=group
PROJECT_THREADS_CHAT_ID=-1001234567890

# Minimum delay (seconds) between Telegram API calls during topic sync
# Set 0 to disable pacing
PROJECT_THREADS_SYNC_ACTION_INTERVAL_SECONDS=1.1
```

`PROJECTS_CONFIG_PATH` schema:

```yaml
projects:
  - slug: my-app
    name: My App
    path: my-app
    enabled: true
```

When `ENABLE_PROJECT_THREADS=true`:
- `PROJECT_THREADS_MODE=private`:
  - `/start` and `/sync_threads` are allowed outside topics in private chat.
  - all other updates must be inside mapped project topics.
- `PROJECT_THREADS_MODE=group`:
  - behavior remains forum-topic based using `PROJECT_THREADS_CHAT_ID`.

#### Monitoring & Logging

```bash
# Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
LOG_LEVEL=INFO

# Enable anonymous telemetry
ENABLE_TELEMETRY=false

# Sentry DSN for error tracking
SENTRY_DSN=https://your-sentry-dsn@sentry.io/project
```

#### Development

```bash
# Enable debug mode
DEBUG=false

# Enable development features
DEVELOPMENT_MODE=false

# Environment override (development, testing, production)
ENVIRONMENT=development
```

#### Webhook (Telegram Polling vs Webhook)

```bash
# Webhook URL for bot (leave empty for polling mode)
WEBHOOK_URL=https://your-domain.com/webhook

# Webhook port
WEBHOOK_PORT=8443

# Webhook path
WEBHOOK_PATH=/webhook
```

## Environment-Specific Configuration

The bot automatically applies different settings based on the environment:

### Development Environment

Activated when `ENVIRONMENT=development` or when `DEBUG=true`:

- `debug = true`
- `development_mode = true`
- `log_level = "DEBUG"`
- `rate_limit_requests = 100` (more lenient)
- `claude_timeout_seconds = 600` (longer timeout)
- `enable_telemetry = false`

### Testing Environment

Activated when `ENVIRONMENT=testing`:

- `debug = true`
- `database_url = "sqlite:///:memory:"` (in-memory database)
- `approved_directory = "/tmp/test_projects"`
- `claude_timeout_seconds = 30` (faster timeout)
- `rate_limit_requests = 1000` (no effective rate limiting)

### Production Environment

Activated when `ENVIRONMENT=production`:

- `debug = false`
- `log_level = "INFO"`
- `enable_telemetry = true`
- `claude_max_cost_per_user = 5.0` (stricter cost limit)
- `claude_max_cost_per_request = 2.0` (per-request SDK cap)
- `rate_limit_requests = 5` (stricter rate limiting)
- `session_timeout_hours = 12` (shorter session timeout)

## Feature Flags

Feature flags allow you to enable or disable functionality dynamically:

```python
from src.config import load_config, FeatureFlags

config = load_config()
features = FeatureFlags(config)

if features.agentic_mode_enabled:
    # Use agentic mode handlers
    pass

if features.api_server_enabled:
    # Start webhook API server
    pass
```

Available feature flags:

- `agentic_mode_enabled`: Agentic conversational mode (default: true)
- `api_server_enabled`: Webhook API server
- `scheduler_enabled`: Cron job scheduler
- `mcp_enabled`: Model Context Protocol support
- `git_enabled`: Git integration commands
- `file_uploads_enabled`: File upload handling
- `quick_actions_enabled`: Quick action buttons
- `telemetry_enabled`: Anonymous usage telemetry
- `token_auth_enabled`: Token-based authentication
- `webhook_enabled`: Telegram webhook mode (vs polling)
- `voice_messages_enabled`: Voice message transcription (default: true)
- `development_features_enabled`: Development-only features

## Validation

The configuration system performs extensive validation:

### Path Validation

- `APPROVED_DIRECTORY` must exist and be accessible
- `MCP_CONFIG_PATH` must exist if MCP is enabled

### Cross-Field Validation

- `AUTH_TOKEN_SECRET` is required when `ENABLE_TOKEN_AUTH=true`
- `MCP_CONFIG_PATH` is required when `ENABLE_MCP=true`
- `MISTRAL_API_KEY` is required when `VOICE_PROVIDER=mistral`
- `OPENAI_API_KEY` is required when `VOICE_PROVIDER=openai`

### Value Validation

- `LOG_LEVEL` must be one of: DEBUG, INFO, WARNING, ERROR, CRITICAL
- Numeric values must be positive where appropriate
- User IDs in `ALLOWED_USERS` must be valid integers

## Claude Integration Options

### Authentication Options

#### Option 1: Use Existing Claude CLI Authentication (Recommended)
```bash
# No ANTHROPIC_API_KEY needed - SDK will use CLI credentials
# Ensure Claude CLI is installed and authenticated: claude auth login
```

#### Option 2: Direct API Key
```bash
ANTHROPIC_API_KEY=sk-ant-api03-your-key-here
```

## Troubleshooting

### Common Issues

1. **"Approved directory does not exist"**
   - Ensure the path in `APPROVED_DIRECTORY` exists
   - Use absolute paths, not relative paths
   - Check file permissions

2. **"auth_token_secret required"**
   - Set `AUTH_TOKEN_SECRET` when using `ENABLE_TOKEN_AUTH=true`
   - Generate a secure secret: `openssl rand -hex 32`

3. **"MCP config file does not exist"**
   - Ensure `MCP_CONFIG_PATH` points to an existing file
   - Or disable MCP with `ENABLE_MCP=false`

## Security Considerations

- **Never commit secrets** to version control
- **Use environment variables** for sensitive data
- **Rotate tokens regularly** if using token-based auth
- **Restrict `APPROVED_DIRECTORY`** to only necessary paths
- **Monitor logs** for configuration errors and security events
