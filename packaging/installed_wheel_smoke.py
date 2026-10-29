"""Check the installed wheel's examples against the checkout, outside the checkout.

Run with the clean wheel environment's interpreter and the source examples directory:
    VENV/python packaging/installed_wheel_smoke.py /checkout/examples/specs
No model, renderer, or test-only dependency is needed.
"""
from __future__ import annotations

import hashlib
import json
import argparse
from pathlib import Path
import sys

import spec_surgeon
from spec_surgeon import catalog


def prompt_fingerprints():
    from spec_surgeon.compiler import system_prompt
    return {mode: hashlib.sha256(system_prompt(chat=chat).encode()).hexdigest()
            for mode, chat in (("compile", False), ("chat", True))}


def check(source: Path, expected_prompts=None):
    installed = Path(spec_surgeon.__file__).resolve()
    assert installed.is_relative_to(Path(sys.prefix).resolve()), f"Not the clean wheel installation: {installed}"
    expected = {p.name: p.read_bytes() for p in source.glob("*.json")}
    actual = {p.name: p.read_bytes() for p in catalog.EXAMPLES_DIR.glob("*.json")}
    assert expected and actual == expected, "Installed worked examples differ from the source inventory"
    examples = [json.loads(expected[name]) for name in sorted(expected)]
    assert catalog.worked_examples() == examples
    assert catalog.op_catalog()["worked_examples"] == examples
    # the guided tour's bundled showcase ships beside the worked examples (examples/tutorial/, tutorial.py)
    from spec_surgeon import tutorial
    showcase = {name: path.read_bytes() for name, path in tutorial.showcase_files().items()}
    checkout_showcase = source.resolve().parents[1] / "src" / "spec_surgeon" / "examples" / "tutorial"
    source_showcase = {p.name: p.read_bytes() for p in checkout_showcase.iterdir() if p.is_file()}
    assert showcase == source_showcase, "Installed tour showcase differs from the source inventory"
    # the bundled numbering schemes the chat offers (examples/schemes/, schemes.py)
    from spec_surgeon import schemes
    installed_schemes = {s: schemes.scheme_file(s).read_bytes() for s in schemes.SCHEMES}
    checkout_schemes = source.resolve().parents[1] / "src" / "spec_surgeon" / "examples" / "schemes"
    assert installed_schemes == {s: (checkout_schemes / schemes.SCHEMES[s][0]).read_bytes() for s in schemes.SCHEMES}, \
        "Installed numbering schemes differ from the source inventory"
    prompts = prompt_fingerprints()
    if expected_prompts is not None:
        assert prompts == expected_prompts, "Installed compiler prompts differ from the source checkout"
    digest = hashlib.sha256(json.dumps(examples, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    print(json.dumps({"worked_examples": len(examples), "catalog_sha256": digest,
                      "prompt_sha256": prompts, "package": str(installed)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", nargs="?", type=Path)
    parser.add_argument("--expected-prompts", type=Path)
    parser.add_argument("--print-prompt-fingerprints", action="store_true")
    args = parser.parse_args()
    if args.print_prompt_fingerprints:
        print(json.dumps(prompt_fingerprints()))
    else:
        if args.source is None:
            parser.error("source examples directory is required")
        expected = json.loads(args.expected_prompts.read_text()) if args.expected_prompts else None
        check(args.source, expected)
