"""Validate and replay the two saved live recipes without making model calls."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from spec_surgeon.batch import run_batch
from spec_surgeon.chain import run_chain, validate_chain
from spec_surgeon.editspec import input_slots, spec_templates, validate_spec


@dataclass(frozen=True)
class LiveRun:
    id: str
    recipe: Path
    expected_files: int


@dataclass(frozen=True)
class Corpus:
    sources: tuple[Path, ...]
    template: Path
    runs: tuple[LiveRun, ...]


def _file(root: Path, value, suffix: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError("corpus file paths must be nonempty strings")
    path = (root / value).resolve()
    if not path.is_relative_to(root) or not path.is_file() or path.suffix.casefold() != suffix:
        raise ValueError(f"corpus requires an existing {suffix} file inside its folder: {value}")
    return path


def load_corpus(folder) -> Corpus:
    root = Path(folder).resolve()
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or not isinstance(manifest.get("sources"), list):
        raise ValueError("corpus manifest needs a sources list")
    sources = tuple(_file(root, path, ".docx") for path in manifest["sources"])
    if len(sources) != 12 or len(set(sources)) != 12:
        raise ValueError("the live corpus must contain exactly 12 distinct source specifications")
    template = _file(root, manifest.get("template"), ".docx")
    if template in sources:
        raise ValueError("the owner template must be separate from the 12 specifications")
    rows = manifest.get("runs")
    if not isinstance(rows, list) or len(rows) != 2 or any(not isinstance(row, dict) for row in rows):
        raise ValueError("corpus manifest needs the two saved runs")
    runs = []
    for row in rows:
        if row.get("id") not in {"1418", "1521"} or type(row.get("expected_files")) is not int \
                or row["expected_files"] != 12:
            raise ValueError("live run ids must be 1418 and 1521, each expecting 12 delivered files")
        runs.append(LiveRun(row["id"], _file(root, row.get("recipe"), ".json"), row["expected_files"]))
    if {run.id for run in runs} != {"1418", "1521"}:
        raise ValueError("both live session recipes are required")
    return Corpus(sources, template, tuple(runs))


def load_recipe(run: LiveRun, template: Path):
    """Resolve moved input paths; $TEMPLATE explicitly names the corpus template."""
    value = json.loads(run.recipe.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("a saved recipe must be an EditSpec or a chain object")
    recipe = value.get("chain", value.get("spec", value))
    if not isinstance(recipe, dict):
        raise ValueError("saved recipe wrapper does not contain a recipe")
    specs = [stage["spec"] for stage in recipe["stages"]] if "stages" in recipe else [recipe]
    for spec in specs:
        for operation in spec.get("ops", []):
            for params, key in input_slots(operation):
                if params[key] == "$TEMPLATE":
                    params[key] = str(template)
                elif not Path(params[key]).is_absolute():
                    params[key] = str((run.recipe.parent / params[key]).resolve())
        for binding in spec.get("bindings", []):
            if isinstance(binding.get("file"), str) and not Path(binding["file"]).is_absolute():
                binding["file"] = str((run.recipe.parent / binding["file"]).resolve())
        validate_spec(spec)
        if any(path != template for path in spec_templates(spec)):
            raise ValueError("saved live recipes must refer to the declared owner template")
    if "stages" in recipe:
        validate_chain(recipe)
    return recipe


def assert_delivered(result, expected: int, *, rendered=False):
    assert len(result.files) == expected, "not every source reached the engine"
    assert all(file.status == "PASS" for file in result.files), "some engine checks failed or inputs were set aside"
    outputs = list((result.out_dir / "outputs").glob("*.docx"))
    assert len(outputs) == expected, "verified files were withheld from outputs/"
    assert not any(result.out_dir.rglob("failed/*.docx")), "a live deliverable was quarantined"
    records = [file.record for file in result.files] if not hasattr(result, "stages") else [
        file.record for stage in result.stages for file in stage.batch.files]
    for record in records:
        assert record is not None and record.passed
        assert not any(check.id == "GOAL" and check.status == "FAIL" for check in record.checks)
        if rendered:
            check = record.get("V6")
            assert check is not None and check.status == "PASS", "requested renderer did not complete V6"


def replay(corpus: Corpus, run: LiveRun, out_dir: Path, *, renderer="none"):
    if renderer not in {"none", "word", "libreoffice"}:
        raise ValueError("live renderer must be none, word or libreoffice")
    recipe = load_recipe(run, corpus.template)
    pinned = {path: hashlib.sha256(path.read_bytes()).hexdigest()
              for path in (*corpus.sources, corpus.template, run.recipe)}
    options = dict(workers=2, worker_mode="thread", renderer=renderer, do_render=renderer != "none", in_place=False)
    try:
        if "stages" in recipe:
            result = run_chain([str(path) for path in corpus.sources], recipe, out_dir, **options)
        else:
            result = run_batch([str(path) for path in corpus.sources], "edit-spec", {}, out_dir, spec=recipe, **options)
    finally:
        assert all(hashlib.sha256(path.read_bytes()).hexdigest() == digest for path, digest in pinned.items()), \
            "the private source corpus changed during replay"
    assert_delivered(result, run.expected_files, rendered=renderer != "none")
    return result
