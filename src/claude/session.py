"""Claude Code session management."""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

import structlog

from ..config.settings import Settings
from .sdk_integration import ClaudeResponse

logger = structlog.get_logger()


def _to_utc(dt: datetime) -> datetime:
    """Normalize datetime to timezone-aware UTC.

    Backward compatibility: legacy persisted sessions may contain naive
    timestamps; treat naive values as UTC.
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


@dataclass
class ClaudeSession:
    """Claude Code session state."""

    session_id: str
    user_id: int
    project_path: Path
    created_at: datetime
    last_used: datetime
    total_cost: float = 0.0
    total_turns: int = 0
    message_count: int = 0
    tools_used: List[str] = field(default_factory=list)
    is_new_session: bool = False  # True if session hasn't been sent to Claude Code yet

    def is_expired(
        self,
        timeout_hours: int,
        daily_reset_hour: Optional[int] = None,
        daily_reset_tz: str = "UTC",
    ) -> bool:
        """Check if session has expired.

        Expires if age exceeds timeout_hours OR if the session spans
        the daily reset boundary (e.g. 3:00 AM local time).
        """
        now = datetime.now(UTC)
        age = now - _to_utc(self.last_used)
        if age > timedelta(hours=timeout_hours):
            return True

        if daily_reset_hour is not None:
            tz = ZoneInfo(daily_reset_tz)
            now_local = now.astimezone(tz)
            today_reset = now_local.replace(
                hour=daily_reset_hour, minute=0, second=0, microsecond=0
            )
            if now_local < today_reset:
                today_reset -= timedelta(days=1)
            last_used_local = _to_utc(self.last_used).astimezone(tz)
            if last_used_local < today_reset:
                return True

        return False

    def update_usage(self, response: ClaudeResponse) -> None:
        """Update session with usage from response."""
        self.last_used = _to_utc(datetime.now(UTC))
        self.total_cost += response.cost
        self.total_turns += response.num_turns
        self.message_count += 1

        # Track unique tools
        if response.tools_used:
            for tool in response.tools_used:
                tool_name = tool.get("name")
                if tool_name and tool_name not in self.tools_used:
                    self.tools_used.append(tool_name)

    def to_dict(self) -> Dict:
        """Convert session to dictionary for storage."""
        return {
            "session_id": self.session_id,
            "user_id": self.user_id,
            "project_path": str(self.project_path),
            "created_at": self.created_at.isoformat(),
            "last_used": self.last_used.isoformat(),
            "total_cost": self.total_cost,
            "total_turns": self.total_turns,
            "message_count": self.message_count,
            "tools_used": self.tools_used,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "ClaudeSession":
        """Create session from dictionary."""
        return cls(
            session_id=data["session_id"],
            user_id=data["user_id"],
            project_path=Path(data["project_path"]),
            created_at=_to_utc(datetime.fromisoformat(data["created_at"])),
            last_used=_to_utc(datetime.fromisoformat(data["last_used"])),
            total_cost=data.get("total_cost", 0.0),
            total_turns=data.get("total_turns", 0),
            message_count=data.get("message_count", 0),
            tools_used=data.get("tools_used", []),
        )


class SessionStorage:
    """Abstract base class for session storage."""

    async def save_session(self, session: ClaudeSession) -> None:
        """Save session to storage."""
        raise NotImplementedError

    async def load_session(
        self, session_id: str, user_id: int
    ) -> Optional[ClaudeSession]:
        """Load session owned by user_id from storage."""
        raise NotImplementedError

    async def delete_session(self, session_id: str) -> None:
        """Delete session from storage."""
        raise NotImplementedError

    async def get_user_sessions(self, user_id: int) -> List[ClaudeSession]:
        """Get all sessions for a user."""
        raise NotImplementedError

    async def get_all_sessions(self) -> List[ClaudeSession]:
        """Get all sessions."""
        raise NotImplementedError


class SessionManager:
    """Manage Claude Code sessions."""

    def __init__(self, config: Settings, storage: SessionStorage):
        """Initialize session manager."""
        self.config = config
        self.storage = storage
        self.active_sessions: Dict[str, ClaudeSession] = {}

    def _is_session_expired(self, session: ClaudeSession) -> bool:
        """Check if session is expired using all configured rules."""
        return session.is_expired(
            self.config.session_timeout_hours,
            daily_reset_hour=self.config.session_daily_reset_hour,
            daily_reset_tz=self.config.session_daily_reset_timezone,
        )

    async def get_or_create_session(
        self,
        user_id: int,
        project_path: Path,
        session_id: Optional[str] = None,
    ) -> ClaudeSession:
        """Get existing session or create new one."""
        logger.info(
            "Getting or creating session",
            user_id=user_id,
            project_path=str(project_path),
            session_id=session_id,
        )

        # Check for existing session
        if session_id and session_id in self.active_sessions:
            session = self.active_sessions[session_id]
            if session.user_id != user_id:
                logger.warning(
                    "Session ownership mismatch in active cache",
                    session_id=session_id,
                    session_owner=session.user_id,
                    requesting_user=user_id,
                )
            elif not self._is_session_expired(session):
                logger.debug("Using active session", session_id=session_id)
                return session

        # Try to load from storage (filtered by user_id)
        if session_id:
            session = await self.storage.load_session(session_id, user_id)
            if session and not self._is_session_expired(session):
                self.active_sessions[session_id] = session
                logger.info("Loaded session from storage", session_id=session_id)
                return session

        # Check user session limit
        user_sessions = await self._get_user_sessions(user_id)
        if len(user_sessions) >= self.config.max_sessions_per_user:
            # Remove oldest session
            oldest = min(user_sessions, key=lambda s: s.last_used)
            await self.remove_session(oldest.session_id)
            logger.info(
                "Removed oldest session due to limit",
                removed_session_id=oldest.session_id,
                user_id=user_id,
            )

        # Create session with empty ID — Claude will provide the real one
        new_session = ClaudeSession(
            session_id="",
            user_id=user_id,
            project_path=project_path,
            created_at=datetime.now(UTC),
            last_used=datetime.now(UTC),
            is_new_session=True,
        )

        # Don't save to storage yet — deferred until after Claude responds
        # with a real session_id (via update_session)

        logger.info(
            "Created new session (pending Claude session ID)",
            user_id=user_id,
            project_path=str(project_path),
        )

        return new_session

    async def update_session(
        self, session: ClaudeSession, response: ClaudeResponse
    ) -> None:
        """Update session with response data and persist."""
        if session.is_new_session:
            # Assign the real session ID from Claude
            if response.session_id:
                session.session_id = response.session_id
            else:
                logger.warning(
                    "Claude returned no session_id for new session; "
                    "session will not be resumable",
                    user_id=session.user_id,
                    project_path=str(session.project_path),
                )
            session.is_new_session = False

            logger.info(
                "New session assigned Claude session ID",
                session_id=session.session_id,
            )

        session.update_usage(response)

        # Persist to storage and track as active
        if session.session_id:
            self.active_sessions[session.session_id] = session
            await self.storage.save_session(session)

        logger.debug(
            "Session updated",
            session_id=session.session_id,
            total_cost=session.total_cost,
            message_count=session.message_count,
        )

    async def remove_session(self, session_id: str) -> None:
        """Remove session."""
        if session_id in self.active_sessions:
            del self.active_sessions[session_id]

        await self.storage.delete_session(session_id)
        logger.info("Session removed", session_id=session_id)

    async def cleanup_expired_sessions(self) -> int:
        """Remove expired sessions."""
        logger.info("Starting session cleanup")

        all_sessions = await self.storage.get_all_sessions()
        expired_count = 0

        for session in all_sessions:
            if self._is_session_expired(session):
                await self.remove_session(session.session_id)
                expired_count += 1

        logger.info("Session cleanup completed", expired_sessions=expired_count)
        return expired_count

    async def _get_user_sessions(self, user_id: int) -> List[ClaudeSession]:
        """Get all sessions for a user."""
        return await self.storage.get_user_sessions(user_id)

    async def get_session_info(self, session_id: str, user_id: int) -> Optional[Dict]:
        """Get session info dict for a session owned by user_id."""
        session = self.active_sessions.get(session_id)

        if session and session.user_id != user_id:
            logger.warning(
                "Session ownership mismatch in get_session_info",
                session_id=session_id,
                session_owner=session.user_id,
                requesting_user=user_id,
            )
            session = None

        if not session:
            session = await self.storage.load_session(session_id, user_id)

        if session:
            return {
                "session_id": session.session_id,
                "project": str(session.project_path),
                "created": session.created_at.isoformat(),
                "last_used": session.last_used.isoformat(),
                "cost": session.total_cost,
                "turns": session.total_turns,
                "messages": session.message_count,
                "tools_used": session.tools_used,
                "expired": self._is_session_expired(session),
            }

        return None

    async def get_user_session_summary(self, user_id: int) -> Dict:
        """Get summary of user's sessions."""
        sessions = await self._get_user_sessions(user_id)

        total_cost = sum(s.total_cost for s in sessions)
        total_messages = sum(s.message_count for s in sessions)
        active_sessions = [
            s for s in sessions if not self._is_session_expired(s)
        ]

        return {
            "user_id": user_id,
            "total_sessions": len(sessions),
            "active_sessions": len(active_sessions),
            "total_cost": total_cost,
            "total_messages": total_messages,
            "projects": list(set(str(s.project_path) for s in sessions)),
        }
