"""Unit tests for skill_discovery: multi-path scan + command rewrite."""

from pathlib import Path

import pytest

from src.bot.features.skill_discovery import (
    DiscoveredSkill,
    discover_skills,
    rewrite_skill_command,
)


def _write_skill(
    root: Path,
    name: str,
    description: str = "Test skill",
    extra_frontmatter: str = "",
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "SKILL.md").write_text(
        "---\n"
        f"name: {name}\n"
        f"description: {description}\n"
        f"{extra_frontmatter}"
        "---\n\nbody\n"
    )


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """Point Path.home() at a tmp dir so tests don't touch the real ~/.claude/."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    # Path.home() on POSIX honors $HOME; monkeypatching HOME is enough.
    return home


@pytest.fixture
def fake_project(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    return project


class TestDiscoverSkills:
    def test_discovers_project_skills(self, fake_project, fake_home):
        _write_skill(
            fake_project / ".claude/skills/foo",
            name="foo",
            description="Foo skill",
        )
        result = discover_skills(fake_project)
        assert "foo" in result
        assert result["foo"].original_name == "foo"
        assert result["foo"].source == "project"
        assert result["foo"].description == "Foo skill"

    def test_dashed_name_normalized_original_preserved(self, fake_project, fake_home):
        _write_skill(
            fake_project / ".claude/skills/git-activity",
            name="git-activity",
            description="Git activity",
        )
        result = discover_skills(fake_project)
        assert "git_activity" in result
        assert "git-activity" not in result
        assert result["git_activity"].original_name == "git-activity"
        assert result["git_activity"].name == "git_activity"

    def test_discovers_user_level_skills(self, fake_project, fake_home):
        _write_skill(
            fake_home / ".claude/skills/ubar",
            name="ubar",
            description="User skill",
        )
        result = discover_skills(fake_project)
        assert "ubar" in result
        assert result["ubar"].source == "user"

    def test_discovers_plugin_skills(self, fake_project, fake_home):
        _write_skill(
            fake_home / ".claude/plugins/marketplaces/mp1/plugins/p1/skills/plug",
            name="plug",
            description="Plugin skill",
        )
        result = discover_skills(fake_project)
        assert "plug" in result
        assert result["plug"].source == "plugin"

    def test_discovers_external_plugin_skills(self, fake_project, fake_home):
        _write_skill(
            fake_home / ".claude/plugins/marketplaces/mp1/external_plugins/ep1/skills/ext",
            name="ext",
            description="External plugin skill",
        )
        result = discover_skills(fake_project)
        assert "ext" in result
        assert result["ext"].source == "plugin"

    def test_project_shadows_plugin_on_collision(self, fake_project, fake_home):
        _write_skill(
            fake_home / ".claude/plugins/marketplaces/mp/plugins/p/skills/shared",
            name="shared",
            description="Plugin version",
        )
        _write_skill(
            fake_project / ".claude/skills/shared",
            name="shared",
            description="Project version",
        )
        result = discover_skills(fake_project)
        assert result["shared"].source == "project"
        assert result["shared"].description == "Project version"

    def test_user_shadows_plugin_on_collision(self, fake_project, fake_home):
        _write_skill(
            fake_home / ".claude/plugins/marketplaces/mp/plugins/p/skills/shared",
            name="shared",
            description="Plugin version",
        )
        _write_skill(
            fake_home / ".claude/skills/shared",
            name="shared",
            description="User version",
        )
        result = discover_skills(fake_project)
        assert result["shared"].source == "user"
        assert result["shared"].description == "User version"

    def test_skips_builtin_command_conflicts(self, fake_project, fake_home):
        _write_skill(
            fake_project / ".claude/skills/status",
            name="status",
            description="Tries to override built-in",
        )
        result = discover_skills(fake_project)
        assert "status" not in result

    def test_skips_user_invokable_false(self, fake_project, fake_home):
        _write_skill(
            fake_project / ".claude/skills/hidden",
            name="hidden",
            description="Not user-callable",
            extra_frontmatter="user-invokable: false\n",
        )
        result = discover_skills(fake_project)
        assert "hidden" not in result

    def test_does_not_filter_disable_model_invocation(self, fake_project, fake_home):
        """disable-model-invocation controls agent routing, not user menu visibility."""
        _write_skill(
            fake_project / ".claude/skills/closeday",
            name="closeday",
            description="Daily synthesis",
            extra_frontmatter="disable-model-invocation: true\n",
        )
        result = discover_skills(fake_project)
        assert "closeday" in result

    def test_skips_files_without_frontmatter(self, fake_project, fake_home):
        bad = fake_project / ".claude/skills/bad"
        bad.mkdir(parents=True)
        (bad / "SKILL.md").write_text("no frontmatter here\n")
        result = discover_skills(fake_project)
        assert "bad" not in result

    def test_missing_project_dir_returns_empty(self, tmp_path, fake_home):
        # No .claude/skills/ at project, no user skills, no plugins
        result = discover_skills(tmp_path / "nonexistent")
        assert result == {}

    def test_description_truncated_to_256(self, fake_project, fake_home):
        long = "x" * 500
        _write_skill(
            fake_project / ".claude/skills/verbose",
            name="verbose_skill",
            description=long,
        )
        result = discover_skills(fake_project)
        assert len(result["verbose_skill"].description) == 256


class TestRewriteSkillCommand:
    def _skills(self) -> dict:
        return {
            "git_activity": DiscoveredSkill(
                name="git_activity",
                description="Git activity",
                original_name="git-activity",
                source="project",
            ),
            "log": DiscoveredSkill(
                name="log",
                description="Live log",
                original_name="log",
                source="project",
            ),
        }

    def test_rewrites_dashed_command(self):
        assert rewrite_skill_command("/git_activity", self._skills()) == "/git-activity"

    def test_preserves_args(self):
        assert (
            rewrite_skill_command("/git_activity today", self._skills())
            == "/git-activity today"
        )

    def test_leaves_non_dashed_unchanged(self):
        assert rewrite_skill_command("/log hello", self._skills()) == "/log hello"

    def test_leaves_unknown_command_unchanged(self):
        assert rewrite_skill_command("/unknown arg", self._skills()) == "/unknown arg"

    def test_leaves_plain_text_unchanged(self):
        assert rewrite_skill_command("hello world", self._skills()) == "hello world"

    def test_empty_string(self):
        assert rewrite_skill_command("", self._skills()) == ""

    def test_handles_bare_slash_without_args(self):
        # "/" alone should not crash
        assert rewrite_skill_command("/", self._skills()) == "/"
