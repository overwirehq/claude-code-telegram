# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed
- **The Claude review workflow posts one review per pull request instead of one per push**: the job reruns on every `synchronize` and posted a fresh comment each time, so #236 collected nine full reviews in four and a half hours, each restating what the last had already settled. `use_sticky_comment` was set but does nothing here — it only applies to the action's tag mode, and this workflow supplies `prompt`, so the action posts nothing itself and the review is whatever the prompt tells Claude to post. The prompt now edits its own previous comment with `gh pr comment --edit-last --create-if-none`, so the pull request carries one review at the current head and GitHub keeps the superseded text in the comment's edit history. The inert input is removed rather than left to look load-bearing
- **The review reports only what should block the merge**: most of the length of those nine reviews was praise, an account of what had been checked, and cosmetic nits ("after 1 turns"), and every nit drew another push, which triggered another review — that loop, not the reviewing, was the spam. The prompt now names what qualifies (a security regression, a bug, an untested behaviour change, a missing setting or CHANGELOG entry) and rules out the rest, including anything `black`, `isort` or `flake8` already gates, and findings that cannot be confirmed from the code. It also reads its own previous review first so it does not repeat itself, but what settles a finding is the code at the current head rather than a reply claiming a fix: the replies are contributor-authored and untrusted like the rest of the pull request, so an earlier finding is re-checked against the diff and raised again unchanged when the code does not carry the claimed fix
- **The `review` check no longer fails red on every fork pull request**: `actions/checkout@v6` refuses to check out fork PR code from a `pull_request_target` workflow unless `allow-unsafe-pr-checkout: true` is set, so a fork PR died in about 9 seconds before reading any code, and `allowed_non_write_users: "*"` from #228 was doing nothing for the outside contributors who are most of this repository's traffic. Opting in was the wrong fix: the Claude CLI reads `.claude/settings.json` from the tree it runs in, so a fork that added a hook there would execute it with the job's secrets in the environment, and the action's secret scrubbing is documented as best-effort — the read-only tool allowlist is no boundary against that, because a hook does not go through it. The checkout now takes the pull request head only for an in-repo branch and the *base* commit for a fork, so fork code is never fetched. The reviewer takes the change from `gh pr diff`, which needs no checkout, and uses the working tree for surrounding context; the prompt states which case it is in, so it cannot mistake a file that predates the change for evidence that something is missing. `CLAUDE.md` is now read from the base branch too, so a fork can no longer edit the file the prompt sends the reviewer to read
- **The `review` check no longer goes red when the reviewer runs out of turns**: exhausting `--max-turns` is not a graceful stop — the action exits with no output, so the check fails and reads like the pull request is broken, which is what happened on #236 once its diff reached thirteen files. The narrower prompt above is the fix; the ceiling also moves from 40 to 80 for headroom

## [1.8.0] - 2026-09-22

Released as a minor rather than a patch: `claude-agent-sdk` moves from the 0.1
line to the 0.2 line, and the Claude Code CLI bundled with it goes from 2.1.49
to 2.1.277. Behaviour is unchanged for a default install, but that is a large
enough step under the bot to be worth its own version. This is the first item
of M0 in `docs/ROADMAP-v2.md`, which plans to ship as 1.8.

### Documentation
- **v2 roadmap**: `docs/ROADMAP-v2.md` plans the 2.0 release (SDK 0.2, interactive permission and question UX, per-conversation concurrency, session browser, classic-mode removal, container distribution), scoped to work not already covered by open pull requests
- **Community files**: issue forms (bug, feature, question), a pull request template with a hand-testing section, `CODEOWNERS`, `MAINTAINERS.md` (roles, one-week response promise, label set, path to maintainership), a Contributor Covenant 2.1 `CODE_OF_CONDUCT.md`, and a rewritten `CONTRIBUTING.md` (PR scope rules, AI-assisted contribution policy, current project layout)

### Changed
- **`claude-agent-sdk` moved from `^0.1.39` to `^0.2.157`**: PyPI yanked 0.1.39 for deletion on or after 2026-09-19, and since #224 both `ci.yml` and `release.yml` install from the committed lock, so the deletion would have broken every build and every release. The lock delta is one package; the SDK's new requirements (`jsonschema`, `sniffio`, a narrower `mcp` range) are all already satisfied by packages the lock carries, so nothing else moved. The Python API is a clean widening: every symbol `src/claude/sdk_integration.py` imports still exists, the three private ones included, and `ClaudeAgentOptions` only widened two annotations the bot either passes a plain `str` for or never sets. The bundled Claude Code CLI goes from 2.1.49 to 2.1.277
- **Repository moved to the `overwirehq` organisation**: the canonical location is now `github.com/overwirehq/claude-code-telegram`. GitHub redirects the old URLs, but every link in the README, docs, issue templates and packaging metadata has been updated. Existing clones keep working; `git remote set-url origin https://github.com/overwirehq/claude-code-telegram.git` points one at the new location directly

### Fixed
- **The review workflow no longer fails red on pull requests opened by a bot**: `anthropics/claude-code-action` refuses a bot-initiated run unless `allowed_bots` names the account, and it fails the job rather than skipping it, so the `review` check died in about 20 seconds with `Workflow initiated by non-human actor` before reading any code — the same shape of misleading red check that #227/#228 fixed for `Invalid OIDC token`, at a different gate in the same action. `allowed_bots` now names `claude[bot]`, and the job condition admits a bot-opened pull request only when its branch lives in this repository, which takes write access to push. Commit author fields are deliberately not the check: `git commit --author` sets them to any name and address. Any other bot, and any bot pull request from a fork, now skips instead of failing. Pull requests from human contributors are unchanged, forks included
- **`CLAUDE_ALLOWED_TOOLS`/`CLAUDE_DISALLOWED_TOOLS` unset no longer breaks the SDK connection**: both settings are `Optional`, and `execute_command` passed `None` straight through to `ClaudeAgentOptions`, which declares them as `list[str]`. 0.1.x tolerated that with a truthiness check; 0.2 does not — the transport calls `list(options.allowed_tools)` and a new connect-time shadowing check iterates the same list, so `None` raised `TypeError` before the CLI even started. Both values are now normalised to a list on every path, which leaves behaviour unchanged (`[]` and `None` are both falsy, so the CLI omits the flags either way)
- **The Claude Code Review workflow runs again**: every run since 2026-02-19 died in about 30 seconds with `Invalid OIDC token`, before reading any code, and left a misleading red `review` check on each PR. The action mints its GitHub token by trading the Actions OIDC token with Anthropic, which requires the Claude GitHub App on the repository; that exchange returned `401`. The workflow now passes the built-in `GITHUB_TOKEN` as `github_token`, which skips the exchange, and drops the `id-token: write` permission it no longer needs. Checkout uses the PR head instead of `refs/pull/N/merge`, which GitHub computes asynchronously and drops on conflicted PRs. The review now also runs on pull requests from outside contributors, who are most of this repository's traffic: the action otherwise refuses any actor without write access, so `allowed_non_write_users` is set. That puts untrusted PR content in the prompt, so the read-only tool allowlist — no `Write`, `Edit`, `WebFetch` or arbitrary `Bash` — is the boundary, and the prompt now states that PR content is data rather than instructions. The model is pinned to Sonnet, which was already the default.
- **CI now catches lockfile drift**: the `lint` and `test` jobs ran `poetry lock && poetry install`, which regenerated `poetry.lock` in place. A `pyproject.toml` dependency change with a stale lock therefore passed every PR and failed only at release time, as it did for v1.7.0 (#196). Both jobs now verify the lock with `poetry check --lock` and install from the committed lock, so CI tests the dependency set that actually ships rather than resolving a fresh one on every run.
- **Green test suite**: `test_allowed_tools_none_unaffected_by_approval_filter` asserted that `allowed_tools` is `None` under `DISABLE_TOOL_VALIDATION`, but #206 had already changed that value to `[]` for type correctness. #217 was written against a base without #206, so the collision only surfaced once both were on `main`, leaving the default branch red. The expectation is now `[]`, and the test is renamed to say so
- **Project URLs**: the Homepage, Repository and Documentation links in `pyproject.toml` pointed at `github.com/richardatkinson/...`, an owner unrelated to this project, so `pip show` and any future PyPI listing linked to the wrong place

### Added
- **Dependabot**: weekly PRs for `claude-agent-sdk`, `python-telegram-bot` and `anthropic`; monthly grouped PRs for other Python dependencies and GitHub Actions
- **Claude Code Review workflow**: read-only first-pass review comment on every non-draft pull request, including fork PRs. Requires the `CLAUDE_CODE_OAUTH_TOKEN` (or `ANTHROPIC_API_KEY`) repository secret

## [1.7.0] - 2026-09-11

Released as a minor rather than a patch: the security fix below changes runtime
behaviour for every deployment. See the upgrade note under **Changed**.

### Security
- **Tool boundary checks now actually run** (#220, closes #219). The `can_use_tool` callback enforces the `APPROVED_DIRECTORY` boundary on Claude's own tool calls, but the SDK only consults it when the Claude CLI sends a `can_use_tool` control request — and the CLI resolves allow rules first, so any tool named in `CLAUDE_ALLOWED_TOOLS` was pre-approved and never reached it. Since the default list contains `Read`, `Write`, `Edit` and `Bash`, the checks were silently inert on every default install. Guarded tools are now stripped from the `allowed_tools` passed to the SDK, and `autoAllowBashIfSandboxed` — a second, independent bypass — is disabled whenever the checks are meant to run. Guarding also now covers `MultiEdit`, `NotebookEdit` and `NotebookRead`, which were never included.

### Changed
- **Upgrade note for #220**: tool calls targeting paths outside `APPROVED_DIRECTORY` are now denied where they previously succeeded. This restores the behaviour the documentation always described, but it is a real change for any deployment that relied on the gap. Routing each guarded call through the callback also adds one control-request round trip per call. `DISABLE_TOOL_VALIDATION=true` restores the previous permissive behaviour for trusted environments.
- **Known limitation**: `CLAUDE_ALLOWED_TOOLS` does not block tools left off the list — unlisted tools reach the callback, which allows anything passing its boundary checks. `CLAUDE_DISALLOWED_TOOLS` is the only setting that denies a tool. Tracked in [#221](https://github.com/overwirehq/claude-code-telegram/issues/221); `SECURITY.md` and `docs/tools.md` now describe the actual behaviour.

### Fixed
- **Polling no longer dies permanently**: a `getUpdates` request torn down mid-flight (unstable network, proxy or tunnel drop) left its connection checked out of a pool holding exactly one, so every later poll failed with "Pool timeout" and never recovered, even after the network came back (#214, closes #213)
- **Startup failures exit instead of hanging**: a `ConfigurationError` during startup left aiosqlite's non-daemon threads running, so `sys.exit(1)` blocked forever in `wait_for_thread_shutdown()` and the process hung rather than exiting — which supervisors read as a healthy service (#212, closes #211)
- **Webhook mode works**: `run_webhook()` manages its own event loop and raised "Cannot close a running event loop" when called inside `asyncio.run()`. Now uses `start_webhook()`, and `pyproject.toml` declares the `webhooks` extra the path requires (#196)
- **Scheduled jobs no longer block the event bus**: `AgentHandler` awaited Claude inline, so a scheduled job stalled every other event until it finished. Jobs now run as background tasks under a concurrency limit (#177, closes #174)
- **Scheduler no longer drops missed jobs**: APScheduler's default `misfire_grace_time` silently discarded jobs that fired while a long Claude command was running. Now set to `None` with `coalesce` enabled (#178, closes #175)
- **Tool lists are typed correctly**: `DISABLE_TOOL_VALIDATION=true` passes `[]` rather than `None` for allowed/disallowed tools, matching the `list[str]` that `ClaudeAgentOptions` declares. Both values are falsy so the CLI omits the flags either way; behaviour is unchanged (#206)

## [1.6.1] - 2026-09-11

### Security
- **Proxy credentials no longer logged**: `HTTPS_PROXY`/`HTTP_PROXY` URLs of the form `scheme://user:pass@host` had their password written to the structured logs in plaintext. The password is now masked before logging, keeping the scheme, user and host readable (#218, closes #169)

### Documentation
- **Security reporting channel**: `SECURITY.md` advertised no working private disclosure channel — the contact line was still a template placeholder. Reporters are now directed to GitHub Security Advisories. Requires private vulnerability reporting to be enabled on the repository (#218, closes #215)
- **Corrected security policy claims**: the supported-versions table listed `0.1.x`, and token-based auth was listed as "implemented and active" despite still being backed by `InMemoryTokenStorage`. Token auth is now documented as a known gap pointing at #58, with `ALLOWED_USERS` named as the access control to use (#218)

## [1.6.0] - 2026-03-30

### Added
- **Image/screenshot analysis**: Images sent to the bot are now passed as multimodal content blocks via the SDK, enabling Claude to actually see and analyze them (#168, closes #137)
- **Exponential backoff retry**: Transient `CLIConnectionError` failures are automatically retried with exponential backoff (1s → 3s → 9s, capped at 30s). MCP config errors and timeouts are correctly excluded (#170, closes #60)
- **Local whisper.cpp voice transcription**: New `VOICE_PROVIDER=local` option for offline voice transcription via whisper.cpp + ffmpeg. No API keys required (#158)
- **`make run-watch`**: Auto-restart during development via watchfiles (#158)
- **Inline Stop button**: Cancel running Claude requests with a ⏹ button in the progress message (#122)
- **Slash command passthrough**: Unknown `/commands` in agentic mode are forwarded to Claude as prompts (#131)
- **Proxy support**: Explicit proxy configuration for httpx client via `HTTPS_PROXY`/`HTTP_PROXY` env vars (#166)

### Fixed
- **Empty responses**: "(No content to display)" after tool-heavy tasks — added missing `StreamUpdate` helper methods, fixed `ConversationEnhancer` call signature, and added fallback for tool-only responses (#136, closes #135)
- **ThinkingBlock raw output**: `ThinkingBlock` objects no longer print as raw Python objects — proper `isinstance` checks extract `.thinking` text (#162, closes #161)

## [1.5.0] - 2026-03-04

### Added
- **Voice Message Transcription**: Send voice messages for automatic transcription and Claude processing. Dual provider support: Mistral Voxtral (default) and OpenAI Whisper (#106)
- **`/restart` command**: Restart bot process from Telegram, plus `set_my_commands` timing fix for reliable command registration on startup (#112)
- **Streaming partial responses**: Stream Claude's output in real-time via Telegram `sendMessageDraft` API. Enable with `ENABLE_STREAM_DRAFTS=true` (#123)

### Fixed
- **`/actions` crash**: Corrected `SessionModel` constructor argument in `get_suggestions` (#125, closes #119)
- **Model config ignored**: `claude_model` setting now passed to SDK `ClaudeAgentOptions`. Default deferred to CLI instead of hardcoded sonnet (#121)

### Documentation
- Linux `aiolimiter` DBus installation workaround (#124)

## [1.4.0] - 2026-02-27

### Added
- **Outbound image support**: Claude can now auto-detect and send images to Telegram, plus MCP `send_image_to_user` tool (#99)
- **CLAUDE.md loading**: Project-level CLAUDE.md files are loaded from the working directory and appended to the system prompt
- **Configurable reply quoting**: `REPLY_QUOTE` setting controls message quoting behavior, centralized via PTB Defaults (#111)
- **`max_budget_usd` cost cap**: Per-request cost limit passed to SDK via `ClaudeAgentOptions` (#95)
- **`Skill` and `AskUserQuestion`** added to default allowed tools (#85, #87)
- **Documentation site**: Docs index and README linking (#92)

### Changed
- **ToolMonitor replaced with SDK `can_use_tool` callback**: Security validation now uses the native SDK hook instead of a custom wrapper. `SecurityValidator` wired directly into `ClaudeAgentOptions.can_use_tool` (#62)
- **`DISABLE_TOOL_VALIDATION=true`** now passes `allowed_tools=None` to the SDK, fully bypassing tool name validation
- **Phase 5 cleanup**: `src/claude/` reduced from 2,774 to 1,316 lines (#96)
- **PTB `AIORateLimiter`** replaces manual sync-local `RetryAfter` retry (#86)
- **Project thread sync throttling**: Configurable `PROJECT_THREADS_SYNC_ACTION_INTERVAL_SECONDS` to avoid Telegram API rate limits (#84)
- **GitHub Actions upgraded** to latest versions for Node 24 compatibility (#67, #68)

### Fixed
- **Empty `CLAUDE_CLI_PATH` causing Permission denied**: Empty string coerced to `None` so SDK auto-discovers the CLI
- **Session resume failing** with generic exit code 1 (#94)
- **Progress message deletion crash**: Bot no longer stops mid-response when progress message deletion fails (#107)
- **General topic routing**: Messages in the General topic of forum supergroups now route correctly (#110)
- **Session ownership enforcement**: `load_session` and `get_or_create_session` now validate ownership (#83)
- **Bash boundary enforcement**: `cd` and chained commands checked against directory boundary (#69)
- **Handler robustness**: Potential `UnboundLocalError` resolved in message handlers (#66)
- **Claude Code internal paths**: `~/.claude/plans/` and `todos/` allowed in tool validation (#89)
- **`Topic_not_modified` treated as success** in topic sync instead of raising an error
- **Test fixes**: `is_forum=False` set on MagicMock chats to prevent test failures (#110)

### Previously Added
- **Agentic Mode** (default interaction model):
  - `MessageOrchestrator` routes messages to agentic (3 commands) or classic (13 commands) handlers based on `AGENTIC_MODE` setting
  - Natural language conversation with Claude -- no terminal commands needed
  - Automatic session persistence per user/project directory
- **Event-Driven Platform**:
  - `EventBus` -- async pub/sub system with typed event subscriptions (UserMessage, Webhook, Scheduled, AgentResponse)
  - `AgentHandler` -- bridges events to `ClaudeIntegration.run_command()` for webhook and scheduled event processing
  - `EventSecurityMiddleware` -- validates events before handler processing
- **Webhook API Server** (FastAPI):
  - `POST /webhooks/{provider}` endpoint for GitHub, Notion, and generic providers
  - GitHub HMAC-SHA256 signature verification
  - Generic Bearer token authentication
  - Atomic deduplication via `webhook_events` table
  - Health check at `GET /health`
- **Job Scheduler** (APScheduler):
  - Cron-based job scheduling with persistent storage in `scheduled_jobs` table
  - Jobs publish `ScheduledEvent` to event bus on trigger
  - Add, remove, and list jobs programmatically
- **Notification Service**:
  - Subscribes to `AgentResponseEvent` for Telegram delivery
  - Per-chat rate limiting (1 msg/sec) to respect Telegram limits
  - Message splitting at 4096 char boundary
  - Broadcast to configurable default chat IDs
- **Database Migration 3**: `scheduled_jobs` and `webhook_events` tables, WAL mode enabled
- **Automatic Session Resumption**: Sessions are now automatically resumed per user+directory
  - SDK integration passes `resume` parameter to Claude Code for real session continuity
  - Session IDs extracted from Claude's `ResultMessage` instead of generated locally
  - `/cd` looks up and resumes existing sessions for the target directory
  - Auto-resume from SQLite database survives bot restarts
  - Graceful fallback to fresh session when resume fails
  - `/new` and `/end` are the only ways to explicitly clear session context

### Recently Completed

#### Storage Layer Implementation (TODO-6) - 2025-06-06
- **SQLite Database with Complete Schema**:
  - 7 core tables: users, sessions, messages, tool_usage, audit_log, user_tokens, cost_tracking
  - Foreign key relationships and proper indexing for performance
  - Migration system with schema versioning and automatic upgrades
  - Connection pooling for efficient database resource management
- **Repository Pattern Data Access Layer**:
  - UserRepository, SessionRepository, MessageRepository, ToolUsageRepository
  - AuditLogRepository, CostTrackingRepository, AnalyticsRepository
- **Persistent Session Management**:
  - SQLiteSessionStorage replacing in-memory storage
  - Session persistence across bot restarts and deployments
- **Analytics and Reporting System**:
  - User dashboards with usage statistics and cost tracking
  - Admin dashboards with system-wide analytics

#### Telegram Bot Core (TODO-4) - 2025-06-06
- Complete Telegram bot with command routing, message parsing, inline keyboards
- Navigation commands: /cd, /ls, /pwd for directory management
- Session commands: /new, /continue, /status for Claude sessions
- File upload support, progress indicators, response formatting

#### Claude Code Integration (TODO-5) - 2025-06-06
- Async process execution with timeout handling
- Session state management and cross-conversation continuity
- Streaming JSON output parsing, tool call extraction
- Cost tracking and usage monitoring

#### Authentication & Security Framework (TODO-3) - 2025-06-05
- Multi-provider authentication (whitelist + token)
- Rate limiting with token bucket algorithm
- Input validation, path traversal prevention
- Security audit logging with risk assessment
- Bot middleware framework (auth, rate limit, security, burst protection)

## [0.1.0] - 2025-06-05

### Added

#### Project Foundation (TODO-1)
- Complete project structure with Poetry dependency management
- Exception hierarchy, structured logging, testing framework
- Code quality tools: Black, isort, flake8, mypy with strict settings

#### Configuration System (TODO-2)
- Pydantic Settings v2 with environment variable loading
- Environment-specific overrides (development, testing, production)
- Feature flags system for dynamic functionality control
- Comprehensive validation with cross-field dependencies

## Development Status

- **TODO-1**: Project Structure & Core Setup -- Complete
- **TODO-2**: Configuration Management -- Complete
- **TODO-3**: Authentication & Security Framework -- Complete
- **TODO-4**: Telegram Bot Core -- Complete
- **TODO-5**: Claude Code Integration -- Complete
- **TODO-6**: Storage & Persistence -- Complete
- **TODO-7**: Advanced Features -- Complete (agentic platform, webhooks, scheduler, notifications)
- **TODO-8**: Complete Testing Suite -- In progress
- **TODO-9**: Deployment & Documentation -- In progress
