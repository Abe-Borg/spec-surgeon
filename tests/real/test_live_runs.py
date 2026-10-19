"""Replay private 2026-10-18 evidence; no corpus is shipped or synthesized here.

Run: SPEC_SURGEON_REAL_CORPUS=/private/live-runs python -m pytest -q -s tests/real/test_live_runs.py
Add SPEC_SURGEON_REAL_RENDERER=word on the owner's Windows machine for V6 acceptance;
the default is none and cannot establish Word or installed-app acceptance.

The corpus folder needs manifest.json:
{"sources":["specs/first.docx", "... eleven more real specs ..."],
 "template":"templates/owner.docx", "runs":[
 {"id":"1418","recipe":"recipes/1418.json","expected_files":12},
 {"id":"1521","recipe":"recipes/1521.json","expected_files":12}]}
Recipes are saved EditSpecs or chains; template input paths may use $TEMPLATE.
Other input paths and binding filenames resolve relative to each recipe file.
Only copies are delivered; every original, template and saved recipe stays pinned.
"""
import os

import pytest

from real.corpus import load_corpus, replay


@pytest.fixture(scope="module")
def private_corpus():
    folder = os.environ.get("SPEC_SURGEON_REAL_CORPUS")
    if not folder:
        pytest.skip("private 12-file live corpus unavailable; set SPEC_SURGEON_REAL_CORPUS")
    return load_corpus(folder)


@pytest.mark.parametrize("session", ["1418", "1521"])
def test_saved_live_recipe_delivers_all_twelve_specs(private_corpus, session, tmp_path):
    run = next(run for run in private_corpus.runs if run.id == session)
    renderer = os.environ.get("SPEC_SURGEON_REAL_RENDERER", "none")
    result = replay(private_corpus, run, tmp_path / session, renderer=renderer)
    print(f"2026-10-18 {session}: {len(result.files)} delivered; renderer={renderer}; outputs={result.out_dir / 'outputs'}")
