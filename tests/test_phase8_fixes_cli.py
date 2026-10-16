"""Phase 8 review fixes, second pass — the command line, the advisor, the visual check, the conversation and the
verifier. Each test fails on the code before its fix (scripted client only; no API calls):

1. review-9 gap: ``do --allow-codegen`` took the custom transform's approval token AFTER the transform was written, so
   a file edited while the writer worked was approved as it is now while the preview paired its new render with a trial
   output made from the old content — the files' stats are taken before the writing and compared once the preview is
   made (exit 2, naming the file).
2. review-1 residual: an advisor respec carried the ``request`` Claude wrote into ``advice/<name>.spec.json`` — it
   carries the original spec's request.
3. codex-3 residual: Claude's visual check labelled two same-named samples alike and filed their findings under one
   name; the preview text named them alike — both use ``compiler.file_labels``.
4. review-6 residual: U+1D159 and U+16FE4 render blank and passed ``clean_wording``; the Zs spaces stay allowed.
5. review-24 minor: ``conversation.json``'s ``approved_words`` held ``"{}"`` (``memory_source`` of no PROJECT.json) —
   and so did the sources a spec's words are traced to.
6. V2 (and V4/V5b) sorted values that can be None beside text and raised TypeError instead of reporting: a total sort
   key, the same comparison.
"""
from __future__ import annotations

import copy
import io
import json
import os
import zipfile
from pathlib import Path

import pytest
from lxml import etree

from fake_claude import FakeClient, resp, tool
from make_fixtures import build, build_rich
from spec_surgeon import codegen, vision
from spec_surgeon import compiler as compiler_module
from spec_surgeon.advisor import advise, advise_batch
from spec_surgeon.batch import run_batch
from spec_surgeon.cli import main as cli_main
from spec_surgeon.compiler import untraceable
from spec_surgeon.conversation import Conversation, clean_wording
from spec_surgeon.editspec import load_spec
from spec_surgeon.preview import GeneratedPreview, SampleView, make_preview
from spec_surgeon.verify import run_suite
from spec_surgeon.verify.reader import W
from test_codegen import GOOD_OP

SPECS = Path(__file__).resolve().parent.parent / "examples" / "specs"
UNSUPPORTED = {"request": "r", "summary": "s", "unsupported": "no op sets the header distance alone",
               "proposal": "set_header_distance"}
OFFER = {"spec": UNSUPPORTED, "custom_transform": "possible"}
WRITER = resp(tool("submit_op", {"source": GOOD_OP, "params": {"twips": 900}}, "w1"))
DOC = "word/document.xml"


@pytest.fixture
def folder(tmp_path) -> Path:
    d = tmp_path / "specs"
    d.mkdir()
    for i in range(2):
        (d / f"21 0{i} 00 – SPEC {i}.docx").write_bytes(build(f"21 0{i} 00", f"SPEC {i}"))
    return d


def touch_edit(f: Path) -> None:
    """Edit a file the way saving it would: new content, new modification time."""
    st = f.stat()
    f.write_bytes(f.read_bytes() + b"\0")
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))


def record_reviews(monkeypatch):
    intents = []

    def review(intent, samples, work_dir, client_factory, *a, **k):
        intents.append(intent)
        return vision.VisualReview(summary="looked at the pages")
    monkeypatch.setattr(vision, "review_samples", review)
    return intents


# ---- 1. review-9 gap: the custom transform's approval is of the files as they were before it was written ----------

def test_do_codegen_refuses_a_file_edited_while_the_transform_was_written(folder, tmp_path, monkeypatch, capsys):
    edited = sorted(folder.glob("*.docx"))[1]
    real = codegen.generate_op

    def writing(*a, **k):                      # the user saves a file while Claude writes and the trials run
        g = real(*a, **k)
        touch_edit(edited)
        return g
    # control: nothing edited — the same command runs (the check compares like with like)
    monkeypatch.setattr(compiler_module, "_client", lambda: FakeClient(resp(tool("submit_editspec", OFFER)), WRITER))
    intents = record_reviews(monkeypatch)
    assert cli_main(["do", "header 0.625in from the top", str(folder), "--out", str(tmp_path / "ok"),
                     "--renderer", "none", "--allow-codegen", "--yes"]) == 0
    assert "2 of 2 file(s) PASS" in capsys.readouterr().out and (tmp_path / "ok" / "run").is_dir()
    assert len(intents) == 1

    monkeypatch.setattr(codegen, "generate_op", writing)
    out = tmp_path / "o"
    assert cli_main(["do", "header 0.625in from the top", str(folder), "--out", str(out), "--renderer", "none",
                     "--allow-codegen", "--yes"]) == 2
    printed = capsys.readouterr()
    assert "passed on the samples" in printed.out
    assert "Not run: a file changed while the custom transform was being written and tested" in printed.err
    assert f"{edited.name}: modified since the custom transform was started" in printed.err
    assert not (out / "run").exists()
    assert len(intents) == 1                   # refused before the visual check: no call to Claude for a stale preview


# ---- 2. review-1 residual: an advisor respec carries the original request ------------------------------------------

def test_an_advisor_respec_carries_the_original_request_never_claudes(tmp_path):
    d = tmp_path / "in"
    d.mkdir()
    (d / "21 05 00 – A.docx").write_bytes(build("21 05 00", "A"))
    (d / "21 13 13 – RICH.docx").write_bytes(build_rich())
    spec = load_spec(SPECS / "remove-all-headers-footers.json")
    spec["request"] = "remove every header and footer"
    spec["ops"][0]["expect"] = {"members_removed": 3}
    out = tmp_path / "out"
    b = run_batch([str(d)], "edit-spec", {}, out, workers=1, renderer="none", do_render=False, spec=spec)
    assert [f.status for f in b.files] == ["PASS", "EXCEPTION"]
    rec = json.loads(next((out / "exceptions").glob("*.exception.json")).read_text(encoding="utf-8"))
    claudes = "Remove every header and footer and replace NFPA 13 (2025) with NFPA 13 (2028)"
    fixed = copy.deepcopy(rec["spec"])
    fixed["ops"][0]["expect"] = {"members_removed": 9}
    fixed["request"] = claudes

    adv = advise(rec, client=FakeClient(resp(tool("submit_editspec", {"spec": fixed, "why": "9 parts here"}))))
    assert adv.kind == "respec" and adv.spec["request"] == "remove every header and footer"
    assert adv.spec["applies_to"] == {"pattern_ids": [rec["fingerprint"]["pattern_id"]]}

    res = advise_batch(out, client=FakeClient(resp(tool("submit_editspec", {"spec": copy.deepcopy(fixed)}))))
    assert [a.kind for a in res] == ["respec"]
    written = next((out / "advice").glob("*.spec.json")).read_text(encoding="utf-8")
    assert json.loads(written)["request"] == "remove every header and footer" and "2028" not in written


# ---- 3. codex-3 residual: same-named samples are told apart --------------------------------------------------------

def make_pdf(path: Path, words: str) -> Path:
    from spec_surgeon.verify.geometry import fitz
    doc = fitz.open()
    doc.new_page(width=612, height=792).insert_text((72, 72), words, fontsize=14)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(path))
    doc.close()
    return path


def two_same_named_samples(tmp_path):
    out = []
    for sub in ("a", "b"):
        out.append(SampleView(source=tmp_path / sub / "Spec.docx", output=tmp_path / f"{sub}-out.docx", passed=True,
                              source_pdf=make_pdf(tmp_path / "pdf" / sub / "before.pdf", f"{sub} before"),
                              output_pdf=make_pdf(tmp_path / "pdf" / sub / "after.pdf", f"{sub} after"),
                              look_pages=[1]))
    return out


def test_the_visual_check_labels_same_named_samples_apart_and_files_findings_by_label(tmp_path):
    samples = two_same_named_samples(tmp_path)
    fake = FakeClient(resp(tool("submit_review", {"summary": "b lost its header", "findings": [
        {"severity": "block", "file": "b/Spec.docx", "page": 1, "text": "The header is gone."},
        {"severity": "warn", "file": "Spec.docx", "page": 1, "text": "Which one? A bare name is not a label."}]})))
    rv = vision.review_samples("REQUEST: x", samples, tmp_path / "review", lambda: fake)
    content = fake.requests[0]["messages"][0]["content"]
    labels = [b["text"] for b in content if b["type"] == "text"][:-1]
    assert labels == ["a/Spec.docx — page 1 — BEFORE", "a/Spec.docx — page 1 — AFTER",
                      "b/Spec.docx — page 1 — BEFORE", "b/Spec.docx — page 1 — AFTER"]
    assert rv.pages_sent == 4
    assert rv.findings == [{"severity": "block", "file": "b/Spec.docx", "page": 1, "text": "The header is gone."},
                           {"severity": "warn", "file": "", "page": None,
                            "text": "Which one? A bare name is not a label."}]
    # one sample alone (or names that differ) keeps its bare name, as before
    one = vision.review_samples("REQUEST: x", samples[:1], tmp_path / "review1",
                                lambda: FakeClient(resp(tool("submit_review", {"summary": "ok", "findings": []}))))
    assert one.pages_sent == 2 and not one.skipped


def test_the_preview_text_tells_same_named_samples_apart(tmp_path):
    samples = two_same_named_samples(tmp_path)
    samples[1].passed, samples[1].checks = False, {"V2": "FAIL"}
    text = GeneratedPreview(out_dir=tmp_path, name="op", samples=samples).to_text()
    assert "   a/Spec.docx: verification PASS" in text and "   b/Spec.docx: verification FAIL ['V2']" in text

    for sub, title in (("a", "ALPHA"), ("b", "BRAVO")):
        (tmp_path / "in" / sub).mkdir(parents=True)
        (tmp_path / "in" / sub / "Spec.docx").write_bytes(build("21 00 00", title))
    spec = load_spec(SPECS / "remove-all-headers-footers.json")
    pv = make_preview(spec, [str(tmp_path / "in" / "a"), str(tmp_path / "in" / "b")], tmp_path / "pv", do_render=False)
    text = pv.to_text()
    assert "   a/Spec.docx: verification PASS" in text and "   b/Spec.docx: verification PASS" in text
    single = make_preview(spec, [str(tmp_path / "in" / "a")], tmp_path / "pv1", do_render=False).to_text()
    assert "   Spec.docx: verification PASS" in single                  # a single folder reads as before


# ---- 4. review-6 residual: two more characters that render blank ---------------------------------------------------

@pytest.mark.parametrize("cp", [0x1D159, 0x16FE4])
def test_clean_wording_refuses_the_null_notehead_and_the_khitan_filler(cp):
    with pytest.raises(ValueError, match=hex(cp)):
        clean_wording("ISSUED" + chr(cp) + "FOR PERMIT")


@pytest.mark.parametrize("space", [" ", " ", " ", " ", "　", "\t"])
def test_clean_wording_keeps_spaces_that_show_as_spaces(space):
    w = f"NFPA{space}13 (2025)"
    assert clean_wording(w) == w


# ---- 5. review-24 minor: no empty approved words --------------------------------------------------------------------

def test_approved_words_hold_no_empty_source_and_trace_nothing_to_one(folder, tmp_path):
    c = Conversation("remove all headers and footers", sorted(folder.glob("*.docx")), client=FakeClient(),
                     answers=["sections 1-3 only", "", "   "])
    rec = json.loads(c.save(tmp_path / "conversation.json").read_text(encoding="utf-8"))
    assert rec["approved_words"] == c.sources == ["remove all headers and footers", "sections 1-3 only"]
    placing = {"request": "r", "summary": "s", "mode": "content", "ops": [{"op": "replace_text",
               "select": {"scope": ["document"]}, "params": {"find": "FM Global", "replace": "{}", "tracked": True,
                                                            "author": "x"}}]}
    assert untraceable(placing, c.sources) == ["{}"]            # "{}" was a source: it traced the words "{}"
    (folder / "PROJECT.json").write_text("{}", encoding="utf-8")
    c = Conversation("remove all headers and footers", sorted(folder.glob("*.docx")), client=FakeClient())
    assert c.sources == ["remove all headers and footers"]
    (folder / "PROJECT.json").write_text(json.dumps({"stamp": "ISSUED FOR BID"}), encoding="utf-8")
    c = Conversation("remove all headers and footers", sorted(folder.glob("*.docx")), client=FakeClient())
    assert len(c.sources) == 2 and "ISSUED FOR BID" in c.sources[1] and "PROJECT.json" not in c.sources[1]


# ---- 6. the verifier sorts None beside text without raising, and reports exactly as before ---------------------------

def mutate(data: bytes, member: str, fn) -> bytes:
    src, out = zipfile.ZipFile(io.BytesIO(data)), io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for info in src.infolist():
            b = src.read(info.filename)
            if info.filename == member:
                root = etree.fromstring(b)
                fn(root)
                b = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
            z.writestr(info, b)
    return out.getvalue()


def text_paragraphs(root):
    return [p for p in root.find(W + "body").findall(W + "p") if p.find(".//" + W + "t") is not None]


def set_text(p, text):
    ts = list(p.iter(W + "t", W + "delText", W + "instrText", W + "delInstrText"))
    ts[0].text = text
    for t in ts[1:]:
        t.text = ""


def new_paragraph(text):
    p = etree.Element(W + "p")
    etree.SubElement(etree.SubElement(p, W + "r"), W + "t").text = text
    return p


def status(rec, cid):
    return next(c for c in rec.checks if c.id == cid)


def test_v2_compares_a_part_mixing_an_inserted_and_a_changed_block_and_still_fails_a_mismatch():
    src = mutate(build("21 05 00", "COMMON WORK RESULTS"), DOC, lambda r: set_text(text_paragraphs(r)[2], "ALPHA TEXT"))

    def edit(root):
        ps = text_paragraphs(root)
        set_text(ps[2], "ALPHA TEXT CHANGED")
        ps[8].addnext(new_paragraph("BRAND NEW"))
    out = mutate(src, DOC, edit)
    changed, inserted = (DOC, "ALPHA TEXT", "ALPHA TEXT CHANGED"), (DOC, None, "BRAND NEW")

    def v2(*diffs):
        plan = {"job": "x", "mode": "formatting", "untracked_diffs_expected": True, "intended_parts": [DOC],
                "rev_delta": {}, "diffs": [{"part": p, "before": b, "after": a} for p, b, a in diffs]}
        return status(run_suite(src, out, plan, "s", "o"), "V2")      # raised TypeError before the fix

    nothing = v2()
    assert nothing.status == "FAIL" and nothing.data["missing"] == []
    assert sorted(nothing.data["unpredicted"], key=str) == sorted([changed, inserted], key=str)
    for diffs in ([changed, inserted], [inserted, changed]):           # predicted exactly, in either order
        ok = v2(*diffs)
        assert ok.status == "PASS" and ok.data["missing"] == [] and ok.data["unpredicted"] == []
    half = v2(changed)
    assert half.status == "FAIL" and half.data["missing"] == [] and half.data["unpredicted"] == [inserted]
    wrong = v2(changed, (DOC, None, "BRAND OLD"))
    assert wrong.status == "FAIL" and wrong.data["missing"] == [(DOC, None, "BRAND OLD")]
    assert wrong.data["unpredicted"] == [inserted]
    removed = v2(changed, inserted, (DOC, "GONE", None))
    assert removed.status == "FAIL" and removed.data["missing"] == [(DOC, "GONE", None)]


def test_v4_and_v5b_report_revisions_without_an_id_beside_ones_with_one():
    src = build("21 05 00", "COMMON WORK RESULTS")

    def wrap(root):                                    # four insertions: two without w:id, two sharing id 9
        for p, rid in zip(text_paragraphs(root)[2:6], (None, None, "9", "9")):
            r = p.find(W + "r")
            ins = etree.Element(W + "ins")
            if rid is not None:
                ins.set(W + "id", rid)
            ins.set(W + "author", "x")
            r.addprevious(ins)
            ins.append(r)
    out = mutate(src, DOC, wrap)
    rec = run_suite(src, out, {"job": "x", "mode": "formatting", "intended_parts": [DOC], "rev_delta": {}, "diffs": []},
                    "s", "o")                          # raised TypeError (None beside "9") before the fix
    v4, v5b = status(rec, "V4"), status(rec, "V5b")
    assert v4.status == "FAIL" and v4.data["duplicate_ids"] == [None, "9"]
    assert v5b.status == "FAIL" and v5b.data["new_revision_ids"] == [(DOC, None), (DOC, "9")]
