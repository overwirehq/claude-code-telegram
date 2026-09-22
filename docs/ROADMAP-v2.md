# v2 Roadmap

**Status:** proposal, September 2026
**Baseline:** v1.7.0, `claude-agent-sdk ^0.1.39`, 559 tests, 56% coverage

This document plans the 2.0 release. It deliberately excludes work that already
exists as an open pull request (see [Out of scope](#out-of-scope-covered-by-open-prs));
those are handled by the PR backlog triage, not by this roadmap.

## What v2 is

v1 proved the idea: Claude Code, reachable from a phone. v2 makes it the
*right* way to run Claude Code from a phone:

1. **Interactive, not fire-and-forget.** Claude can ask questions, propose a
   plan, and request permission, and the user can answer from Telegram.
   Today `AskUserQuestion` is allowed but unanswerable, and there is no plan
   or undo flow.
2. **Many conversations at once.** One global lock currently serialises every
   Claude request across every user and every project topic. v2 runs one
   request per conversation, with a global concurrency cap.
3. **Sessions you can see.** List, switch, fork, and pick up sessions that
   scheduled jobs or webhooks started.
4. **Current SDK.** Move from 0.1.x to 0.2.x and use the SDK for permissions,
   hooks, checkpointing and session management instead of home-grown code.
5. **One mode.** Agentic mode becomes the only mode. Classic mode's useful
   pieces (file handling, voice, git helpers) already live in shared modules.
6. **Installable in one command.** Container image and a published package.

Everything else (multi-backend support, more chat platforms) is out of scope
for 2.0. The bet is to be the best Claude-specific bridge, leaning on what
only the Agent SDK provides.

## Out of scope (covered by open PRs)

Do not duplicate these in v2 work. Merge, revise, or close them during triage.

| Area | PR(s) | Related issue |
|------|-------|---------------|
| Alternative model providers (MiniMax, base URL) | #210, #143 | #171, #208 |
| Voice replies (TTS) | #167 | |
| Sending agent-mentioned images / arbitrary files | #204, #191 | |
| Document, image and PDF uploads | #199, #193 | |
| Daily session reset by timezone | #198 | |
| Skill discovery and command-name normalisation | #197 | |
| `/model` command | #160 | #138 |
| Reply/quote context in prompt | #194 | |
| Token auth end-to-end | #190 | #58 |
| Photo album buffering, chunked paste concat | #188, #187 | #186 |
| Custom commands in the bot menu | #179 | #173, #176 |
| Per-chat routing, group trigger prefix, history buffer | #165 | |
| `/schedule` command | #151 | #150 |
| Streaming drafts, rich HTML, follow-up interrupts | #152 | #126 |

Merged in 1.7.0 and therefore no longer listed: #214, #212, #196, #177, #178,
#206, #220 (guarded tools now routed through `can_use_tool`) and #217
(per-tool Allow/Deny approval prompt, closing #216).

One remaining PR interacts with v2 work and should be merged first so v2
builds on it rather than around it: #165, which moves session state from
`user_data` to `chat_data` and is extended in M2.

## Milestones

Sizes: **S** under a day, **M** two to four days, **L** a week or more.
Each item lists the files most likely to change so work can be split.

### M0. Foundations (ship as 1.8, non-breaking)

Preparation that every later milestone depends on. Nothing here changes
behaviour for a default install.

| # | Item | Size |
|---|------|------|
| 0.1 | Bump `claude-agent-sdk` to `^0.2` | M |
| 0.2 | Parse `ResultMessage.subtype` | S |
| 0.3 | Raise `CLAUDE_MAX_TURNS` default | S |
| 0.4 | Container image and package publishing | M |
| 0.5 | Repository hygiene | S |
| 0.6 | CI matrix and coverage gate | S |

**0.1 SDK bump.** Moves the pin from `^0.1.39` to `^0.2.157`. This became
urgent rather than planned: PyPI yanked 0.1.39 for deletion on or after
2026-09-19, and since #224 both `ci.yml` and `release.yml` install from the
committed lock, so the deletion would break every build.

The Python API turned out to be a clean widening. Every symbol
`src/claude/sdk_integration.py` imports still exists in 0.2.157, including the
three private ones (`_errors.MessageParseError`,
`_internal.message_parser.parse_message`, `types.StreamEvent`). 0.1.39
exported 64 public names; 0.2.157 exports all 64 plus 69 more. Nothing was
removed or narrowed. `ClaudeAgentOptions` changed two annotations, both
widenings: `system_prompt` gained union members, and `effort`'s inline
literal became the `EffortLevel` alias. The bot passes a plain `str` for the
first and never sets the second.

One real regression: 0.1.x tolerated `None` for `allowed_tools` /
`disallowed_tools`, 0.2 does not. The transport calls
`list(options.allowed_tools)`, and a new connect-time shadowing check
iterates the same list, so `None` raises `TypeError` before the CLI starts.
Both settings are `Optional`, so `execute_command` now normalises them to
lists.

An earlier draft of this item listed two things to audit. Neither survived
contact:

- *Strict skill-name validation (0.2.129) is a breaking change.* That
  validation applies to `ClaudeAgentOptions.skills`, a field 0.2 introduced
  and this bot never sets. It does not touch `allowed_tools`.
- *`CLAUDE_ALLOWED_TOOLS` parsing needs to strip whitespace.*
  `src/config/settings.py` already does `tool.strip()` per entry. The stray
  space in `.env.example` was cosmetic, never a bug.

The risk that is real sits below the Python API: the SDK bundles the Claude
Code CLI, which jumps from 2.1.49 to 2.1.277. The suite mocks the SDK almost
completely -- `tests/unit/test_claude/test_sdk_integration.py` and
`tests/unit/test_bot/test_stop_button.py` are the only files that import it,
and both patch heavily -- so a green suite shows the types line up, not that
the runtime behaves. The bump was therefore also exercised against the real
bundled CLI, driving `ClaudeSDKManager` directly with no mocks: session
resume, a `can_use_tool` file denial, a Bash boundary rejection, the
interrupt path behind the Stop button, `stream_callback` partial messages,
and interactive approval in both the Allow and the Deny direction. All
behaved as they did on 0.1.39.

One observation worth carrying into #221: with `can_use_tool` set, 0.2 emits
`CanUseToolShadowedWarning` at connect time naming every tool the callback
will never see -- on a default install, `Glob, Grep, LS, Task, TaskOutput,
WebFetch, TodoRead, TodoWrite, WebSearch, Skill`. That is the SDK detecting,
by itself, the condition #219 reported and #220 fixed by hand.

Files: `pyproject.toml`, `poetry.lock`, `src/claude/sdk_integration.py`,
`docs/ROADMAP-v2.md`. Done when the suite passes on 0.2.x and a live smoke
test resumes an existing session.

**0.2 Result subtype.** `execute_command` reads `total_cost_usd` from the
`ResultMessage` but ignores `subtype`. A run that ends with
`error_max_turns` or `error_max_budget_usd` is reported as a normal
completion, which is the likely cause of the false "Task completed" reports
in #172. Surface the subtype in `ClaudeResponse` and render a distinct
footer ("Stopped: turn limit reached. Send a message to continue."). Files:
`src/claude/sdk_integration.py`, `src/bot/orchestrator.py`.

**0.3 Turn limit.** `DEFAULT_CLAUDE_MAX_TURNS = 10` is a chat-era default.
Agentic tasks routinely need 30 to 50 tool round-trips. Raise the default to
50 and rely on `max_budget_usd` as the real safety cap. File:
`src/utils/constants.py`, docs.

**0.4 Distribution.** Add a `Dockerfile` (python:3.12-slim, Node for the
Claude Code CLI, non-root user, `data/` volume) and a `docker-compose.yml`
with the three required variables. Publish to
`ghcr.io/overwirehq/claude-code-telegram` from `release.yml` on tag. Publish
the wheel to PyPI from the same workflow so
`pipx install claude-code-telegram` works. Done when a fresh machine goes
from zero to a responding bot with `docker compose up` and a three-line
`.env`.

**0.5 Hygiene.** Add `.github/ISSUE_TEMPLATE/` (bug, feature, question),
`CODEOWNERS`, and a label set (`bug`, `enhancement`, `sdk`, `security`,
`good first issue`, `needs-triage`). Restore an automated first-pass review
workflow on pull requests. (`docs/tools.md` was corrected in 1.7.0 and
`CONTRIBUTING.md` is rewritten alongside this roadmap.)

**0.6 CI.** Test on 3.11, 3.12 and 3.13. Add mypy to the lint job (the
Makefile runs it, CI does not). Fail the test job below the current coverage
so it can only go up.

### M1. Interactive agent UX

The core of v2. Every item here turns something Claude Code does in the
terminal into something the user can do from a Telegram keyboard.

| # | Item | Size |
|---|------|------|
| 1.1 | Answer `AskUserQuestion` from Telegram | M |
| 1.2 | Plan mode with plan approval | M |
| 1.3 | Permission modes per conversation | M |
| 1.4 | "Allow for this session" on approval prompts | S |
| 1.5 | Undo last change via file checkpointing | M |
| 1.6 | Surface tool failures and subagent activity | S |
| 1.7 | `/effort` command | S |

**1.1 AskUserQuestion.** The tool is in the default allowlist, so Claude
asks questions that nobody can answer; the run stalls or Claude guesses. The
SDK routes the call through `can_use_tool` with the questions in the tool
input, but only for tools that are *not* pre-approved in `allowed_tools`
(#219/#220 established this against a live bot; 1.7.0 strips the
`GUARDED_TOOLS` set from the allowlist handed to the SDK for exactly this
reason). So first add `AskUserQuestion` to that set, then intercept it in the
callback: render each question as an inline keyboard (one
row per option, plus "Other…" which switches the conversation to
free-text reply mode), await the answer with a timeout, and return
`PermissionResultAllow(updated_input=...)` where the input is the original
`questions` list plus an `answers` map of question text to the chosen label
(or list of labels for multi-select). This is the documented SDK mechanism
for `AskUserQuestion`. Reuse the
pending-prompt registry and priority callback prefix that #217 introduces for
Allow/Deny so button presses bypass the sequential lock. Multi-select
questions toggle checkmarks and finish with a "Done" button. Files:
`src/claude/sdk_integration.py` (callback plumbing),
`src/bot/orchestrator.py` (keyboard rendering, callback handler),
`src/bot/update_processor.py` (priority prefix). Done when Claude can ask a
two-question clarification and receive both answers without the user typing.

**1.2 Plan mode.** `/plan <task>` runs with `permission_mode="plan"`. Claude
explores read-only and calls `ExitPlanMode` with the plan text. Intercept
that in `can_use_tool` (add `ExitPlanMode` to `GUARDED_TOOLS` as in 1.1 so
the callback fires; a `PreToolUse` hook matcher is the fallback if it does
not), post the plan as a message with "Approve",
"Revise" and "Cancel" buttons. Approve returns allow and switches the live
client to `acceptEdits` for the remainder of the run via the client's
permission-mode setter (documented for the TypeScript client as
`setPermissionMode`; confirm the Python name during 0.1). Revise prompts for
a text reply that is sent as the next turn while staying in plan mode;
Cancel interrupts. Files as 1.1 plus `src/claude/facade.py` to carry the
mode.
Done when a user can review and approve a multi-file change before any file
is written.

**1.3 Permission modes.** `/mode` shows and sets the conversation's mode:
`default` (approval prompt from #217 for gated tools), `plan`,
`acceptEdits` (edits auto-approved, Bash still prompts), `auto` (the SDK's
classifier decides; requires the CLI version the SDK bundles), and
`bypass` (only if `DISABLE_TOOL_VALIDATION=true`). Store per chat and
thread. Show the current mode in the "Working…" progress message so the
user always knows what will and will not prompt. Files:
`src/bot/orchestrator.py`, `src/config/settings.py` (default mode),
`src/storage/` (persist per conversation).

**1.4 Allow for session.** Extend the #217 keyboard with "Allow for this
session". Implement by returning `PermissionResultAllow` with a
`PermissionUpdate` that adds an allow rule for the tool (and for Bash, the
command prefix) scoped to the session. Persist the choice alongside the
session record so it survives a bot restart. #217 shipped the Allow/Deny
keyboard in 1.7.0; this adds the third button to it.

**1.5 Undo.** Enable `enable_file_checkpointing` together with
`extra_args={"replay-user-messages": None}` so the stream carries
`UserMessage.uuid` for each turn; store that UUID in the messages table. Add
an "Undo" button to the final response; pressing it calls
`client.rewind_files(uuid)` and replies with the list of files restored.
Offer a confirmation step when the rewind spans more than one turn.
Limitation to state in the UI: checkpoints cover `Write`, `Edit` and
`NotebookEdit` only. Changes made through `Bash` (or by subagents) are not
tracked, so the button should say what it will and will not revert. Files:
`src/claude/sdk_integration.py`, `src/storage/repositories.py` (new
column), `src/bot/orchestrator.py`. Done when a bad edit can be reverted
from the phone without touching git.

**1.6 Failures and subagents.** Register `PostToolUseFailure`,
`SubagentStart` and `SubagentStop` hooks. Failures render as a single line in
the progress message ("Bash failed: exit 1") instead of the current generic
"(No content to display)" fallback. Subagent lines show the agent name and
elapsed time at verbose level 1 and above. Files:
`src/claude/sdk_integration.py`, `src/bot/orchestrator.py`.

**1.7 Effort.** `/effort low|medium|high|xhigh|max` sets the SDK `effort`
option per conversation, shown in `/status`. Pairs with `/model` from #160.

### M2. Sessions

Addresses #130, #149 and #209. Depends on #165 (state keyed by chat, not
user) or an equivalent change.

| # | Item | Size |
|---|------|------|
| 2.1 | Key session state by conversation | M |
| 2.2 | `/sessions` list, switch, fork, rename | M |
| 2.3 | Continue a scheduled or webhook run | M |
| 2.4 | Token-based usage reporting | S |

**2.1 Conversation key.** Today the active session lives in
`context.user_data["claude_session_id"]`, so one user has one live session
regardless of chat or topic. Introduce a `ConversationKey(chat_id,
thread_id)` and a `conversations` table (key, working directory, active
session ID, permission mode, effort, verbose level). Project-threads mode
already derives a `chat:thread` state key; generalise it. Files:
`src/bot/orchestrator.py`, `src/storage/database.py` (migration),
`src/storage/repositories.py`, `src/projects/thread_manager.py`.

**2.2 Session browser.** `/sessions` lists the last ten sessions for the
current working directory from the local sessions table joined with the
SDK's `list_sessions` and `get_session_info` helpers (importable from
`claude_agent_sdk`; they read the CLI's local session store, so they also
see sessions started from a terminal on the same machine, which is the
cross-device half of #130). Show title, last used, turns and cost. Each row is an inline
button; pressing it sets the conversation's active session. Long-press
alternatives via a second row: "Fork" (resume with `fork_session=True` so
the original is untouched), "Rename", "Delete". `/new` gains an optional
title. Files: `src/bot/orchestrator.py`, `src/storage/repositories.py`,
`src/claude/session.py`. Done when a user can return to yesterday's
conversation in two taps.

**2.3 Continue automated runs.** Scheduled jobs and webhook handlers
already call `run_command` and get a session ID back, then drop it. Store
it on the notification and add a "Continue in chat" button to the delivered
message. Pressing it binds the current conversation to that session and
working directory. Files: `src/events/handlers.py`,
`src/notifications/`, `src/bot/orchestrator.py`. Closes #149.

**2.4 Usage.** Read `ResultMessage.model_usage` and store input, output and
cache tokens per model alongside cost. `/status` shows tokens always and
dollars only when an API key is configured, since the SDK's cost figure is
meaningless under subscription auth (#209). Files:
`src/claude/sdk_integration.py`, `src/storage/repositories.py`
(`cost_tracking` gains token columns), `src/bot/orchestrator.py`.

### M3. Concurrency

| # | Item | Size |
|---|------|------|
| 3.1 | Per-conversation locking with a global cap | M |
| 3.2 | Steer a running request without killing it | M |
| 3.3 | Automated runs share the cap | S |

**3.1 Locking.** `StopAwareUpdateProcessor` holds one `asyncio.Lock` for
every non-priority update, so two users, or two topics of one user, cannot
run Claude at the same time. Replace it with a lock per `ConversationKey`
and an `asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)` (default 3) around the
Claude call itself. Messages for a busy conversation queue behind its lock
as they do now; messages for an idle conversation run immediately.
`ClaudeSDKManager` holds no per-request state, so concurrent
`execute_command` calls are safe. Key `_active_requests` by conversation
rather than user so Stop buttons target the right run. Files:
`src/bot/update_processor.py`, `src/bot/orchestrator.py`,
`src/config/settings.py`. Done when two topics each show a live progress
message at once. Closes #102.

**3.2 Steering.** #152 interrupts the running request when a follow-up
arrives. The SDK does not accept a new user message while a turn is still
streaming, so true mid-step steering is not available; what is available is
keeping the `ClaudeSDKClient` open after the turn and sending the next
`query()` on it immediately, with no session reload. v2 offers the choice: a
message during a run gets two buttons, "Queue" (hold it and send it as the
next turn the moment the current one finishes, keeping all work) and "Stop
and replace" (interrupt now, the #152 behaviour). Default to "Queue" after
a short timeout. Requires keeping the client object reachable from
`ActiveRequest` and holding it open for the queued turn. Files:
`src/claude/sdk_integration.py`, `src/bot/orchestrator.py`.

**3.3 Automated runs.** Scheduler and webhook handlers bypass PTB and
therefore the lock today; under 3.1 they take the same semaphore so a burst
of webhooks cannot starve chat.

### M4. 2.0 cleanup (breaking)

| # | Item | Size |
|---|------|------|
| 4.1 | Remove classic mode | L |
| 4.2 | Remove dead code and stale settings | S |
| 4.3 | Test coverage to 75% | L |
| 4.4 | Upgrade guide | S |

**4.1 Classic mode.** `src/bot/handlers/` and the classic-only parts of
`src/bot/features/` are roughly 6,500 lines that duplicate agentic mode
with a different UI. Deprecate in 1.8 (log a warning when
`AGENTIC_MODE=false`), remove in 2.0. Keep the three classic commands that
have no agentic equivalent as agentic commands: `/cd` (alias of `/repo`),
`/export` (session export already lives in `src/bot/features/`), and
`/git` (read-only status and log). Drop `/actions`, `/ls`, `/pwd`,
`/projects`, `/continue`, `/end`; Claude does all of these better from a
plain message.

**4.2 Dead code.** `src/claude/monitor.py` survives only for two helper
functions; move them into `src/security/validators.py`. Remove `USE_SDK`
(the CLI backend was deleted in 1.4). Remove `ENABLE_QUICK_ACTIONS`,
`QUICK_ACTIONS_TIMEOUT` and `ENABLE_CONVERSATION_MODE` with classic mode.
Fold `docs/SDK_DUPLICATION_REVIEW.md` findings that are complete into the
changelog and delete the document.

**4.3 Tests.** Current gaps: `src/main.py` 3%, `src/storage/session_storage.py`
6%, `src/scheduler/scheduler.py` 7%, `src/events/middleware.py` 7%,
`src/mcp/telegram_server.py` 47%. Add a fake SDK transport fixture that
yields scripted `AssistantMessage`, `ToolUseBlock` and `ResultMessage`
sequences so orchestrator flows (approval, question, plan, undo, steer) are
tested end to end without a CLI. Raise the coverage gate from 0.6 as each
milestone lands.

**4.4 Upgrade guide.** `docs/upgrading-to-v2.md`: removed settings and
their replacements, the `conversations` migration, what happens to existing
`user_data` session state (migrated to the user's private chat on first
message), and the new permission-mode default.

### Stretch (after 2.0)

- **Structured webhook output.** Use `output_format` with a JSON schema for
  GitHub PR events so the webhook handler can post a review back through
  `gh` instead of only summarising to Telegram.
- **Per-topic subagents.** Define `agents` per project in
  `config/projects.yaml` (a reviewer, a test-runner) and expose them as
  `/agent <name>` in that project's topic.
- **Telegram Mini App** for the session browser and diff viewer, if inline
  keyboards prove too cramped for 2.2.

## Release mechanics

1. **1.8.0**: M0 complete. Deprecation warning for classic mode. Announce
   the v2 plan in the release notes with a link to this document.
2. **2.0.0-beta.1**: M1 and M2 complete on `main`, published as a
   pre-release tag and a `beta` Docker tag. Two to three weeks of feedback.
3. **2.0.0-beta.2**: M3 complete. Concurrency defaults tuned from beta
   feedback.
4. **2.0.0**: M4 complete. Upgrade guide published. Classic mode removed.

Each milestone is a GitHub milestone with one issue per numbered item so
contributors can claim work. Items marked S are candidates for
`good first issue`.

## Suggested order

M0 first, in full, because the SDK bump changes the surface every later item
builds on. Then M1.1 and M1.2 (the two features users notice most), then
M3.1 (the fix that makes project-threads mode viable), then the rest of M1
and M2 in parallel, then M3.2, then M4.

Rough total: M0 about two weeks, M1 three, M2 two, M3 two, M4 three. Around
twelve weeks of focused work, less with two or three regular contributors
taking S and M items.
