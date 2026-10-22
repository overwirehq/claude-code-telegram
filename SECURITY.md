# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 1.7.x   | Yes                |
| < 1.7   | No -- please upgrade |

## Security Model

The Claude Code Telegram Bot implements a defense-in-depth security model with multiple layers:

### 1. Authentication & Authorization
- **User Whitelist**: Only pre-approved Telegram user IDs can access the bot
- **Token-Based Auth**: Optional token-based authentication for additional security
  (see the caveat under *Current Security Status* before relying on it)
- **Session Management**: Secure session handling with timeout and cleanup

### 2. Directory Boundaries
- **Approved Directory**: All operations confined to a pre-configured directory tree
- **Path Validation**: Prevents directory traversal attacks (../../../etc/passwd)
- **Permission Checks**: Validates file system permissions before operations
- **Pre-execution Tool Checks**: Claude's own tool calls are validated before they run
  via the SDK `can_use_tool` callback -- file paths for `Read`, `Write`, `Edit`,
  `MultiEdit`, `NotebookEdit` and `NotebookRead`, and directory escapes for `Bash`.
  Active by default; see *Tool Call Enforcement* below for exactly when it applies.

### 3. Input Validation
- **Command Sanitization**: All user inputs sanitized to prevent injection attacks
- **File Type Validation**: Only allowed file types can be uploaded
- **Path Sanitization**: Removes dangerous characters and patterns (`;`, `&&`, `$()`, `..`)
- **Secret File Protection**: Blocks access to `.env`, `.ssh`, `id_rsa`, `.pem` files

### 4. Rate Limiting
- **Request Rate Limiting**: Token bucket algorithm prevents abuse with configurable limits
- **Cost-Based Limiting**: Tracks and limits Claude usage costs per user
- **Burst Protection**: Configurable burst capacity prevents spike attacks

### 5. Audit Logging
- **Authentication Events**: All login attempts and auth failures logged
- **Command Execution**: All commands and file operations logged
- **Security Violations**: Path traversal attempts, injection attempts, and other violations logged
- **Risk Assessment**: Automatic severity classification for security events

### 6. Webhook Authentication
- **GitHub HMAC-SHA256**: Webhook payloads verified against `X-Hub-Signature-256` header using a shared secret
- **Generic Bearer Token**: Non-GitHub providers authenticated via `Authorization: Bearer <token>` header
- **Deduplication**: Atomic `INSERT OR IGNORE` on delivery ID prevents replay attacks
- **Event Security Middleware**: Validates webhook events before handler processing

## Current Security Status

The following are implemented and active:

- Whitelist authentication (`ALLOWED_USERS`)
- Rate limiting with token bucket algorithm (request and cost-based)
- Input validation with path traversal, command injection, and zip bomb protection
- Directory isolation with approved directory boundaries
- Security audit logging with risk assessment and event tracking
- Bot middleware framework (auth, rate limit, security, burst protection)
- Webhook signature verification (GitHub HMAC-SHA256, generic Bearer token)
- Event security middleware for webhook and scheduled event validation
- Configuration security via Pydantic validators and SecretStr
- Pre-execution boundary checks on Claude's tool calls (`can_use_tool`)

### Tool Call Enforcement

The `can_use_tool` callback in `src/claude/sdk_integration.py` is what enforces the
approved-directory boundary on tool calls **Claude itself initiates** -- the case
where Claude is steered off course by content it reads mid-task, rather than by
the user's message (user input is covered by layer 3).

The callback is reactive: the SDK runs it only when the Claude CLI sends a
`can_use_tool` control request, and the CLI resolves its allow rules before asking.
A tool named in `CLAUDE_ALLOWED_TOOLS` is therefore pre-approved and never reaches
the callback. To keep the checks live, `ClaudeSDKManager.execute_command` removes
the guarded tools from the `allowed_tools` it hands the SDK -- scoped rules such as
`Bash(git:*)` or `Read(//etc/**)` included, since a scoped rule pre-approves the
calls it matches just like a bare tool name -- and disables
`autoAllowBashIfSandboxed` (a second bypass, which auto-approves sandboxed Bash
without a control request). Before this was fixed the checks were wired up but
never consulted on a default configuration
([#219](https://github.com/overwirehq/claude-code-telegram/issues/219)).

Routing a tool call through the callback costs one local stdio round trip to the
CLI subprocess plus ~30-90 microseconds of validation -- negligible against the
model latency of the tool call itself, so `Read` is gated along with the mutating
tools.

**When it is active:** whenever a `SecurityValidator` is wired (the default for the
bot) and `DISABLE_TOOL_VALIDATION` is `false` (the default).

**When it is not:**

- `DISABLE_TOOL_VALIDATION=true` -- no allow/deny lists are sent to the SDK, guarded
  tools are left pre-approved, and sandboxed Bash is auto-approved. This is the
  documented escape hatch for trusted environments, and it turns these checks off.
- Direct use of `ClaudeSDKManager` without a `SecurityValidator` (there is no
  validator to consult).

Note that `CLAUDE_ALLOWED_TOOLS` is not itself an enforcement boundary while the
callback is wired: a tool left out of the list is not blocked, it is simply routed
to the callback, which allows anything that passes the boundary checks. Use
`CLAUDE_DISALLOWED_TOOLS` to actually block a tool.

**Known gap:** token-based authentication is not usable end to end. The provider
exists, but `src/main.py` still backs it with `InMemoryTokenStorage`, so issued
tokens are lost on restart and there is no supported flow for issuing one.
**Use `ALLOWED_USERS` as the access control for any real deployment.** Tracked in
[#58](https://github.com/overwirehq/claude-code-telegram/issues/58).

## Security Configuration

### Required Security Settings

```bash
# Base directory for all operations (CRITICAL)
APPROVED_DIRECTORY=/path/to/approved/projects

# User access control
ALLOWED_USERS=123456789,987654321  # Telegram user IDs

# Optional: Token-based authentication
# NOTE: incomplete -- see the known gap under "Current Security Status" (#58).
# ENABLE_TOKEN_AUTH=true
# AUTH_TOKEN_SECRET=your-secret-here  # Generate with: openssl rand -hex 32
```

### Webhook Security Settings

```bash
# GitHub webhook signature verification
GITHUB_WEBHOOK_SECRET=your-github-webhook-secret

# Generic webhook Bearer token
WEBHOOK_API_SECRET=your-api-secret

# API server (required for webhooks)
ENABLE_API_SERVER=true
API_SERVER_PORT=8080
```

### Recommended Security Settings

```bash
# Strict rate limiting for production
RATE_LIMIT_REQUESTS=5
RATE_LIMIT_WINDOW=60
RATE_LIMIT_BURST=10

# Cost controls
CLAUDE_MAX_COST_PER_USER=5.0

# Security features
ENABLE_TELEMETRY=true  # For security monitoring
LOG_LEVEL=INFO         # Capture security events

# Environment
ENVIRONMENT=production  # Enables strict security defaults
```

## Security Best Practices

### For Administrators

1. **Directory Configuration**
   ```bash
   # Use minimal necessary permissions
   chmod 755 /path/to/approved/projects

   # Avoid sensitive directories
   # Don't use: /, /home, /etc, /var
   # Use: /home/user/projects, /opt/bot-projects
   ```

2. **Token Management**
   ```bash
   # Generate secure secrets
   openssl rand -hex 32

   # Store in environment, never in code
   export AUTH_TOKEN_SECRET="generated-secret"
   export GITHUB_WEBHOOK_SECRET="generated-secret"
   export WEBHOOK_API_SECRET="generated-secret"
   ```

3. **User Management**
   ```bash
   # Get Telegram User ID: message @userinfobot
   # Add to whitelist
   export ALLOWED_USERS="123456789,987654321"
   ```

4. **Monitoring**
   ```bash
   # Enable logging and monitoring
   export LOG_LEVEL=INFO
   export ENABLE_TELEMETRY=true

   # Monitor logs for security events
   tail -f bot.log | grep -i "security\|auth\|violation"
   ```

### For Developers

1. **Never Commit Secrets** -- use `.gitignore` for `.env`, `*.key`, `*.pem`
2. **Use Type Safety** -- all functions must have type hints (`mypy --strict`)
3. **Validate All Inputs** -- use `SecurityValidator` for user-provided paths and commands
4. **Log Security Events** -- use structlog with `violation_type` and `user_id` context

## Threat Model

### Threats We Protect Against

1. **Directory Traversal** (High Priority) -- path traversal, symlink attacks
2. **Command Injection** (High Priority) -- shell injection, env var injection
3. **Unauthorized Access** (Medium Priority) -- non-whitelisted users, token replay
4. **Resource Abuse** (Medium Priority) -- rate limit bypass, cost limit violations
5. **Webhook Forgery** (Medium Priority) -- unsigned payloads, replay attacks
6. **Information Disclosure** (Low Priority) -- sensitive file exposure, error leakage

### Threats Outside Scope

- Network-level attacks (handled by hosting infrastructure)
- Telegram API vulnerabilities (handled by Telegram)
- Host OS security (handled by system administration)

## Reporting a Vulnerability

**Please do not open a public GitHub issue for a security vulnerability.**

Report it privately through GitHub Security Advisories:

**https://github.com/overwirehq/claude-code-telegram/security/advisories/new**

That form is private to you and the maintainers, supports attachments and
follow-up discussion, and lets us credit you on the published advisory. You can
also reach it from the repository's **Security** tab -> **Report a vulnerability**.

Please include:

- A description of the issue and the component it affects
- Steps to reproduce, ideally with a minimal configuration
- The potential impact, and any preconditions an attacker would need
- A suggested mitigation, if you have one

Note that this bot is designed to execute commands on a host machine on behalf
of authorised Telegram users. Reports are most useful when they show a way to
cross one of the boundaries described in the threat model above -- for example
escaping `APPROVED_DIRECTORY`, bypassing the user whitelist or rate limits,
forging a webhook, or reaching a secret the validator is meant to block.

### Response Process

1. **Acknowledgment** within 48 hours
2. **Initial assessment** within 1 week
3. **Fix development** as soon as possible
4. **Security advisory** published after the fix, crediting the reporter unless
   they ask otherwise

### Maintainer setup

Private vulnerability reporting must be enabled for the advisory link above to
work: **Settings -> Advanced Security -> Private vulnerability reporting ->
Enable**. Keep it on; it is the only private channel this policy advertises.

## Production Checklist

- [ ] `APPROVED_DIRECTORY` properly configured and restricted
- [ ] `ALLOWED_USERS` whitelist configured
- [ ] Rate limiting enabled and configured
- [ ] Logging enabled and monitored
- [ ] Authentication tokens properly secured
- [ ] `GITHUB_WEBHOOK_SECRET` set (if using GitHub webhooks)
- [ ] `WEBHOOK_API_SECRET` set (if using generic webhooks)
- [ ] API server behind reverse proxy with TLS (if enabled)
- [ ] Environment variables properly configured
- [ ] File permissions properly set
- [ ] Network access properly restricted
- [ ] All dependencies updated to latest secure versions
