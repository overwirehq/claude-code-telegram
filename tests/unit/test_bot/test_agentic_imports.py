"""Guard: agentic mode must not import from the classic handlers package.

``src/bot/handlers/`` is classic-mode code slated for removal.  Everything the
agentic orchestrator needs from it has been moved to ``src/bot/commands.py``
and ``src/bot/utils/``; the one remaining import is the classic registration
inside ``_register_classic_handlers``.  This test walks the orchestrator's AST
so a new ``from .handlers`` import cannot slip back in unnoticed.
"""

import ast
from pathlib import Path

ORCHESTRATOR = Path(__file__).resolve().parents[3] / "src" / "bot" / "orchestrator.py"


def _handler_imports(tree: ast.Module):
    """Yield (import node, enclosing function name or None) for handlers imports."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for child in ast.walk(node):
            if isinstance(child, ast.ImportFrom) and _targets_handlers(child):
                yield child, node.name
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and _targets_handlers(node):
            yield node, None


def _targets_handlers(node: ast.ImportFrom) -> bool:
    module = node.module or ""
    return module == "handlers" or module.startswith("handlers.")


def test_orchestrator_imports_handlers_only_for_classic_registration():
    tree = ast.parse(ORCHESTRATOR.read_text(encoding="utf-8"))
    found = [(scope, ast.unparse(node)) for node, scope in _handler_imports(tree)]
    assert found == [
        (
            "_register_classic_handlers",
            "from .handlers import callback, command, message",
        )
    ], f"unexpected classic-handler imports in orchestrator.py: {found}"


def test_shared_code_does_not_import_handlers():
    """The modules agentic mode imports must not reach back into handlers/."""
    shared = [
        Path("src/bot/commands.py"),
        Path("src/bot/utils/error_messages.py"),
        Path("src/bot/utils/working_directory.py"),
    ]
    root = ORCHESTRATOR.parents[2]
    for rel in shared:
        tree = ast.parse((root / rel).read_text(encoding="utf-8"))
        bad = [
            ast.unparse(n)
            for n in ast.walk(tree)
            if isinstance(n, ast.ImportFrom) and "handlers" in (n.module or "")
        ]
        assert not bad, f"{rel} imports classic handlers: {bad}"
