"""Discover Claude Code skills from project, user, and plugin locations.

Scans SKILL.md frontmatter for name, description, and argument-hint.
Returns a dict of skill name -> DiscoveredSkill. Project-agnostic -- works
with any Claude Code project, following the standard on-disk skill layout:

  {project_dir}/.claude/skills/<skill>/SKILL.md                        (project)
  ~/.claude/skills/<skill>/SKILL.md                                    (user)
  ~/.claude/plugins/marketplaces/<m>/plugins/<p>/skills/<s>/SKILL.md   (plugin)
  ~/.claude/plugins/marketplaces/<m>/external_plugins/<p>/skills/<s>/SKILL.md

Precedence: project > user > plugin. On collision the higher-precedence
entry wins, and the shadowed one is logged at debug.
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Optional, Tuple

import structlog
import yaml

logger = structlog.get_logger(__name__)

# Commands that must not be overridden by skills
_BUILTIN_COMMANDS = frozenset({
    "start", "new", "status", "verbose", "repo", "tts",
    "help", "sync_threads", "restart",
})


@dataclass
class DiscoveredSkill:
    name: str                   # Telegram-safe form (lowercase, [a-z0-9_])
    description: str
    argument_hint: Optional[str] = None
    original_name: str = ""     # raw `name:` from frontmatter (may contain dashes)
    source: str = "project"     # "project" | "user" | "plugin"


def _normalize(raw_name: str) -> str:
    return raw_name.strip().lower().replace(" ", "_").replace("-", "_")


def _iter_skill_files(project_dir: Path) -> Iterable[Tuple[Path, str]]:
    """Yield (SKILL.md path, source) pairs in precedence order."""
    project_dir = Path(project_dir)
    # Project skills: <project>/.claude/skills/<skill>/SKILL.md
    project_skills = project_dir / ".claude" / "skills"
    if project_skills.is_dir():
        for p in project_skills.glob("*/SKILL.md"):
            yield p, "project"

    # User skills: ~/.claude/skills/<skill>/SKILL.md
    user_skills = Path.home() / ".claude" / "skills"
    if user_skills.is_dir():
        for p in user_skills.glob("*/SKILL.md"):
            yield p, "user"

    # Plugin skills: ~/.claude/plugins/marketplaces/<m>/{plugins,external_plugins}/<p>/skills/<s>/SKILL.md
    marketplaces = Path.home() / ".claude" / "plugins" / "marketplaces"
    if marketplaces.is_dir():
        for marketplace in marketplaces.iterdir():
            if not marketplace.is_dir():
                continue
            for plugin_root in ("plugins", "external_plugins"):
                root = marketplace / plugin_root
                if not root.is_dir():
                    continue
                for p in root.glob("*/skills/*/SKILL.md"):
                    yield p, "plugin"


def discover_skills(project_dir: Path) -> Dict[str, DiscoveredSkill]:
    """Scan standard Claude Code skill locations and return a name -> skill map.

    Returns a dict mapping the Telegram-safe command name (lowercase, no /,
    dashes replaced with underscores) to a DiscoveredSkill. The original
    dashed name is preserved in `original_name` so callers can rewrite the
    command text back to the form Claude Code expects.

    Skips:
      - files without valid YAML frontmatter or without a `name:` field
      - names that clash with built-in bot commands
      - skills with `user-invokable: false` in frontmatter
      - lower-precedence entries when a higher-precedence skill has the same name
    """
    discovered: Dict[str, DiscoveredSkill] = {}

    for skill_md, source in _iter_skill_files(project_dir):
        try:
            text = skill_md.read_text(encoding="utf-8")

            match = re.match(r"^---\n(.*?)\n---", text, re.DOTALL)
            if not match:
                continue

            meta = yaml.safe_load(match.group(1))
            if not isinstance(meta, dict) or "name" not in meta:
                continue

            if meta.get("user-invokable") is False:
                logger.debug("Skipping skill (user-invokable: false)", path=str(skill_md))
                continue

            raw_name = str(meta["name"]).strip()
            cmd_name = _normalize(raw_name)

            if cmd_name in _BUILTIN_COMMANDS:
                logger.debug("Skipping skill (conflicts with built-in)", skill=cmd_name)
                continue

            if cmd_name in discovered:
                logger.debug(
                    "Skipping shadowed skill",
                    skill=cmd_name,
                    shadowed_by=discovered[cmd_name].source,
                    shadowed_source=source,
                    path=str(skill_md),
                )
                continue

            description = str(meta.get("description", "")).strip()
            if not description:
                description = f"Run /{cmd_name} skill"

            discovered[cmd_name] = DiscoveredSkill(
                name=cmd_name,
                description=description[:256],
                argument_hint=meta.get("argument-hint"),
                original_name=raw_name,
                source=source,
            )
        except Exception as e:
            logger.warning(
                "Failed to parse skill frontmatter",
                path=str(skill_md),
                error=str(e),
            )

    if discovered:
        by_source: Dict[str, int] = {}
        for s in discovered.values():
            by_source[s.source] = by_source.get(s.source, 0) + 1
        logger.info(
            "Skills discovered",
            count=len(discovered),
            by_source=by_source,
            names=sorted(discovered.keys()),
        )

    return discovered


def rewrite_skill_command(text: str, skills: Dict[str, DiscoveredSkill]) -> str:
    """Rewrite a leading /<normalized> to /<original_name> for discovered skills.

    Telegram's Bot API only permits `[a-z0-9_]` in command names, so skill
    names containing dashes are normalized for the menu (e.g. git-activity
    -> git_activity). Claude Code's skill dispatcher matches the raw name,
    so we undo the substitution before forwarding to it. Leaves non-command
    text, unknown commands, and already-original-form commands untouched.
    """
    if not text.startswith("/"):
        return text
    head, sep, rest = text.partition(" ")
    cmd = head[1:].lower()
    skill = skills.get(cmd)
    if skill and skill.original_name and skill.original_name != cmd:
        return f"/{skill.original_name}" + (f"{sep}{rest}" if sep else "")
    return text
