"""Extension import-boundary test — ARISTOTLE's self-defending SoC boundary.

This is the machine-enforced boundary that makes Aristotle's separation of
concerns permanent. It catches the real SoC erosion: a forbidden import
inside Aristotle that reaches into platform internals.

Rule (AST-checked — catches static, lazy, AND importlib imports):

  Aristotle imports from `aip.*` ONLY through the allowlist:
    - aip.foundation.protocols.*   (Actor Protocol + future Protocols)
    - aip.adapter.extensions       (public extension API: Manifest, etc.)
    - aip.foundation.schemas       (dataclasses extensions may use)

  Anything else — aip.adapter.corpus_registry, aip.orchestration.*,
  aip.adapter.api.* — is a hard violation. Aristotle reaches the container
  via ctx.container (duck-typed as Any in the foundation Protocol), not by
  importing it.

The allowlist is deliberately small. Growing it requires a deliberate
decision recorded in this file. The test fails CI loudly on the first
forbidden import.

This test mirrors the platform's tests/test_extension_import_boundary.py
(which checks ALL extensions). This one checks ARISTOTLE specifically —
so the boundary is enforced from both sides.

Run:  pytest tests/test_import_boundary.py -v
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ARISTOTLE_ROOT = PROJECT_ROOT / "aristotle"

# ---------------------------------------------------------------------------
# The allowlist — Aristotle may import from aip.* ONLY through these.
# Growing this list is a deliberate architectural decision.
# ---------------------------------------------------------------------------

ALLOWED_AIP_IMPORT_PREFIXES: tuple[str, ...] = (
    "aip.foundation.protocols",  # Actor Protocol + future Protocols
    "aip.adapter.extensions",  # public extension API (Manifest, etc.)
    "aip.foundation.schemas",  # dataclasses extensions may use
)


# ---------------------------------------------------------------------------
# AST helpers (same pattern as AIP_Brain's test_import_boundary.py)
# ---------------------------------------------------------------------------


def _is_type_checking_block(node: ast.AST) -> bool:
    if not isinstance(node, ast.If):
        return False
    test = node.test
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
    )


def _collect_imports(filepath: Path) -> list[tuple[str, int, str]]:
    """Collect all imports (static, lazy, importlib) from a Python file.

    Returns list of (module_path, line_number, import_style).
    """
    try:
        source = filepath.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return []

    try:
        tree = ast.parse(source, filename=str(filepath))
    except SyntaxError:
        return []

    imports: list[tuple[str, int, str]] = []

    def _visit(node: ast.AST, depth: int = 0) -> None:
        if _is_type_checking_block(node):
            return

        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.Import):
                for alias in child.names:
                    style = "static" if depth <= 1 else "lazy"
                    imports.append((alias.name, child.lineno, style))
                _visit(child, depth + 1)

            elif isinstance(child, ast.ImportFrom):
                if child.module and child.level == 0:
                    style = "static" if depth <= 1 else "lazy"
                    imports.append((child.module, child.lineno, style))
                _visit(child, depth + 1)

            elif isinstance(child, ast.Call):
                func = child.func
                mod_name: str | None = None

                if (
                    isinstance(func, ast.Attribute)
                    and func.attr == "import_module"
                    and child.args
                ):
                    arg = child.args[0]
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        mod_name = arg.value

                if mod_name:
                    imports.append((mod_name, child.lineno, "importlib"))

                if (
                    isinstance(func, ast.Name)
                    and func.id == "__import__"
                    and child.args
                ):
                    arg = child.args[0]
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        imports.append((arg.value, child.lineno, "importlib"))

                _visit(child, depth + 1)

            else:
                _visit(child, depth + 1)

    _visit(tree)
    return imports


def _py_files(directory: Path) -> list[Path]:
    if not directory.exists():
        return []
    return sorted(p for p in directory.rglob("*.py") if "__pycache__" not in p.parts)


def _is_allowed_aip_import(module: str) -> bool:
    """Return True if the module is in the allowlist or a submodule of it."""
    for prefix in ALLOWED_AIP_IMPORT_PREFIXES:
        if module == prefix or module.startswith(prefix + "."):
            return True
    return False


# ---------------------------------------------------------------------------
# Test: Aristotle imports only the allowlist
# ---------------------------------------------------------------------------


def test_aristotle_imports_only_allowlist():
    """Every .py under aristotle/ imports from aip.* ONLY through the allowlist.

    The allowlist: aip.foundation.protocols.*, aip.adapter.extensions,
    aip.foundation.schemas. Anything else is a hard violation — Aristotle
    reaches the container via ctx.container (duck-typed), not by importing it.

    This test is the machine-enforced separation of concerns. It catches
    the real erosion: a forbidden `from aip.adapter.corpus_registry import ...`
    inside Aristotle, six weeks from now.
    """
    if not ARISTOTLE_ROOT.exists():
        pytest.skip("No aristotle/ directory present")

    violations: list[str] = []

    for py_file in _py_files(ARISTOTLE_ROOT):
        rel = py_file.relative_to(PROJECT_ROOT)
        for module, lineno, style in _collect_imports(py_file):
            if module.startswith("aip.") or module == "aip":
                if not _is_allowed_aip_import(module):
                    violations.append(
                        f"{rel}:{lineno} ({style}) — imports {module!r} "
                        f"(not in allowlist: {ALLOWED_AIP_IMPORT_PREFIXES})"
                    )

    assert not violations, (
        "Aristotle may import from aip.* ONLY through the allowlist "
        f"({ALLOWED_AIP_IMPORT_PREFIXES}). Aristotle reaches the container "
        "via ctx.container (duck-typed), not by importing platform internals.\n  "
        + "\n  ".join(violations)
    )


# ---------------------------------------------------------------------------
# Test: informational summary (always passes)
# ---------------------------------------------------------------------------


def test_aristotle_boundary_summary():
    """Informational: print the current aip.* imports across aristotle/.

    Always passes. Gives visibility into the coupling surface during CI runs.
    """
    if not ARISTOTLE_ROOT.exists():
        print("\nNo aristotle/ directory present.")
        return

    summary: dict[str, list[str]] = {}
    for py_file in _py_files(ARISTOTLE_ROOT):
        rel = py_file.relative_to(PROJECT_ROOT)
        for module, lineno, style in _collect_imports(py_file):
            if module.startswith("aip.") or module == "aip":
                allowed = "ALLOWED" if _is_allowed_aip_import(module) else "FORBIDDEN"
                summary.setdefault(module, []).append(
                    f"  {rel}:{lineno} ({style}) [{allowed}]"
                )

    print("\n" + "=" * 72)
    print("ARISTOTLE IMPORT BOUNDARY SUMMARY")
    print("=" * 72)
    if not summary:
        print("\n  No aip.* imports found in aristotle/.")
    else:
        for module in sorted(summary):
            print(f"\n  {module}:")
            for entry in summary[module]:
                print(entry)
    print("\n" + "=" * 72)
    assert True
