"""Find and replace, occurrence by occurrence (backlog F1): the waiting plan lists every occurrence per document, the
user's unticks go into the previewed spec through the gate, the preview is made again, and the copies leave them as
they are. A refused document lists what was found up to the refusal, so the occurrence the refusal names can be
unticked and the document then plans."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from make_fixtures import build, build_batch1
from spec_surgeon import llm, vision
from spec_surgeon.document_task import DocumentTask
from spec_surgeon.gui import Session
from spec_surgeon.package import DocxPackage, W


def accepted_text(data: bytes, starts: str) -> str:
    root = DocxPackage(data).xml("word/document.xml")
    for p in root.iter(W + "p"):
        out, hidden = [], False
        for el in p.iter(W + "t", W + "tab", W + "delText"):
            q, gone = el.getparent(), False
            while q is not None and q is not p:
                if q.tag in (W + "del", W + "moveFrom"):
                    gone = True
                q = q.getparent()
            if not gone and el.tag != W + "delText":
                out.append("\t" if el.tag == W + "tab" else (el.text or ""))
        text = "".join(out)
        if text.startswith(starts):
            return text
    raise AssertionError(f"no paragraph starting {starts!r}")


@pytest.fixture
def folder(tmp_path) -> Path:
    d = tmp_path / "specs"
    d.mkdir()
    (d / "21 13 13.docx").write_bytes(build("21 13 13", "WET-PIPE SPRINKLER SYSTEMS"))
    (d / "21 13 16.docx").write_bytes(build("21 13 16", "DRY-PIPE SPRINKLER SYSTEMS"))
    return d


@pytest.fixture(autouse=True)
def no_ai(monkeypatch):
    def never(*a, **k):
        raise AssertionError("no AI call")
    monkeypatch.setattr(vision, "review_samples", never)
    monkeypatch.setattr(llm, "client", never)


def safe_task(folder: Path, tmp_path: Path, files=None) -> DocumentTask:
    task = DocumentTask(Session(out_root=tmp_path / "out", visual_check=False), render=False, mode="safe")
    task.attach(files or sorted(folder.glob("*.docx")))
    return task


PAIRS = {"pairs": [{"find": "FM Global", "replace": "GRC"}]}


def test_the_plan_lists_every_occurrence_of_every_document_and_an_untick_leaves_it(folder, tmp_path):
    task = safe_task(folder, tmp_path)
    view = task.perform("recipe", recipe="find_replace", inputs=PAIRS)
    assert view["stage"] == "preview"
    docs = view["occurrences"]
    assert [d["name"] for d in docs] == ["21 13 13.docx", "21 13 16.docx"]
    assert all(d["status"] == "planned" and d["reason"] == "" for d in docs)
    first = docs[0]["items"]
    assert [i["paragraph"] for i in first] == [12, 13, 14, 16] and not any(i["skipped"] for i in first)
    b = first[1]
    assert b["where"] == "the body" and b["match"] == "FM Global" and b["find"] == "FM Global" and b["replace"] == "GRC"
    assert b["before"] == "B. The auditor is" and b["after"] == "and reviews the hydraulic calculations."
    assert b["part"] == "word/document.xml" and b["end"] - b["start"] == len("FM Global") and b["op_index"] == 0
    keys = [i["key"] for d in docs for i in d["items"]]
    assert len(set(keys)) == len(keys) == 8
    token = view["token"]
    # untick B's occurrence in the first document: the spec carries it, the plan says so, the token changes
    view = task.perform("occurrences", skip=[b["key"]])
    assert view["stage"] == "preview" and view["token"] != token
    assert task.session.spec["ops"][0]["params"]["skip"] == [
        {"file": docs[0]["source"], "part": "word/document.xml", "paragraph": 13, "start": b["start"], "end": b["end"],
         "find": "FM Global"}]
    docs = view["occurrences"]
    assert [i["skipped"] for i in docs[0]["items"]] == [False, True, False, False]
    assert not any(i["skipped"] for i in docs[1]["items"])
    assert task.messages[-1]["role"] == "app" and "1 occurrence will be left as it is" in task.messages[-1]["text"]
    assert task.session.model_free                      # still the button's plan: no AI call anywhere (above)
    # the copies: B keeps FM Global in the first document, every other occurrence is replaced
    view = task.perform("primary", token=view["token"])
    assert view["stage"] == "done" and len(task.results) == 2
    out = {Path(src).name: Path(dst).read_bytes() for src, dst in task.results.items()}
    assert accepted_text(out["21 13 13.docx"], "B.") == "B.\tThe auditor is FM Global and reviews the hydraulic calculations."
    assert "GRC" in accepted_text(out["21 13 13.docx"], "A.") and "FM Global" not in accepted_text(out["21 13 13.docx"], "A.")
    assert "FM Global" not in accepted_text(out["21 13 16.docx"], "B.")
    assert all(r.status == "PASS" for r in task.session.last_batch.files)
    carried = [line for lines in task.applied.values() for line in lines]
    assert any("1 occurrence you unticked in the preview is left as it is" in line for line in carried)
    assert task.view()["occurrences"] == []           # the list belongs to a waiting plan


def test_every_untick_is_the_whole_set_and_an_unknown_key_or_no_preview_is_refused(folder, tmp_path):
    task = safe_task(folder, tmp_path)
    view = task.perform("occurrences", skip=[])
    assert view["stage"] == "empty" and task.messages[-1]["text"] == "There is no preview to update."
    view = task.perform("recipe", recipe="find_replace", inputs=PAIRS)
    items = view["occurrences"][0]["items"]
    view = task.perform("occurrences", skip=[items[0]["key"], items[2]["key"]])
    assert [i["skipped"] for i in view["occurrences"][0]["items"]] == [True, False, True, False]
    assert len(task.session.spec["ops"][0]["params"]["skip"]) == 2
    view = task.perform("occurrences", skip=[items[2]["key"]])          # the first one put back
    assert [i["skipped"] for i in view["occurrences"][0]["items"]] == [False, False, True, False]
    view = task.perform("occurrences", skip=[])                          # all back
    assert not any(i["skipped"] for i in view["occurrences"][0]["items"])
    assert "skip" not in task.session.spec["ops"][0]["params"]
    assert "Every occurrence is back in" in task.messages[-1]["text"]
    before, token = task.session.spec, view["token"]
    view = task.perform("occurrences", skip=["no such key"])
    assert "not in the preview's list" in task.messages[-1]["text"]
    assert task.session.spec is before and view["stage"] == "preview" and view["token"] == token   # the preview stands
    with pytest.raises(ValueError, match="could not be read"):
        task.perform("occurrences", skip="x")
    task.stage = "preview"                                               # (perform voids the stage on an exception)
    # Discard shows what came before the button, as for any Safe-mode preview
    task.discard_preview()
    assert task.view()["stage"] == "empty" and task.view()["occurrences"] == []


def test_a_refused_document_lists_its_occurrences_and_unticking_the_named_one_lets_it_plan(tmp_path):
    folder = tmp_path / "specs"
    folder.mkdir()
    (folder / "A.docx").write_bytes(build_batch1())                          # "Owner" inside a hyperlink (H.)
    (folder / "B.docx").write_bytes(build("21 13 16", "DRY-PIPE SPRINKLER SYSTEMS"))
    task = safe_task(folder, tmp_path)
    view = task.perform("recipe", recipe="find_replace",
                        inputs={"pairs": [{"find": "Owner", "replace": "Client"}], "tracked": True})
    assert view["stage"] == "preview"
    assert [d["name"] for d in view["set_aside"]] == ["A.docx"]
    docs = {d["name"]: d for d in view["occurrences"]}
    a = docs["A.docx"]
    assert a["status"] == "refused" and a["reason"]
    assert [i["paragraph"] for i in a["items"]] == [4, 10, 11]           # up to the refusal, the refused one included
    assert docs["B.docx"]["status"] == "planned"
    h = next(i for i in a["items"] if i["paragraph"] == 11)
    view = task.perform("occurrences", skip=[h["key"]])
    assert view["stage"] == "preview" and view["set_aside"] == []
    a = {d["name"]: d for d in view["occurrences"]}["A.docx"]
    assert a["status"] == "planned" and [i["paragraph"] for i in a["items"]] == [4, 10, 11, 12, 13, 14, 15]
    assert [i["paragraph"] for i in a["items"] if i["skipped"]] == [11]
    view = task.perform("primary", token=view["token"])
    assert view["stage"] == "done" and len(task.results) == 2 and view["left_untouched"] == []


def test_the_record_and_the_journal_carry_the_unticks(folder, tmp_path):
    journal = tmp_path / "journal.json"
    task = DocumentTask(Session(out_root=tmp_path / "out", visual_check=False), render=False, mode="safe",
                        journal=journal)
    task.attach(sorted(folder.glob("*.docx")))
    view = task.perform("recipe", recipe="find_replace", inputs=PAIRS)
    key = view["occurrences"][1]["items"][0]["key"]
    view = task.perform("occurrences", skip=[key])
    task.perform("primary", token=view["token"])
    batch = task.session.last_batch
    record = next(r for r in batch.files if Path(r.source).name == "21 13 16.docx")
    assert record.status == "PASS"
    # the occurrence log (backlog F2): the record's JSON carries every occurrence with its status, its markdown
    # lists them, and the batch report counts them per copy
    rec = json.loads((batch.out_dir / "records" / f"{record.record_stem}.record.json").read_text(encoding="utf-8"))
    v10 = next(c for c in rec["checks"] if c["id"] == "V10")
    log = v10["data"]["occurrences"]                               # the checker's own finding, not the plan's claim
    assert [o["skipped"] for o in log] == [True, False, False, False]
    assert log[0]["where"] == "the body" and log[0]["match"] == "FM Global" and log[0]["replace"] == "GRC"
    assert log[0]["before"] == "A. The Owner's insurer is" and "key" not in log[0]
    plan = json.loads((batch.out_dir / "plans" / f"{record.record_stem}.plan.json").read_text(encoding="utf-8"))
    a = next(x for x in plan["property_assertions"] if x["kind"] == "replaced_text")
    assert len(a["skip"]) == 1 and [o["skipped"] for o in a["occurrences"]] == [True, False, False, False]
    md = (batch.out_dir / "records" / f"{record.record_stem}.record.md").read_text(encoding="utf-8")
    assert "FIND AND REPLACE, OCCURRENCE BY OCCURRENCE: 3 replaced, 1 left at the user's word" in md
    assert "the body, paragraph 13: A. The Owner's insurer is «FM Global» and all equipment shall be FM Approved. left at the user's word" in md
    assert "the body, paragraph 14: B. The auditor is «FM Global» and reviews the hydraulic calculations. → 'GRC'" in md
    report = (batch.out_dir / "BATCH_REPORT.md").read_text(encoding="utf-8")
    assert "## Find and replace, occurrence by occurrence" in report
    assert "- 21 13 16.docx: 3 replaced, 1 left at the user's word" in report
    assert "- 21 13 13.docx: 4 replaced, 0 left at the user's word" in report
    # the journal keeps the spec with its skip list, so a restored window shows the same plan
    data = json.loads(journal.read_text(encoding="utf-8"))
    assert data["applied"]
    assert any("1 occurrence you unticked in the preview is left as it is" in line
               for lines in data["applied"].values() for line in lines)


# ---- Add a folder of documents (backlog F3) ---------------------------------------------------------------------------

def test_a_folder_attaches_its_own_documents_and_names_what_its_subfolders_hold(tmp_path):
    folder = tmp_path / "Specs"
    (folder / "Division 22").mkdir(parents=True)
    (folder / "b.docx").write_bytes(build("21 13 16", "DRY-PIPE SPRINKLER SYSTEMS"))
    (folder / "A.docx").write_bytes(build("21 13 13", "WET-PIPE SPRINKLER SYSTEMS"))
    (folder / "~$A.docx").write_bytes(b"lock")                                 # Word's lock file: skipped
    (folder / "notes.txt").write_text("not a document", encoding="utf-8")
    (folder / "Division 22" / "22 11 16.docx").write_bytes(build("22 11 16", "DOMESTIC WATER PIPING"))
    (folder / "Division 22" / "22 13 16.docx").write_bytes(build("22 13 16", "SANITARY WASTE AND VENT PIPING"))
    task = DocumentTask(Session(out_root=tmp_path / "out", visual_check=False), render=False, mode="safe")
    got = task.attach_folder(folder)
    assert got == {"folder": str(folder.resolve()), "attached": 2, "subfolders": 2}
    assert [a.path.name for a in task.attachments] == ["A.docx", "b.docx"]          # sorted by name, the rest left
    assert task.messages[-1]["role"] == "app"
    assert task.messages[-1]["text"] == ("2 documents added from Specs. Its subfolders hold 2 more Word documents, not "
                                         "added; add those folders too if you want them.")
    # the same folder again: nothing attached twice, and the message says so
    got = task.attach_folder(folder)
    assert got["attached"] == 0 and len(task.attachments) == 2
    assert task.messages[-1]["text"].startswith("0 documents added from Specs (2 already attached).")
    with pytest.raises(ValueError, match="no Word documents of its own; its subfolders hold 1"):
        empty = tmp_path / "Empty"
        (empty / "sub").mkdir(parents=True)
        (empty / "sub" / "x.docx").write_bytes(build("21 13 13", "X"))
        task.attach_folder(empty)
    with pytest.raises(ValueError, match="is not a folder"):
        task.attach_folder(folder / "A.docx")
    with pytest.raises(ValueError, match="app's own folders"):
        (tmp_path / "out").mkdir(exist_ok=True)
        task.attach_folder(tmp_path / "out")
