"""HTML formatting utilities for Telegram messages.

Telegram's HTML mode only requires escaping 3 characters (<, >, &) vs the many
ambiguous Markdown v1 metacharacters, making it far more robust for rendering
Claude's output which contains underscores, asterisks, brackets, etc.
"""

import re
from bisect import bisect_left
from typing import Callable, Dict, List, Tuple


def escape_html(text: str) -> str:
    """Escape the 3 HTML-special characters for Telegram.

    This replaces all 3 _escape_markdown functions previously scattered
    across the codebase.
    """
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _strip_code_span_padding(code: str) -> str:
    """CommonMark: drop one space from each end when both ends have one.

    That is what lets a value which itself begins or ends with a backtick be
    written at all -- see _inline_code in formatting.py, which relies on it.
    """
    if len(code) >= 2 and code[0] == " " and code[-1] == " " and code.strip(" "):
        return code[1:-1]
    return code


def _extract_code_spans(text: str, render: Callable[[str], str]) -> str:
    r"""Replace each backtick code span with whatever `render` returns for it.

    A span opens on a run of backticks and closes on a run of exactly the same
    length, so a span can carry backticks of its own. Deleting the inner
    backticks instead would silently rewrite a shell command -- `whoami` is
    command substitution, whoami is an argument -- and this output is shown as
    an account of what a tool call actually was.

    Scanned rather than matched with a regex. Pairing equal-length runs needs a
    backreference inside a lazy middle, ``(`+)([^\n]*?)\1``, which re-scans
    the rest of the line for every opener that never closes: on a reply whose
    backtick runs are all of different lengths that is superlinear, 313ms at
    64KB and 2.4s at 256KB, blocking the event loop for every other user.

    Both lookups here are therefore O(1) or O(log n) rather than a scan: each
    run's next same-length partner is pre-computed in one backward pass, and
    "is there a newline in between" is a binary search over the newline
    offsets. Scanning the gap instead would reproduce the same shape -- an
    opener whose only partner sits at the far end of the text, across a line
    break, pays for the whole distance and is then skipped.
    """
    runs = [(m.start(), m.end()) for m in re.finditer(r"`+", text)]
    if not runs:
        return text

    next_same: List[int] = [-1] * len(runs)
    seen: Dict[int, int] = {}
    for i in range(len(runs) - 1, -1, -1):
        length = runs[i][1] - runs[i][0]
        next_same[i] = seen.get(length, -1)
        seen[length] = i

    newlines = [m.start() for m in re.finditer("\n", text)]

    def _crosses_a_line(start: int, end: int) -> bool:
        at = bisect_left(newlines, start)
        return at < len(newlines) and newlines[at] < end

    out: List[str] = []
    cursor = 0
    i = 0
    while i < len(runs):
        start, open_end = runs[i]
        close = next_same[i]
        # A span does not span lines, so an opener whose only same-length
        # partner sits beyond a newline is just text.
        if close == -1 or _crosses_a_line(open_end, runs[close][0]):
            i += 1
            continue
        out.append(text[cursor:start])
        out.append(render(_strip_code_span_padding(text[open_end : runs[close][0]])))
        cursor = runs[close][1]
        i = close + 1

    out.append(text[cursor:])
    return "".join(out)


def markdown_to_telegram_html(text: str) -> str:
    """Convert Claude's markdown output to Telegram-compatible HTML.

    Telegram supports a narrow HTML subset: <b>, <i>, <code>, <pre>,
    <a href>, <s>, <u>. This function converts common markdown patterns
    to that subset while preserving code blocks verbatim.

    Order of operations:
    1. Extract fenced code blocks -> placeholders
    2. Extract inline code -> placeholders (backtick runs of any length)
    3. HTML-escape remaining text
    4. Convert bold (**text** / __text__)
    5. Convert italic (*text*, _text_ with word boundaries)
    6. Convert links [text](url)
    7. Convert headers (# Header -> <b>Header</b>)
    8. Convert strikethrough (~~text~~)
    9. Restore placeholders
    """
    placeholders: List[Tuple[str, str]] = []
    placeholder_counter = 0

    def _make_placeholder(html_content: str) -> str:
        nonlocal placeholder_counter
        key = f"\x00PH{placeholder_counter}\x00"
        placeholder_counter += 1
        placeholders.append((key, html_content))
        return key

    # --- 1. Extract fenced code blocks ---
    def _replace_fenced(m: re.Match) -> str:  # type: ignore[type-arg]
        lang = m.group(1) or ""
        code = m.group(2)
        escaped_code = escape_html(code)
        if lang:
            html = f'<pre><code class="language-{escape_html(lang)}">{escaped_code}</code></pre>'
        else:
            html = f"<pre><code>{escaped_code}</code></pre>"
        return _make_placeholder(html)

    text = re.sub(
        r"```(\w+)?\n(.*?)```",
        _replace_fenced,
        text,
        flags=re.DOTALL,
    )

    # --- 2. Extract inline code ---
    def _replace_inline_code(code: str) -> str:
        return _make_placeholder(f"<code>{escape_html(code)}</code>")

    text = _extract_code_spans(text, _replace_inline_code)

    # --- 3. HTML-escape remaining text ---
    text = escape_html(text)

    # --- 4. Bold: **text** or __text__ ---
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"__(.+?)__", r"<b>\1</b>", text)

    # --- 5. Italic: *text* (require non-space after/before) ---
    text = re.sub(r"\*(\S.*?\S|\S)\*", r"<i>\1</i>", text)
    # _text_ only at word boundaries (avoid my_var_name)
    text = re.sub(r"(?<!\w)_(\S.*?\S|\S)_(?!\w)", r"<i>\1</i>", text)

    # --- 6. Links: [text](url) ---
    text = re.sub(
        r"\[([^\]]+)\]\(([^)]+)\)",
        r'<a href="\2">\1</a>',
        text,
    )

    # --- 7. Headers: # Header -> <b>Header</b> ---
    text = re.sub(r"^#{1,6}\s+(.+)$", r"<b>\1</b>", text, flags=re.MULTILINE)

    # --- 8. Strikethrough: ~~text~~ ---
    text = re.sub(r"~~(.+?)~~", r"<s>\1</s>", text)

    # --- 9. Restore placeholders ---
    for key, html_content in placeholders:
        text = text.replace(key, html_content)

    return text
