"""The verifier must not share code with the transform side (CLAUDE.md rule 1): nothing from jobs/, ops/,
editspec.py, ooxml.py or package.py — nor from the Claude layer (Claude never grades: llm, conversation, compiler,
codegen, advisor) or the app around the engine (preview, gui)."""
import ast
from pathlib import Path

VERIFY = Path(__file__).resolve().parent.parent / "src" / "spec_surgeon" / "verify"
_NAMES = {"jobs", "ooxml", "package", "ops", "editspec", "llm", "conversation", "vision", "library", "compiler",
          "codegen", "advisor", "preview", "gui"}
FORBIDDEN = {f"spec_surgeon.{n}" for n in _NAMES} | _NAMES


def test_verify_imports_nothing_from_transform_side():
    for py in VERIFY.glob("*.py"):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                assert not any(mod == f or mod.endswith("." + f) or mod.startswith(f + ".") for f in FORBIDDEN), (py.name, mod)
                if node.level >= 2:  # from ..something import
                    assert mod.split(".")[0] not in _NAMES, (py.name, mod)
                    assert mod or all(a.name not in _NAMES for a in node.names), (py.name, [a.name for a in node.names])
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    assert not any(alias.name == f or alias.name.startswith(f + ".") for f in FORBIDDEN), (py.name, alias.name)
