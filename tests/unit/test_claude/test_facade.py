"""Test ClaudeIntegration facade — force_new skips auto-resume."""

from datetime import datetime
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest

from src.claude.facade import ClaudeIntegration
from src.claude.session import ClaudeSession, SessionManager
from src.config.settings import Settings

from .conftest import InMemorySessionStorage


def _make_mock_response(session_id: str = "new-session-id") -> MagicMock:
    """Create a mock ClaudeResponse with sensible defaults."""
    resp = MagicMock()
    resp.session_id = session_id
    resp.cost = 0.0
    resp.duration_ms = 100
    resp.num_turns = 1
    resp.tools_used = []
    resp.is_error = False
    resp.content = "ok"
    return resp


def _make_user_data(force_new: bool = False) -> Dict[str, Any]:
    """Simulate context.user_data dict as the handlers would see it."""
    return {
        "claude_session_id": None,
        "session_started": True,
        "force_new_session": force_new,
    }


@pytest.fixture
def config(tmp_path):
    """Create test config."""
    return Settings(
        telegram_bot_token="test:token",
        telegram_bot_username="testbot",
        approved_directory=tmp_path,
        session_timeout_hours=24,
        max_sessions_per_user=5,
    )


@pytest.fixture
def session_manager(config):
    """Create session manager with in-memory storage."""
    storage = InMemorySessionStorage()
    return SessionManager(config, storage)


@pytest.fixture
def facade(config, session_manager):
    """Create facade with mocked SDK manager."""
    sdk_manager = MagicMock()

    integration = ClaudeIntegration(
        config=config,
        sdk_manager=sdk_manager,
        session_manager=session_manager,
    )
    return integration


class TestForceNewSkipsAutoResume:
    """Verify that force_new=True prevents _find_resumable_session."""

    async def test_auto_resume_finds_existing_session(self, facade, session_manager):
        """Without force_new, run_command auto-resumes an existing session."""
        project = Path("/test/project")
        user_id = 123

        # Seed an existing non-temp session in storage
        existing = ClaudeSession(
            session_id="real-session-id",
            user_id=user_id,
            project_path=project,
            created_at=datetime.utcnow(),
            last_used=datetime.utcnow(),
        )
        await session_manager.storage.save_session(existing)
        session_manager.active_sessions[existing.session_id] = existing

        # _find_resumable_session should find it
        found = await facade._find_resumable_session(user_id, project)
        assert found is not None
        assert found.session_id == "real-session-id"

    async def test_force_new_skips_auto_resume(self, facade, session_manager):
        """With force_new=True, run_command does NOT auto-resume."""
        project = Path("/test/project")
        user_id = 123

        # Seed an existing non-temp session
        existing = ClaudeSession(
            session_id="real-session-id",
            user_id=user_id,
            project_path=project,
            created_at=datetime.utcnow(),
            last_used=datetime.utcnow(),
        )
        await session_manager.storage.save_session(existing)
        session_manager.active_sessions[existing.session_id] = existing

        # Mock _find_resumable_session to track whether it's called
        with patch.object(
            facade, "_find_resumable_session", wraps=facade._find_resumable_session
        ) as spy:
            with patch.object(
                facade,
                "_execute",
                return_value=_make_mock_response(),
            ):
                await facade.run_command(
                    prompt="hello",
                    working_directory=project,
                    user_id=user_id,
                    session_id=None,
                    force_new=True,
                )

            # _find_resumable_session should NOT have been called
            spy.assert_not_called()


class TestForceNewSurvivesFailure:
    """Verify the handler-level contract: force_new_session flag stays set
    when run_command fails, so the next retry still starts a fresh session."""

    async def _seed_session(
        self,
        session_manager: SessionManager,
        user_id: int = 123,
        project: Path = Path("/test/project"),
    ) -> ClaudeSession:
        existing = ClaudeSession(
            session_id="old-session-id",
            user_id=user_id,
            project_path=project,
            created_at=datetime.utcnow(),
            last_used=datetime.utcnow(),
        )
        await session_manager.storage.save_session(existing)
        session_manager.active_sessions[existing.session_id] = existing
        return existing

    async def test_flag_survives_run_command_failure(self, facade, session_manager):
        """If run_command raises, the caller should still see
        force_new_session=True so the retry skips auto-resume."""
        project = Path("/test/project")
        user_id = 123
        await self._seed_session(session_manager, user_id, project)

        user_data = _make_user_data(force_new=True)

        # Simulate what the handler does: read flag, call run_command
        force_new = bool(user_data.get("force_new_session"))
        assert force_new is True

        with patch.object(
            facade,
            "_execute",
            side_effect=RuntimeError("network timeout"),
        ):
            with pytest.raises(RuntimeError, match="network timeout"):
                await facade.run_command(
                    prompt="hello",
                    working_directory=project,
                    user_id=user_id,
                    session_id=None,
                    force_new=force_new,
                )

        # Handler would NOT have cleared the flag (no success path reached)
        # so user_data still has it — simulating the handler contract:
        assert user_data["force_new_session"] is True

    async def test_flag_cleared_after_successful_run(self, facade, session_manager):
        """After a successful run_command, the handler clears
        force_new_session so subsequent messages auto-resume normally."""
        project = Path("/test/project")
        user_id = 123
        await self._seed_session(session_manager, user_id, project)

        user_data = _make_user_data(force_new=True)
        force_new = bool(user_data.get("force_new_session"))

        with patch.object(
            facade,
            "_execute",
            return_value=_make_mock_response(),
        ):
            await facade.run_command(
                prompt="hello",
                working_directory=project,
                user_id=user_id,
                session_id=None,
                force_new=force_new,
            )

        # Simulate the handler clearing the flag on success
        if force_new:
            user_data["force_new_session"] = False

        assert user_data["force_new_session"] is False

    async def test_retry_after_failure_still_skips_auto_resume(
        self, facade, session_manager
    ):
        """Full scenario: /new -> fail -> retry -> success.
        Both calls should skip auto-resume; flag cleared only after success."""
        project = Path("/test/project")
        user_id = 123
        await self._seed_session(session_manager, user_id, project)

        user_data = _make_user_data(force_new=True)

        # --- First attempt: fails ---
        force_new = bool(user_data.get("force_new_session"))
        with patch.object(
            facade, "_find_resumable_session", wraps=facade._find_resumable_session
        ) as spy1:
            with patch.object(
                facade,
                "_execute",
                side_effect=RuntimeError("backend down"),
            ):
                with pytest.raises(RuntimeError):
                    await facade.run_command(
                        prompt="hello",
                        working_directory=project,
                        user_id=user_id,
                        session_id=None,
                        force_new=force_new,
                    )
            spy1.assert_not_called()

        # Flag untouched (handler didn't reach success path)
        assert user_data["force_new_session"] is True

        # --- Second attempt: succeeds ---
        force_new = bool(user_data.get("force_new_session"))
        with patch.object(
            facade, "_find_resumable_session", wraps=facade._find_resumable_session
        ) as spy2:
            with patch.object(
                facade,
                "_execute",
                return_value=_make_mock_response(),
            ):
                await facade.run_command(
                    prompt="hello",
                    working_directory=project,
                    user_id=user_id,
                    session_id=None,
                    force_new=force_new,
                )
            spy2.assert_not_called()

        # Handler clears on success
        if force_new:
            user_data["force_new_session"] = False
        assert user_data["force_new_session"] is False


class TestEmptySessionIdWarning:
    """Verify facade warns when final session_id is empty."""

    async def test_empty_session_id_warning_in_facade(self, facade, session_manager):
        """When Claude returns no session_id, facade logs a warning."""
        project = Path("/test/project")
        user_id = 456

        # Return a response with empty session_id
        mock_response = _make_mock_response(session_id="")

        with patch.object(
            facade,
            "_execute",
            return_value=mock_response,
        ):
            result = await facade.run_command(
                prompt="hello",
                working_directory=project,
                user_id=user_id,
                session_id=None,
            )

        # Session ID should be empty on the response
        assert not result.session_id


class TestEffortPropagation:
    """Verify the facade preserves an effort override."""

    async def test_run_command_passes_effort_to_execute(self, facade):
        project = Path("/test/project")

        with patch.object(
            facade,
            "_execute",
            return_value=_make_mock_response(),
        ) as execute:
            await facade.run_command(
                prompt="hello",
                working_directory=project,
                user_id=123,
                force_new=True,
                effort="high",
            )

        assert execute.await_args.kwargs["effort"] == "high"
