"""Configuration management using Pydantic Settings.

Features:
- Environment variable loading
- Type validation
- Default values
- Computed properties
- Environment-specific settings
"""

import json
from pathlib import Path
from typing import Any, List, Literal, Optional

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from src.utils.constants import (
    DEFAULT_CLAUDE_MAX_COST_PER_REQUEST,
    DEFAULT_CLAUDE_MAX_COST_PER_USER,
    DEFAULT_CLAUDE_MAX_TURNS,
    DEFAULT_CLAUDE_TIMEOUT_SECONDS,
    DEFAULT_DATABASE_URL,
    DEFAULT_MAX_SESSIONS_PER_USER,
    DEFAULT_PROJECT_THREADS_SYNC_ACTION_INTERVAL_SECONDS,
    DEFAULT_RATE_LIMIT_BURST,
    DEFAULT_RATE_LIMIT_REQUESTS,
    DEFAULT_RATE_LIMIT_WINDOW,
    DEFAULT_RETRY_BACKOFF_FACTOR,
    DEFAULT_RETRY_BASE_DELAY,
    DEFAULT_RETRY_MAX_ATTEMPTS,
    DEFAULT_RETRY_MAX_DELAY,
    DEFAULT_SESSION_TIMEOUT_HOURS,
)


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Bot settings
    telegram_bot_token: SecretStr = Field(
        ..., description="Telegram bot token from BotFather"
    )
    telegram_bot_username: str = Field(..., description="Bot username without @")

    # Security
    approved_directory: Path = Field(..., description="Base directory for projects")
    allowed_users: Optional[List[int]] = Field(
        None, description="Allowed Telegram user IDs"
    )
    enable_token_auth: bool = Field(
        False, description="Enable token-based authentication"
    )
    auth_token_secret: Optional[SecretStr] = Field(
        None, description="Secret for auth tokens"
    )

    # Security relaxation (for trusted environments)
    disable_security_patterns: bool = Field(
        False,
        description=(
            "Disable dangerous pattern validation (pipes, redirections, etc.)"
        ),
    )
    disable_tool_validation: bool = Field(
        False,
        description="Allow all Claude tools by bypassing tool validation checks",
    )
    interactive_tool_approval: bool = Field(
        False,
        description=(
            "Require interactive Telegram approval before executing risky tool calls"
        ),
    )
    interactive_tool_approval_tools: Optional[List[str]] = Field(
        default=["Bash", "Write", "Edit"],
        description="Tool names that require interactive approval when enabled",
    )
    interactive_tool_approval_timeout_seconds: int = Field(
        60,
        description="Seconds to wait for user approval before applying the timeout action",
    )
    interactive_tool_approval_timeout_action: Literal["deny", "allow"] = Field(
        "deny",
        description=(
            "Decision applied automatically when the user doesn't respond "
            "within the timeout: 'deny' (fail closed) or 'allow'"
        ),
    )

    # Claude settings
    claude_binary_path: Optional[str] = Field(
        None, description="Path to Claude CLI binary (deprecated)"
    )
    claude_cli_path: Optional[str] = Field(
        None, description="Path to Claude CLI executable"
    )
    anthropic_api_key: Optional[SecretStr] = Field(
        None,
        description="Anthropic API key for SDK (optional if CLI logged in)",
    )
    claude_model: Optional[str] = Field(
        None, description="Claude model to use (defaults to CLI default if unset)"
    )
    claude_max_turns: int = Field(
        DEFAULT_CLAUDE_MAX_TURNS, description="Max conversation turns"
    )
    claude_timeout_seconds: int = Field(
        DEFAULT_CLAUDE_TIMEOUT_SECONDS, description="Claude timeout"
    )
    claude_max_cost_per_user: float = Field(
        DEFAULT_CLAUDE_MAX_COST_PER_USER, description="Max cost per user"
    )
    claude_max_cost_per_request: float = Field(
        DEFAULT_CLAUDE_MAX_COST_PER_REQUEST,
        description="Max cost per individual request (SDK budget cap)",
    )
    # NOTE: When changing this list, also update docs/tools.md,
    # docs/configuration.md, .env.example,
    # src/claude/facade.py (_get_admin_instructions),
    # and src/bot/orchestrator.py (_TOOL_ICONS).
    claude_allowed_tools: Optional[List[str]] = Field(
        default=[
            "Read",
            "Write",
            "Edit",
            "Bash",
            "Glob",
            "Grep",
            "LS",
            "Task",
            "TaskOutput",
            "MultiEdit",
            "NotebookRead",
            "NotebookEdit",
            "WebFetch",
            "TodoRead",
            "TodoWrite",
            "WebSearch",
            "Skill",
        ],
        description="List of allowed Claude tools",
    )
    claude_disallowed_tools: Optional[List[str]] = Field(
        default=[],
        description="List of explicitly disallowed Claude tools/commands",
    )

    # Retry settings
    claude_retry_max_attempts: int = Field(
        DEFAULT_RETRY_MAX_ATTEMPTS,
        ge=0,
        description="Max retry attempts for transient SDK errors (0 = disabled)",
    )
    claude_retry_base_delay: float = Field(
        DEFAULT_RETRY_BASE_DELAY,
        ge=0,
        description=(
            "Base delay in seconds between retries. "
            "0 means retries are attempted immediately with no pause."
        ),
    )
    claude_retry_backoff_factor: float = Field(
        DEFAULT_RETRY_BACKOFF_FACTOR,
        gt=0,
        description="Exponential backoff multiplier",
    )
    claude_retry_max_delay: float = Field(
        DEFAULT_RETRY_MAX_DELAY,
        ge=0,
        description=(
            "Maximum delay cap in seconds. "
            "0 disables the cap entirely (delays grow unbounded with backoff)."
        ),
    )

    # Sandbox settings
    sandbox_enabled: bool = Field(
        True,
        description="Enable OS-level bash sandboxing for approved dir",
    )
    sandbox_excluded_commands: Optional[List[str]] = Field(
        default=["git", "npm", "pip", "poetry", "make", "docker"],
        description="Commands that run outside the sandbox (need system access)",
    )

    # Rate limiting
    rate_limit_requests: int = Field(
        DEFAULT_RATE_LIMIT_REQUESTS, description="Requests per window"
    )
    rate_limit_window: int = Field(
        DEFAULT_RATE_LIMIT_WINDOW, description="Rate limit window seconds"
    )
    rate_limit_burst: int = Field(
        DEFAULT_RATE_LIMIT_BURST, description="Burst capacity"
    )

    # Storage
    database_url: str = Field(
        DEFAULT_DATABASE_URL, description="Database connection URL"
    )
    session_timeout_hours: int = Field(
        DEFAULT_SESSION_TIMEOUT_HOURS, description="Session timeout"
    )
    session_timeout_minutes: int = Field(
        default=120,
        description="Session timeout in minutes",
        ge=10,
        le=1440,  # Max 24 hours
    )
    max_sessions_per_user: int = Field(
        DEFAULT_MAX_SESSIONS_PER_USER, description="Max concurrent sessions"
    )
    session_daily_reset_hour: Optional[int] = Field(
        default=None,
        description="Hour of day (0-23) to force session reset. None = disabled.",
        ge=0,
        le=23,
    )
    session_daily_reset_timezone: str = Field(
        default="UTC",
        description="Timezone for daily reset hour (e.g. 'Europe/Lisbon')",
    )

    # Features
    enable_mcp: bool = Field(False, description="Enable Model Context Protocol")
    mcp_config_path: Optional[Path] = Field(
        None, description="MCP configuration file path"
    )
    enable_git_integration: bool = Field(True, description="Enable git commands")
    enable_file_uploads: bool = Field(True, description="Enable file upload handling")
    enable_voice_messages: bool = Field(
        True, description="Enable voice message transcription"
    )
    voice_provider: Literal["mistral", "openai", "local"] = Field(
        "mistral",
        description="Voice transcription provider: 'mistral', 'openai', or 'local'",
    )
    mistral_api_key: Optional[SecretStr] = Field(
        None, description="Mistral API key for voice transcription"
    )
    openai_api_key: Optional[SecretStr] = Field(
        None, description="OpenAI API key for Whisper voice transcription"
    )
    voice_transcription_model: Optional[str] = Field(
        None,
        description=(
            "Model for voice transcription. "
            "Defaults to 'voxtral-mini-latest' (Mistral) or 'whisper-1' (OpenAI)"
        ),
    )
    voice_max_file_size_mb: int = Field(
        20,
        description=(
            "Maximum Telegram voice message size (MB) that will be downloaded "
            "for transcription"
        ),
        ge=1,
        le=200,
    )
    whisper_cpp_binary_path: Optional[str] = Field(
        None,
        description=(
            "Path to whisper.cpp binary. "
            "Required when VOICE_PROVIDER=local. Auto-detected from PATH if unset."
        ),
    )
    whisper_cpp_model_path: Optional[str] = Field(
        None,
        description=(
            "Path to whisper.cpp GGML model file, or model name "
            "(e.g. 'base', 'small'). Defaults to 'base'. "
            "Named models resolve to ~/.cache/whisper-cpp/ggml-{name}.bin"
        ),
    )
    enable_quick_actions: bool = Field(True, description="Enable quick action buttons")
    agentic_mode: bool = Field(
        True,
        description="Conversational agentic mode (default) vs classic command mode",
    )

    # Reply quoting
    reply_quote: bool = Field(
        True,
        description=(
            "Quote the original user message when replying. "
            "Set to false for cleaner thread-based conversations."
        ),
    )

    # Output verbosity (0=quiet, 1=normal, 2=detailed)
    verbose_level: int = Field(
        1,
        description=(
            "Bot output verbosity: 0=quiet (final response only), "
            "1=normal (tool names + reasoning), "
            "2=detailed (tool inputs + longer reasoning)"
        ),
        ge=0,
        le=2,
    )

    # Streaming drafts (Telegram sendMessageDraft)
    enable_stream_drafts: bool = Field(
        False,
        description="Stream partial responses via sendMessageDraft (private chats only)",
    )
    stream_draft_interval: float = Field(
        0.3,
        description="Minimum seconds between draft updates (0.1-5.0)",
        ge=0.1,
        le=5.0,
    )

    # Monitoring
    log_level: str = Field("INFO", description="Logging level")
    enable_telemetry: bool = Field(False, description="Enable anonymous telemetry")
    sentry_dsn: Optional[str] = Field(None, description="Sentry DSN for error tracking")

    # Development
    debug: bool = Field(False, description="Enable debug mode")
    development_mode: bool = Field(False, description="Enable development features")

    # Webhook settings (optional)
    webhook_url: Optional[str] = Field(None, description="Webhook URL for bot")
    webhook_port: int = Field(8443, description="Webhook port")
    webhook_path: str = Field("/webhook", description="Webhook path")

    # Agentic platform settings
    enable_api_server: bool = Field(False, description="Enable FastAPI webhook server")
    api_server_port: int = Field(8080, description="Webhook API server port")
    enable_scheduler: bool = Field(False, description="Enable job scheduler")
    github_webhook_secret: Optional[str] = Field(
        None, description="GitHub webhook HMAC secret"
    )
    webhook_api_secret: Optional[str] = Field(
        None, description="Shared secret for generic webhook providers"
    )
    notification_chat_ids: Optional[List[int]] = Field(
        None, description="Default Telegram chat IDs for proactive notifications"
    )
    enable_project_threads: bool = Field(
        False,
        description="Enable strict routing by Telegram forum project threads",
    )
    project_threads_mode: Literal["private", "group"] = Field(
        "private",
        description="Project thread mode: private chat topics or group forum topics",
    )
    project_threads_chat_id: Optional[int] = Field(
        None, description="Telegram forum chat ID where project topics are managed"
    )
    projects_config_path: Optional[Path] = Field(
        None, description="Path to YAML project registry for thread mode"
    )
    project_threads_sync_action_interval_seconds: float = Field(
        DEFAULT_PROJECT_THREADS_SYNC_ACTION_INTERVAL_SECONDS,
        description=(
            "Minimum delay between Telegram API calls during project topic sync"
        ),
        ge=0.0,
    )

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", case_sensitive=False, extra="ignore"
    )

    @field_validator("allowed_users", "notification_chat_ids", mode="before")
    @classmethod
    def parse_int_list(cls, v: Any) -> Optional[List[int]]:
        """Parse comma-separated integer lists."""
        if v is None:
            return None
        if isinstance(v, int):
            return [v]
        if isinstance(v, str):
            return [int(uid.strip()) for uid in v.split(",") if uid.strip()]
        if isinstance(v, list):
            return [int(uid) for uid in v]
        return v  # type: ignore[no-any-return]

    @field_validator(
        "claude_allowed_tools", "interactive_tool_approval_tools", mode="before"
    )
    @classmethod
    def parse_claude_allowed_tools(cls, v: Any) -> Optional[List[str]]:
        """Parse comma-separated tool names."""
        if v is None:
            return None
        if isinstance(v, str):
            return [tool.strip() for tool in v.split(",") if tool.strip()]
        if isinstance(v, list):
            return [str(tool) for tool in v]
        return v  # type: ignore[no-any-return]

    @field_validator("approved_directory")
    @classmethod
    def validate_approved_directory(cls, v: Any) -> Path:
        """Ensure approved directory exists and is absolute."""
        if isinstance(v, str):
            v = Path(v)

        path = v.resolve()
        if not path.exists():
            raise ValueError(f"Approved directory does not exist: {path}")
        if not path.is_dir():
            raise ValueError(f"Approved directory is not a directory: {path}")
        return path  # type: ignore[no-any-return]

    @field_validator("mcp_config_path", mode="before")
    @classmethod
    def validate_mcp_config(cls, v: Any, info: Any) -> Optional[Path]:
        """Validate MCP configuration path if MCP is enabled."""
        if not v:
            return v  # type: ignore[no-any-return]
        if isinstance(v, str):
            v = Path(v)
        if not v.exists():
            raise ValueError(f"MCP config file does not exist: {v}")
        # Validate that the file contains valid JSON with mcpServers
        try:
            with open(v) as f:
                config_data = json.load(f)
        except json.JSONDecodeError as e:
            raise ValueError(f"MCP config file is not valid JSON: {e}")
        if not isinstance(config_data, dict):
            raise ValueError("MCP config file must contain a JSON object")
        if "mcpServers" not in config_data:
            raise ValueError(
                "MCP config file must contain a 'mcpServers' key. "
                'Format: {"mcpServers": {"name": {"command": ...}}}'
            )
        if not isinstance(config_data["mcpServers"], dict):
            raise ValueError(
                "'mcpServers' must be an object mapping server names to configurations"
            )
        if not config_data["mcpServers"]:
            raise ValueError(
                "'mcpServers' must contain at least one server configuration"
            )
        return v  # type: ignore[no-any-return]

    @field_validator("projects_config_path", mode="before")
    @classmethod
    def validate_projects_config_path(cls, v: Any) -> Optional[Path]:
        """Validate projects config path if provided."""
        if not v:
            return None
        if isinstance(v, str):
            value = v.strip()
            if not value:
                return None
            v = Path(value)
        if not v.exists():
            raise ValueError(f"Projects config file does not exist: {v}")
        if not v.is_file():
            raise ValueError(f"Projects config path is not a file: {v}")
        return v  # type: ignore[no-any-return]

    @field_validator("project_threads_mode", mode="before")
    @classmethod
    def validate_project_threads_mode(cls, v: Any) -> str:
        """Validate project thread mode."""
        if v is None:
            return "private"
        mode = str(v).strip().lower()
        if mode not in {"private", "group"}:
            raise ValueError("project_threads_mode must be one of ['private', 'group']")
        return mode

    @field_validator("voice_provider", mode="before")
    @classmethod
    def validate_voice_provider(cls, v: Any) -> str:
        """Validate and normalize voice transcription provider."""
        if v is None:
            return "mistral"
        provider = str(v).strip().lower()
        if provider not in {"mistral", "openai", "local"}:
            raise ValueError(
                "voice_provider must be one of ['mistral', 'openai', 'local']"
            )
        return provider

    @field_validator("project_threads_chat_id", mode="before")
    @classmethod
    def validate_project_threads_chat_id(cls, v: Any) -> Optional[int]:
        """Allow empty chat ID for private mode by treating blank values as None."""
        if v is None:
            return None
        if isinstance(v, str):
            value = v.strip()
            if not value:
                return None
            return int(value)
        if isinstance(v, int):
            return v
        return v  # type: ignore[no-any-return]

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: Any) -> str:
        """Validate log level."""
        valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        if v.upper() not in valid_levels:
            raise ValueError(f"log_level must be one of {valid_levels}")
        return v.upper()  # type: ignore[no-any-return]

    @model_validator(mode="after")
    def validate_cross_field_dependencies(self) -> "Settings":
        """Validate dependencies between fields."""
        # Check auth token requirements
        if self.enable_token_auth and not self.auth_token_secret:
            raise ValueError(
                "auth_token_secret required when enable_token_auth is True"
            )

        # Check MCP requirements
        if self.enable_mcp and not self.mcp_config_path:
            raise ValueError("mcp_config_path required when enable_mcp is True")

        if self.enable_project_threads:
            if (
                self.project_threads_mode == "group"
                and self.project_threads_chat_id is None
            ):
                raise ValueError(
                    "project_threads_chat_id required when "
                    "project_threads_mode is 'group'"
                )
            if not self.projects_config_path:
                raise ValueError(
                    "projects_config_path required when enable_project_threads is True"
                )

        return self

    @property
    def is_production(self) -> bool:
        """Check if running in production mode."""
        return not (self.debug or self.development_mode)

    @property
    def database_path(self) -> Optional[Path]:
        """Extract path from SQLite database URL."""
        if self.database_url.startswith("sqlite:///"):
            db_path = self.database_url.replace("sqlite:///", "")
            return Path(db_path).resolve()
        return None

    @property
    def telegram_token_str(self) -> str:
        """Get Telegram token as string."""
        return self.telegram_bot_token.get_secret_value()

    @property
    def auth_secret_str(self) -> Optional[str]:
        """Get auth token secret as string."""
        if self.auth_token_secret:
            return self.auth_token_secret.get_secret_value()
        return None

    @property
    def anthropic_api_key_str(self) -> Optional[str]:
        """Get Anthropic API key as string."""
        return (
            self.anthropic_api_key.get_secret_value()
            if self.anthropic_api_key
            else None
        )

    @property
    def mistral_api_key_str(self) -> Optional[str]:
        """Get Mistral API key as string."""
        return self.mistral_api_key.get_secret_value() if self.mistral_api_key else None

    @property
    def openai_api_key_str(self) -> Optional[str]:
        """Get OpenAI API key as string."""
        return self.openai_api_key.get_secret_value() if self.openai_api_key else None

    @property
    def resolved_voice_model(self) -> str:
        """Get the voice transcription model, with provider-specific defaults."""
        if self.voice_transcription_model:
            return self.voice_transcription_model
        if self.voice_provider == "openai":
            return "whisper-1"
        if self.voice_provider == "local":
            return self.whisper_cpp_model_path or "base"
        return "voxtral-mini-latest"

    @property
    def voice_max_file_size_bytes(self) -> int:
        """Maximum allowed voice message size in bytes."""
        return self.voice_max_file_size_mb * 1024 * 1024

    @property
    def voice_provider_api_key_env(self) -> str:
        """API key environment variable required for the configured voice provider."""
        if self.voice_provider == "openai":
            return "OPENAI_API_KEY"
        if self.voice_provider == "local":
            return ""
        return "MISTRAL_API_KEY"

    @property
    def voice_provider_display_name(self) -> str:
        """Human-friendly label for the configured voice provider."""
        if self.voice_provider == "openai":
            return "OpenAI Whisper"
        if self.voice_provider == "local":
            return "Local whisper.cpp"
        return "Mistral Voxtral"

    @property
    def resolved_whisper_cpp_binary(self) -> str:
        """Resolve whisper.cpp binary path, defaulting to 'whisper-cpp' on PATH."""
        return self.whisper_cpp_binary_path or "whisper-cpp"

    @property
    def resolved_whisper_cpp_model_path(self) -> str:
        """Resolve whisper.cpp model file path from name or explicit path."""
        path_or_name = self.whisper_cpp_model_path or "base"
        if "/" in path_or_name or path_or_name.endswith(".bin"):
            return path_or_name
        return str(Path.home() / ".cache" / "whisper-cpp" / f"ggml-{path_or_name}.bin")
