"""Tests for the footer that says why a Claude run stopped (#230, #172)."""

from src.bot.utils.formatting import (
    format_permission_denials,
    format_stop_reason,
    with_stop_reason,
)
from src.bot.utils.html_format import markdown_to_telegram_html
from src.claude.sdk_integration import ClaudeResponse


def _response(**kwargs) -> ClaudeResponse:
    defaults = {
        "content": "Some output",
        "session_id": "s1",
        "cost": 0.01,
        "duration_ms": 100,
        "num_turns": 10,
    }
    defaults.update(kwargs)
    return ClaudeResponse(**defaults)


class TestFormatStopReason:
    """The footer appended to Claude's reply."""

    def test_no_footer_for_a_clean_run(self):
        assert format_stop_reason(_response(result_subtype="success")) is None

    def test_no_footer_when_the_cli_reported_no_subtype(self):
        """Older CLI versions omit subtype; that must not read as a failure."""
        assert format_stop_reason(_response()) is None

    def test_turn_limit(self):
        footer = format_stop_reason(
            _response(result_subtype="error_max_turns", terminal_reason="max_turns")
        )

        assert footer is not None
        assert "turn limit reached after 10 turns" in footer
        assert "Send a message to continue" in footer
        assert footer.startswith("\n\n")

    def test_turn_limit_without_a_terminal_reason(self):
        """subtype alone is enough; terminal_reason is extra detail."""
        footer = format_stop_reason(_response(result_subtype="error_max_turns"))

        assert footer is not None
        assert "turn limit reached" in footer

    def test_cost_budget(self):
        """CLAUDE_MAX_COST_PER_REQUEST is passed to the SDK as max_budget_usd."""
        footer = format_stop_reason(_response(result_subtype="error_max_budget_usd"))

        assert footer is not None
        assert "cost budget reached" in footer

    def test_error_during_execution_shows_the_cli_prose(self):
        footer = format_stop_reason(
            _response(
                result_subtype="error_during_execution",
                errors=["Tool ran out of memory"],
            )
        )

        assert footer is not None
        assert "the run hit an error" in footer
        assert "Tool ran out of memory" in footer

    def test_unknown_subtype_names_the_raw_value(self):
        footer = format_stop_reason(_response(result_subtype="error_brand_new"))

        assert footer is not None
        assert "error_brand_new" in footer

    def test_terminal_reason_wins_over_an_unknown_subtype(self):
        footer = format_stop_reason(
            _response(result_subtype="error_something", terminal_reason="api_error")
        )

        assert footer is not None
        assert "the API returned an error" in footer

    def test_a_single_turn_is_singular(self):
        footer = format_stop_reason(
            _response(result_subtype="error_max_turns", num_turns=1)
        )

        assert footer is not None
        assert "after 1 turn." in footer

    def test_no_turn_count_when_none_were_recorded(self):
        footer = format_stop_reason(
            _response(result_subtype="error_max_turns", num_turns=0)
        )

        assert footer is not None
        assert "after" not in footer

    def test_long_error_detail_is_clipped(self):
        footer = format_stop_reason(
            _response(result_subtype="error_during_execution", errors=["x" * 500])
        )

        assert footer is not None
        assert "…" in footer
        assert len(footer) < 400

    def test_denials_are_reported_even_on_a_successful_run(self):
        footer = format_stop_reason(
            _response(
                result_subtype="success",
                permission_denials=[
                    {"tool_name": "Write", "tool_input": {"file_path": "/etc/hosts"}}
                ],
            )
        )

        assert footer is not None
        assert "1 tool call was blocked: Write(`/etc/hosts`)" in footer
        assert "Stopped" not in footer

    def test_stop_line_and_denials_together(self):
        footer = format_stop_reason(
            _response(
                result_subtype="error_max_turns",
                permission_denials=[{"tool_name": "Bash", "tool_input": {}}],
            )
        )

        assert footer is not None
        assert "turn limit reached" in footer
        assert "1 tool call was blocked: Bash" in footer


class TestFormatPermissionDenials:
    """The blocked-calls line."""

    def test_none_when_nothing_was_denied(self):
        assert format_permission_denials([]) is None

    def test_plural_wording_and_argument_extraction(self):
        line = format_permission_denials(
            [
                {"tool_name": "Write", "tool_input": {"file_path": "/etc/hosts"}},
                {"tool_name": "Bash", "tool_input": {"command": "cd /"}},
            ]
        )

        assert line == "🚫 2 tool calls were blocked: Write(`/etc/hosts`), Bash(`cd /`)"

    def test_tool_without_a_recognised_argument(self):
        line = format_permission_denials([{"tool_name": "WebSearch", "tool_input": {}}])

        assert line == "🚫 1 tool call was blocked: WebSearch"

    def test_long_arguments_are_clipped(self):
        line = format_permission_denials(
            [{"tool_name": "Bash", "tool_input": {"command": "echo " + "a" * 200}}]
        )

        assert line is not None
        assert "…" in line
        assert len(line) < 100

    def test_whitespace_in_arguments_is_collapsed(self):
        line = format_permission_denials(
            [{"tool_name": "Bash", "tool_input": {"command": "ls\n  -la"}}]
        )

        assert line == "🚫 1 tool call was blocked: Bash(`ls -la`)"

    def test_long_lists_are_summarised(self):
        denials = [{"tool_name": f"Tool{i}", "tool_input": {}} for i in range(8)]

        line = format_permission_denials(denials)

        assert line is not None
        assert line.startswith("🚫 8 tool calls were blocked:")
        assert "and 3 more" in line
        assert "Tool5" not in line

    def test_non_dict_entries_are_ignored(self):
        """permission_denials is typed list[Any]; the CLI payload is opaque."""
        assert format_permission_denials(["not a dict", None]) is None

    def test_non_list_input_is_ignored(self):
        assert format_permission_denials(None) is None

    def test_malformed_entries_do_not_hide_the_ones_behind_them(self):
        """Filtering after the slice let five bad entries swallow the list."""
        denials = ["junk"] * 5 + [
            {"tool_name": "Write", "tool_input": {"file_path": "/etc/hosts"}}
        ]

        line = format_permission_denials(denials)

        assert line is not None
        assert "Write(`/etc/hosts`)" in line
        assert "6 tool calls were blocked" in line

    def test_missing_tool_name_falls_back(self):
        line = format_permission_denials([{"tool_input": {"file_path": "/x"}}])

        assert line == "🚫 1 tool call was blocked: unknown(`/x`)"


class TestInterruptedRuns:
    """The user pressed Stop; they do not need to be told why it ended."""

    def test_note_replaces_the_stop_reason(self):
        footer = format_stop_reason(
            _response(
                interrupted=True,
                result_subtype="error_during_execution",
                terminal_reason="aborted_streaming",
            )
        )

        assert footer == "\n\n_(Interrupted by user)_"

    def test_wording_is_unchanged_from_before_the_footer_existed(self):
        response = _response(content="Partial output.", interrupted=True)

        assert (
            with_stop_reason(response) == "Partial output.\n\n_(Interrupted by user)_"
        )

    def test_blocked_calls_are_still_listed(self):
        footer = format_stop_reason(
            _response(
                interrupted=True,
                permission_denials=[{"tool_name": "Bash", "tool_input": {}}],
            )
        )

        assert footer is not None
        assert "_(Interrupted by user)_" in footer
        assert "1 tool call was blocked: Bash" in footer
        assert "Stopped" not in footer


class TestFooterSurvivesTheMarkdownPass:
    """The footer is rendered with Claude's reply, so Markdown runs over it."""

    def test_underscores_in_a_path_are_not_italicised(self):
        """Without inline code, /tmp/_a_b_ renders as /tmp/<i>a_b</i>."""
        footer = format_stop_reason(
            _response(
                result_subtype="success",
                permission_denials=[
                    {"tool_name": "Write", "tool_input": {"file_path": "/tmp/_a_b_"}}
                ],
            )
        )

        assert footer is not None
        html = markdown_to_telegram_html(footer)
        assert "<code>/tmp/_a_b_</code>" in html
        assert "<i>" not in html

    def test_italics_do_not_bleed_between_two_denials(self):
        """One underscore per entry used to open an italic span in the next."""
        footer = format_stop_reason(
            _response(
                result_subtype="success",
                permission_denials=[
                    {"tool_name": "Read", "tool_input": {"file_path": "/x/_p_"}},
                    {"tool_name": "Write", "tool_input": {"file_path": "/y/_q_"}},
                ],
            )
        )

        assert footer is not None
        html = markdown_to_telegram_html(footer)
        assert "<code>/x/_p_</code>" in html
        assert "<code>/y/_q_</code>" in html
        assert "<i>" not in html

    def test_shell_metacharacters_are_html_escaped(self):
        footer = format_stop_reason(
            _response(
                result_subtype="success",
                permission_denials=[
                    {"tool_name": "Bash", "tool_input": {"command": "cd / && ls"}}
                ],
            )
        )

        assert footer is not None
        html = markdown_to_telegram_html(footer)
        assert "<code>cd / &amp;&amp; ls</code>" in html

    def test_backticks_in_the_argument_are_preserved(self):
        """`whoami` runs a command; whoami prints a word. Dropping the
        backticks would report a different call than the one blocked."""
        footer = format_stop_reason(
            _response(
                result_subtype="success",
                permission_denials=[
                    {"tool_name": "Bash", "tool_input": {"command": "echo `whoami`"}}
                ],
            )
        )

        assert footer is not None
        html = markdown_to_telegram_html(footer)
        assert "<code>echo `whoami`</code>" in html

    def test_consecutive_backticks_are_preserved(self):
        footer = format_stop_reason(
            _response(
                result_subtype="success",
                permission_denials=[
                    {"tool_name": "Bash", "tool_input": {"command": "echo ``x``"}}
                ],
            )
        )

        assert footer is not None
        html = markdown_to_telegram_html(footer)
        assert "<code>echo ``x``</code>" in html

    def test_an_argument_that_is_only_backticks_survives(self):
        footer = format_stop_reason(
            _response(
                result_subtype="success",
                permission_denials=[
                    {"tool_name": "Bash", "tool_input": {"command": "`"}}
                ],
            )
        )

        assert footer is not None
        html = markdown_to_telegram_html(footer)
        assert "<code>`</code>" in html

    def test_backticks_with_html_metacharacters(self):
        footer = format_stop_reason(
            _response(
                result_subtype="success",
                permission_denials=[
                    {
                        "tool_name": "Bash",
                        "tool_input": {"command": "echo `a && b` > /x"},
                    }
                ],
            )
        )

        assert footer is not None
        html = markdown_to_telegram_html(footer)
        assert "<code>echo `a &amp;&amp; b` &gt; /x</code>" in html

    def test_error_prose_is_not_italicised(self):
        footer = format_stop_reason(
            _response(
                result_subtype="error_during_execution",
                errors=["cannot read _config_ from *here*"],
            )
        )

        assert footer is not None
        html = markdown_to_telegram_html(footer)
        assert "<i>" not in html
        assert "<b>" not in html


class TestWithStopReason:
    """The helper every render site uses."""

    def test_clean_run_is_the_content_unchanged(self):
        response = _response(content="All done.", result_subtype="success")

        assert with_stop_reason(response) == "All done."

    def test_stopped_run_gets_the_footer(self):
        response = _response(content="Partial.", result_subtype="error_max_turns")

        text = with_stop_reason(response)

        assert text.startswith("Partial.")
        assert "turn limit reached" in text

    def test_empty_content_still_carries_the_footer(self):
        response = _response(content="", result_subtype="error_max_turns")

        assert "turn limit reached" in with_stop_reason(response)


class TestEveryRenderSiteCarriesTheFooter:
    """A new entry point that renders a reply must not skip the footer.

    The first cut of this fix covered `agentic_text` and the four sites in
    `handlers/message.py` and missed five others, so a run truncated at the
    turn limit still reported success through document upload, voice, photo,
    `/continue` and the quick-action buttons. This walks the source rather
    than trusting a list.
    """

    def test_format_claude_response_is_always_given_the_footer(self):
        import ast
        from pathlib import Path

        import src.bot as bot_package

        offenders = []
        for path in sorted(Path(bot_package.__file__).parent.rglob("*.py")):
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                name = func.attr if isinstance(func, ast.Attribute) else None
                if name != "format_claude_response" or not node.args:
                    continue
                argument = node.args[0]
                wrapped = (
                    isinstance(argument, ast.Call)
                    and isinstance(argument.func, ast.Name)
                    and argument.func.id == "with_stop_reason"
                )
                if not wrapped:
                    offenders.append(f"{path.name}:{node.lineno}")

        assert offenders == [], (
            "these calls render a Claude reply without the stop-reason "
            f"footer: {offenders}"
        )
