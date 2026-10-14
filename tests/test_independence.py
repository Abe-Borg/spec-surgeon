"""The verifier must not share code with the transform side (CLAUDE.md rule 1): nothing from jobs/, ops/,
editspec.py, ooxml.py or package.py."""
import ast
from pathlib import Path

VERIFY = Path(__file__).resolve().parent.parent / "src" / "spec_surgeon" / "verify"
FORBIDDEN = {"spec_surgeon.jobs", "spec_surgeon.ooxml", "spec_surgeon.package", "spec_surgeon.ops",
             "spec_surgeon.editspec", "jobs", "ooxml", "package", "ops", "editspec"}


def test_verify_imports_nothing_from_transform_side():
    for py in VERIFY.glob("*.py"):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                assert not any(mod == f or mod.endswith("." + f) or mod.startswith(f + ".") for f in FORBIDDEN), (py.name, mod)
                if node.level >= 2:  # from ..something import
                    assert mod.split(".")[0] not in {"jobs", "ooxml", "package", "ops", "editspec"}, (py.name, mod)
                    assert mod or all(a.name not in {"jobs", "ooxml", "package", "ops", "editspec"}
                                      for a in node.names), (py.name, [a.name for a in node.names])
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    assert not any(alias.name == f or alias.name.startswith(f + ".") for f in FORBIDDEN), (py.name, alias.name)
