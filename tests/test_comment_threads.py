"""reply_to_comments, resolve_comments and V10's ``comment_replies`` / ``resolved_comments`` kinds (built-in batch 6, item
F8 — docs/BUILTIN_OPS_BATCH6_DESIGN.md §9): replies beside their thread's marks and linked in commentsExtended (created
when missing), threads resolved and reopened on their top comment's entry; with and without the side parts, Word's
mark layout and python-docx's, Word 2007's comments without paragraph ids; a wrong OUTPUT per rule; wrong
IMPLEMENTATIONS shared by plan and apply; refusals with their codes; parameters; plan-time conflicts; the catalog and
the user's words; the batch pipeline and a LibreOffice render."""
from __future__ import annotations

import ast
import io
import shutil
import zipfile
from pathlib import Path
from typing import List, Tuple

import pytest
from lxml import etree

from fx_comments import (CEX, CID, COMMENTS, DOC, W14, W15, build_classic_comments, build_commented, build_plain,
                         build_word_threads, para_id)
from make_fixtures import W
from spec_surgeon.catalog import COMMON_ASSUMPTIONS, REQUEST_ONLY_OPS, op_catalog
from spec_surgeon.ops import JobException, get_op, merge_fragments
from spec_surgeon.ops import comment_threads as ct
from spec_surgeon.package import DocxPackage, serialize_xml
from spec_surgeon.report import FAILURE_EXPLANATIONS
from spec_surgeon.verify import run_suite

EXTENDED, IDS, EXTENSIBLE = "word/commentsExtended.xml", "word/commentsIds.xml", "word/commentsExtensible.xml"
P14, P15, PC, PX = "{%s}" % W14, "{%s}" % W15, "{%s}" % CID, "{%s}" % CEX
RELS = "word/_rels/document.xml.rels"
REPLY = {"text": "Revised in 2.3 — see the new wording.", "author": "Abe Borg", "initials": "AB"}


@pytest.fixture(scope="module")
def src() -> bytes:
    return build_commented()


def run_ops(data: bytes, specs: List[Tuple[str, dict, dict]]):
    pkg = DocxPackage(data)
    frags = [get_op(o).plan(pkg, sel, par) for o, sel, par in specs]
    plan = merge_fragments(frags)
    work = pkg.copy()
    for f in frags:
        get_op(f.op).apply(work, f)
    out = work.output_bytes()
    return plan, out, run_suite(data, out, plan.to_dict(), "src.docx", "out.docx")


def reply(data: bytes, **params):
    return run_ops(data, [("reply_to_comments", {}, {**REPLY, **params})])


def resolve(data: bytes, **params):
    return run_ops(data, [("resolve_comments", {}, params)])


def failed(rec):
    return [c.id for c in rec.checks if c.status == "FAIL"]


def v10(rec) -> str:
    c = next(c for c in rec.checks if c.id == "V10")
    return c.summary + " | " + " | ".join(c.data.get("errors", []))


def mutate(docx: bytes, part: str, fn) -> bytes:
    zin = zipfile.ZipFile(io.BytesIO(docx))
    root = etree.fromstring(zin.read(part))
    fn(root)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for info in zin.infolist():
            z.writestr(info, serialize_xml(root) if info.filename == part else zin.read(info.filename))
    return buf.getvalue()


def xml(data: bytes, part: str):
    return etree.fromstring(zipfile.ZipFile(io.BytesIO(data)).read(part))


def names(data: bytes) -> List[str]:
    return zipfile.ZipFile(io.BytesIO(data)).namelist()


def marks(data: bytes, starts_with: str) -> List[str]:
    """The comment marks of the body paragraph starting with ``starts_with``, in order: S7 (start), E7 (end), R7
    (reference)."""
    tag = {"commentRangeStart": "S", "commentRangeEnd": "E", "commentReference": "R"}
    for p in xml(data, DOC).iter(W + "p"):
        if "".join(t.text or "" for t in p.iter(W + "t")).startswith(starts_with):
            return [tag[etree.QName(m).localname] + m.get(W + "id")
                    for m in p.iter(*(W + t for t in tag))]
    raise AssertionError(starts_with)


def entries(data: bytes) -> List[Tuple[str, str, str]]:
    return [(e.get(P15 + "paraId"), e.get(P15 + "paraIdParent"), e.get(P15 + "done"))
            for e in xml(data, EXTENDED).findall(P15 + "commentEx")]


def comments(data: bytes) -> List[Tuple[str, str, str]]:
    return [(c.get(W + "id"), c.get(W + "author"), "".join(t.text or "" for t in c.iter(W + "t")))
            for c in xml(data, COMMENTS).findall(W + "comment")]


def _caught(src, plan, out, part, fn, needle=None, check="V10"):
    bad = mutate(out, part, fn)
    rec = run_suite(src, bad, plan.to_dict(), "src.docx", "out.docx")
    assert check in failed(rec), rec.to_markdown()
    if needle:
        assert needle in v10(rec), v10(rec)


# ---- replies ---------------------------------------------------------------------------------------------------------

def test_a_reply_to_every_thread_beside_its_marks(src):
    """Threads 0 (with reply 1), 2 (with reply 3), 4 (over two paragraphs), 5 and 6 (starting between paragraphs) each get
    one reply: a comment with the user's words, linked to the top comment's last paragraph, its marks after the thread's
    last start and last reference."""
    plan, out, rec = reply(src)
    assert rec.passed, rec.to_markdown()
    assert plan.property_assertions[0]["expected_threads"] == ["0", "2", "4", "5", "6"]
    assert comments(out)[-5:] == [(str(i), "Abe Borg", REPLY["text"]) for i in range(7, 12)]
    assert all(c.get(W + "initials") == "AB" and c.get(W + "date") is None
               for c in xml(out, COMMENTS).findall(W + "comment")[-5:])
    new = entries(out)[7:]
    assert [(up, done) for _, up, done in new] == [(para_id(t), "0") for t in (0, 2, 4, 5, 6)]
    pids = [pid for pid, _, _ in new]
    assert [c.find(W + "p").get(P14 + "paraId") for c in xml(out, COMMENTS).findall(W + "comment")[-5:]] == pids
    ids = xml(out, IDS).findall(PC + "commentId")[7:]
    assert [e.get(PC + "paraId") for e in ids] == pids
    assert [e.get(PX + "durableId") for e in xml(out, EXTENSIBLE).findall(PX + "commentExtensible")[7:]] == \
        [e.get(PC + "durableId") for e in ids]
    assert marks(out, "A.") == ["S0", "S1", "S7", "E1", "R1", "E0", "R0", "E7", "R7"]
    assert marks(out, "C.") == ["S4", "S9"] and marks(out, "D.") == ["E4", "R4", "E9", "R9"]
    assert marks(out, "F.") == ["S11", "E6", "R6", "E11", "R11"]          # comment 6 starts between paragraphs
    assert names(out) == names(src)
    assert any("thread is marked resolved" in n for n in plan.notes)        # thread 0


def test_replies_follow_words_layout_of_a_thread():
    data = build_word_threads()
    assert marks(data, "A.") == ["S0", "S1", "E0", "R0", "E1", "R1"]
    plan, out, rec = reply(data, which="authors", authors=["JACK"])
    assert rec.passed, rec.to_markdown()
    assert marks(out, "A.") == ["S0", "S1", "S7", "E0", "R0", "E1", "R1", "E7", "R7"]
    assert marks(out, "B.") == ["S2", "S3", "S8", "E2", "R2", "E3", "R3", "E8", "R8"]
    # Jack wrote reply 1 in Jeremy's thread 0 and comment 2: a reply to a reply is a reply to its thread
    assert plan.property_assertions[0]["expected_threads"] == ["0", "2"]
    assert [up for _, up, _ in entries(out)[7:]] == [para_id(0), para_id(2)]


def test_a_reply_chosen_by_its_text(src):
    plan, out, rec = reply(src, which="text_regex", text_regex=r"spans C\b")
    assert rec.passed, rec.to_markdown()
    assert plan.property_assertions[0]["expected_threads"] == ["4"]
    assert comments(out)[-1] == ("7", "Abe Borg", REPLY["text"])
    plan, out, rec = reply(src, which="text_regex", text_regex="and its valve")    # pending inserted text reads too
    assert rec.passed and plan.property_assertions[0]["expected_threads"] == ["5"]


def test_without_side_parts_the_threads_part_is_created():
    data = build_commented(side=False)
    plan, out, rec = run_ops(data, [("reply_to_comments", {}, {"text": "Moved.", "author": "Abe", "which": "authors",
                                                               "authors": ["Abe"]})])
    assert rec.passed, rec.to_markdown()
    assert plan.members_added == [EXTENDED] and names(out) == names(data) + [EXTENDED]
    pid = xml(out, COMMENTS).findall(W + "comment")[-1].find(W + "p").get(P14 + "paraId")
    assert entries(out) == [(para_id(5), None, "0"), (pid, para_id(5), "0")]
    rel = [r for r in xml(out, RELS) if r.get("Target") == "commentsExtended.xml"]
    assert len(rel) == 1 and rel[0].get("Type").endswith("/commentsExtended")
    ct_root = xml(out, "[Content_Types].xml")
    assert any(o.get("PartName") == "/" + EXTENDED for o in ct_root)
    assert xml(out, COMMENTS).findall(W + "comment")[-1].get(W + "initials") is None
    assert rec.get("V5a").status == "PASS"                                   # the new part is schema-valid


def test_word_2007_comments_get_their_paragraph_ids():
    data = build_classic_comments()
    assert not any(p.get(P14 + "paraId") for p in xml(data, COMMENTS).iter(W + "p"))
    plan, out, rec = reply(data, which="authors", authors=["Jack"])
    assert rec.passed, rec.to_markdown()
    tops = {c.get(W + "id"): c for c in xml(out, COMMENTS).findall(W + "comment")}
    up1, up2 = (list(tops[i].iter(W + "p"))[-1].get(P14 + "paraId") for i in ("1", "2"))
    assert up1 and up2 and tops["0"].find(W + "p").get(P14 + "paraId") is None       # only the threads replied to
    assert [(up, done) for _, up, done in entries(out)] == [(None, "0"), (up1, "0"), (None, "0"), (up2, "0")]


def test_no_thread_to_reply_to(src):
    with pytest.raises(JobException) as e:
        get_op("reply_to_comments").plan(DocxPackage(src), {}, {**REPLY, "which": "authors", "authors": ["Nobody"]})
    assert e.value.code == "not-found"
    with pytest.raises(JobException) as e:
        get_op("reply_to_comments").plan(DocxPackage(build_plain()), {}, REPLY)
    assert e.value.code == "not-found"
    plan, out, rec = reply(src, which="authors", authors=["Nobody"], allow_missing=True)
    assert rec.passed and out == src


# ---- resolving and reopening ----------------------------------------------------------------------------------------

def test_resolving_every_thread_marks_the_top_comments(src):
    plan, out, rec = resolve(src)
    assert rec.passed, rec.to_markdown()
    assert plan.property_assertions[0]["expected_changed"] == ["2", "4", "5", "6"]     # 0 already resolved
    assert entries(out) == [(para_id(0), None, "1"), (para_id(1), para_id(0), "1"), (para_id(2), None, "1"),
                            (para_id(3), para_id(2), "0"), (para_id(4), None, "1"), (para_id(5), None, "1"),
                            (para_id(6), None, "1")]                                    # reply 3 left as it was
    assert xml(out, COMMENTS) is not None and zipfile.ZipFile(io.BytesIO(out)).read(COMMENTS) == \
        zipfile.ZipFile(io.BytesIO(src)).read(COMMENTS)
    assert plan.intended_parts == [EXTENDED] and plan.mode == "formatting"


def test_reopening_and_threads_already_as_asked(src):
    plan, out, rec = resolve(src, action="reopen")
    assert rec.passed, rec.to_markdown()
    assert plan.property_assertions[0]["expected_changed"] == ["0"]
    assert entries(out)[:2] == [(para_id(0), None, "0"), (para_id(1), para_id(0), "1")]
    plan, out, rec = resolve(src, which="authors", authors=["Jack"])      # threads 0 (resolved) and 2
    assert rec.passed and plan.property_assertions[0]["expected_changed"] == ["2"]
    assert any("already resolved" in n for n in plan.notes)
    plan, again, rec = resolve(out, which="authors", authors=["Jack"])
    assert rec.passed and again == out


def test_resolving_without_side_parts_and_paragraph_ids():
    data = build_classic_comments()
    plan, out, rec = resolve(data, which="text_regex", text_regex="Galvanized|location")
    assert rec.passed, rec.to_markdown()
    assert plan.members_added == [EXTENDED]
    pids = {c.get(W + "id"): list(c.iter(W + "p"))[-1].get(P14 + "paraId")
            for c in xml(out, COMMENTS).findall(W + "comment")}
    assert entries(out) == [(pids["2"], None, "1"), (pids["5"], None, "1")]
    assert all(pids[i] is None for i in ("0", "1", "3", "4", "6"))
    plan, out, rec = resolve(data, action="reopen")                        # nothing resolved: nothing to do
    assert rec.passed and out == data


def test_an_entry_without_a_done_mark_is_open(src):
    data = mutate(src, EXTENDED, lambda r: r.findall(P15 + "commentEx")[2].attrib.pop(P15 + "done"))
    plan, out, rec = resolve(data, which="authors", authors=["Jack"])
    assert rec.passed, rec.to_markdown()
    assert entries(out)[2] == (para_id(2), None, "1")


# ---- a wrong output is caught ----------------------------------------------------------------------------------------

def test_a_wrong_reply_is_caught(src):
    plan, out, rec = reply(src, which="authors", authors=["Jack"])
    assert rec.passed

    def words(root):
        root.findall(W + "comment")[-1].find(f"{W}p/{W}r/{W}t").text = "Something else."
    _caught(src, plan, out, COMMENTS, words, check="V2")
    _caught(src, plan, out, COMMENTS, words, "not the source with exactly the replies")
    _caught(src, plan, out, EXTENDED, lambda r: r.findall(P15 + "commentEx")[-2].set(P15 + "paraIdParent", para_id(1)),
            "exactly the replies' entries")
    _caught(src, plan, out, IDS, lambda r: r.remove(r.findall(PC + "commentId")[-1]), "exactly the replies' entries")


def test_misplaced_marks_are_caught(src):
    plan, out, rec = reply(src, which="authors", authors=["Jack"])

    def no_reference(root):
        ref = next(m for m in root.iter(W + "commentReference") if m.get(W + "id") == "7")
        ref.getparent().getparent().remove(ref.getparent())
    _caught(src, plan, out, DOC, no_reference, check="V3")
    _caught(src, plan, out, DOC, no_reference, "not exactly the predicted change")

    def start_later(root):
        start = next(m for m in root.iter(W + "commentRangeStart") if m.get(W + "id") == "7")
        run = start.getnext()
        start.getparent().remove(start)
        run.addnext(start)
    _caught(src, plan, out, DOC, start_later, "not exactly the predicted change")


def test_a_wrong_resolution_is_caught(src):
    plan, out, rec = resolve(src, which="authors", authors=["Jack"])
    assert rec.passed
    _caught(src, plan, out, EXTENDED, lambda r: r.findall(P15 + "commentEx")[3].set(P15 + "done", "1"),
            "not the source with exactly those threads resolved")
    _caught(src, plan, out, EXTENDED, lambda r: r.findall(P15 + "commentEx")[4].set(P15 + "done", "1"),
            "not the source with exactly those threads resolved")


# ---- a wrong implementation shared by plan and apply is caught -------------------------------------------------------

def test_a_shared_choice_that_ignores_the_authors_is_caught(src, monkeypatch):
    real = ct.chosen_threads
    monkeypatch.setattr(ct, "chosen_threads", lambda book, which, authors, rx: real(book, "all", [], None))
    plan, out, rec = reply(src, which="authors", authors=["Jack"])
    assert len(plan.property_assertions[0]["expected_threads"]) == 5
    assert "V10" in failed(rec) and "chosen here" in v10(rec)


def test_a_reply_to_a_reply_is_refused_by_the_op_itself(src, monkeypatch):
    """A choice that takes a reply for a thread's top finds no marks of a thread under it: the op refuses before any
    output exists."""
    def unthreaded(book, which, authors, rx):
        return [c for c in book.comments if c.id == "1"]
    monkeypatch.setattr(ct, "chosen_threads", unthreaded)
    _refused(src, "reply_to_comments", REPLY, "comment-anchor")


def test_shared_ids_that_reuse_a_paragraph_id_are_caught(src, monkeypatch):
    monkeypatch.setattr(ct.NewIds, "hex", lambda self: para_id(6))
    plan, out, rec = reply(src, which="authors", authors=["Abe"])
    assert "V10" in failed(rec) and "exactly the replies" in v10(rec)


def test_a_shared_place_after_the_first_reference_is_caught(src, monkeypatch):
    real = ct.thread_marks

    def first(parts, book, top):
        m = real(parts, book, top)
        if top.id == "0":
            m = {**m, "ref": ["1", 0]}                                  # python-docx nests reply 1's reference first
        return m
    monkeypatch.setattr(ct, "thread_marks", first)
    plan, out, rec = reply(src, which="authors", authors=["Jeremy"])
    assert "V10" in failed(rec) and "not exactly the predicted change" in v10(rec)


def test_a_shared_choice_that_resolves_replies_is_caught(src, monkeypatch):
    monkeypatch.setattr(ct, "chosen_threads", lambda book, which, authors, rx: list(book.comments))
    plan, out, rec = resolve(src, action="reopen")
    assert "V10" in failed(rec) and "change here" in v10(rec)


# ---- refusals --------------------------------------------------------------------------------------------------------

def _refused(data, op, params, code):
    with pytest.raises(JobException) as e:
        get_op(op).plan(DocxPackage(data), {}, params)
    assert e.value.code == code, e.value.reason
    return e.value


def test_refusals_of_a_thread(src):
    _refused(build_commented(duplicate=True), "reply_to_comments", REPLY, "structure")
    _refused(build_commented(duplicate=True), "resolve_comments", {}, "structure")
    # comment 2's thread has a second reference in a text box: no single place for its reply
    _refused(build_commented(textbox=True), "reply_to_comments", {**REPLY, "which": "authors", "authors": ["Jack"]},
             "comment-anchor")

    def only_in_box(root):                       # its only reference: the first copy of the text box (Word keeps two)
        refs = [m for m in root.iter(W + "commentReference") if m.get(W + "id") == "2"]
        boxed = [m for m in refs if any(a.tag == W + "txbxContent" for a in m.iterancestors())]
        for m in refs:
            if m is not boxed[0]:
                m.getparent().getparent().remove(m.getparent())
    boxed = mutate(build_commented(textbox=True), DOC, only_in_box)
    _refused(boxed, "reply_to_comments", {**REPLY, "which": "authors", "authors": ["Jack"]}, "textbox")

    def no_reference(root):
        ref = next(m for m in root.iter(W + "commentReference") if m.get(W + "id") == "5")
        ref.getparent().getparent().remove(ref.getparent())
    _refused(mutate(src, DOC, no_reference), "reply_to_comments", {**REPLY, "which": "authors", "authors": ["Abe"]},
             "comment-anchor")

    def start_after(root):
        start = next(m for m in root.iter(W + "commentRangeStart") if m.get(W + "id") == "5")
        p = start.getparent()
        p.remove(start)
        p.append(start)
    _refused(mutate(src, DOC, start_after), "reply_to_comments", {**REPLY, "which": "authors", "authors": ["Abe"]},
             "comment-anchor")
    # the other threads are not reached by these defects
    assert reply(mutate(src, DOC, start_after), which="authors", authors=["Jack"])[2].passed


def test_refusals_of_the_side_parts(src):
    no_ids = mutate(src, RELS, lambda r: r.remove(next(x for x in r if x.get("Target") == "commentsIds.xml")))
    for op, params in (("reply_to_comments", REPLY), ("resolve_comments", {})):
        _refused(no_ids, op, params, "comment-metadata")
    missing = mutate(src, IDS, lambda r: r.remove(r.findall(PC + "commentId")[0]))
    _refused(missing, "reply_to_comments", {**REPLY, "which": "authors", "authors": ["Jeremy"]}, "comment-metadata")
    assert reply(missing, which="authors", authors=["Abe"])[2].passed            # another thread is fine
    renamed = mutate(src, EXTENDED, lambda r: setattr(r, "tag", P15 + "people"))
    _refused(renamed, "resolve_comments", {}, "comment-metadata")


@pytest.mark.parametrize("params", [
    {"author": "Abe"}, {"text": "x"}, {"text": " ", "author": "Abe"}, {"text": "a\nb", "author": "Abe"},
    {"text": "x", "author": "Abe", "which": "everyone"}, {"text": "x", "author": "Abe", "authors": ["Jack"]},
    {"text": "x", "author": "Abe", "which": "authors"}, {"text": "x", "author": "Abe", "which": "authors", "authors": []},
    {"text": "x", "author": "Abe", "which": "text_regex", "text_regex": "("},
    {"text": "x", "author": "Abe", "tracked": True}, {"text": "x", "author": "Abe", "date": "2026-10-21"},
    {"text": "x", "author": "Abe", "initials": ""}, {"text": "x", "author": "Abe", "allow_missing": "yes"},
])
def test_reply_parameters_are_checked(src, params):
    with pytest.raises(JobException) as e:
        get_op("reply_to_comments").plan(DocxPackage(src), {}, params)
    assert e.value.code == "bad-params"


@pytest.mark.parametrize("select, params", [
    ({}, {"action": "close"}), ({}, {"tracked": True}), ({}, {"allow_missing": 1}), ({}, {"which": "resolved"}),
    ({}, {"text_regex": "x"}), ({"scope": ["document"]}, {}), ({}, {"done": True}),
])
def test_resolve_parameters_are_checked(src, select, params):
    with pytest.raises(JobException) as e:
        get_op("resolve_comments").plan(DocxPackage(src), select, params)
    assert e.value.code == "bad-params"


def test_every_refusal_code_is_explained():
    codes = set()
    for n in ast.walk(ast.parse(Path(ct.__file__).read_text(encoding="utf-8"))):
        if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "JobException" and len(n.args) > 1:
            assert isinstance(n.args[1], ast.Constant), n.lineno
            codes.add(n.args[1].value)
    codes.discard("internal")
    assert codes == {"bad-params", "comment-anchor", "comment-metadata", "not-found", "structure", "textbox"}
    assert sorted(c for c in codes if c not in FAILURE_EXPLANATIONS) == []


# ---- touches and conflicts (rule 9) ----------------------------------------------------------------------------------

def _plan(data, *specs):
    pkg = DocxPackage(data)
    return merge_fragments([get_op(o).plan(pkg, s, p) for o, s, p in specs])


@pytest.mark.parametrize("other", [
    ("remove_comments", {}, {"which": "resolved"}),
    ("resolve_comments", {}, {}),
    ("add_review_comments", {"paragraphs": {"text_regex": "^G\\."}}, {"text": "Check.", "author": "Abe"}),
    ("replace_text", {"scope": ["document"]}, {"find": "listed and approved", "replace": "listed", "tracked": False}),
    ("replace_text", {"scope": ["comments"]}, {"find": "Which listing?", "replace": "Which?", "tracked": False}),
])
def test_a_reply_beside_another_change_of_its_comments_conflicts(src, other):
    with pytest.raises(JobException) as e:
        _plan(src, ("reply_to_comments", {}, REPLY), other)
    assert e.value.code == "conflict"


def test_reply_then_resolve_in_one_request_run_as_two_verified_steps(src, tmp_path):
    """"Answer Jack's comments and resolve them": the two ops change the same threads, so the engine cuts the spec into
    two steps in the user's order, the second planned on the first one's output, each verified."""
    from spec_surgeon.chain import run_chain
    from spec_surgeon.editspec import split_into_stages, stage_cuts
    jack = {"which": "authors", "authors": ["Jack"]}
    spec = {"request": "r", "summary": "s", "mode": "untracked",
            "ops": [{"op": "reply_to_comments", "params": {**REPLY, **jack}},
                    {"op": "resolve_comments", "params": jack}]}
    pkg = DocxPackage(src, name="x.docx")
    assert stage_cuts(pkg, spec) == [1, 1]
    folder = tmp_path / "in"
    folder.mkdir()
    (folder / "21 13 13 – WET-PIPE SPRINKLER SYSTEMS.docx").write_bytes(src)
    r = run_chain([str(folder)], split_into_stages(pkg, spec), tmp_path / "run", workers=1, renderer="none",
                  do_render=False)
    assert r.counts()["PASS"] == 1, [(f.status, f.reason) for f in r.files]
    out = next((tmp_path / "run" / "outputs").glob("*.docx")).read_bytes()
    assert comments(out)[-2:] == [("7", "Abe Borg", REPLY["text"]), ("8", "Abe Borg", REPLY["text"])]
    assert entries(out)[2] == (para_id(2), None, "1") and entries(out)[-1][2] == "0"      # thread 2 resolved


def test_remove_comments_reads_the_threads_these_ops_write():
    """The replies are threaded and the resolved marks read as remove_comments reads them: removing the replier's
    comments takes the replies alone; removing resolved threads takes the threads resolved here, replies and all."""
    data = build_classic_comments()
    _, replied, rec = reply(data, which="authors", authors=["Jack"])
    assert rec.passed
    _, resolved, rec = resolve(replied, which="authors", authors=["Abe Borg"])
    assert rec.passed, rec.to_markdown()
    _, out, rec = run_ops(resolved, [("remove_comments", {}, {"which": "authors", "authors": ["abe borg"]})])
    assert rec.passed and [c[0] for c in comments(out)] == ["0", "1", "2", "3", "4", "5", "6"]
    _, out, rec = run_ops(resolved, [("remove_comments", {}, {"which": "resolved"})])
    assert rec.passed and [c[0] for c in comments(out)] == ["0", "3", "4", "5", "6"]


def test_resolving_beside_a_removal_conflicts_and_beside_an_edit_runs(src):
    with pytest.raises(JobException) as e:
        _plan(src, ("resolve_comments", {}, {}), ("remove_comments", {}, {"which": "authors", "authors": ["Abe"]}))
    assert e.value.code == "conflict"
    plan, out, rec = run_ops(src, [("resolve_comments", {}, {}),
                                   ("replace_text", {"scope": ["document"]},
                                    {"find": "No comment here", "replace": "Nothing here", "author": "Abe"})])
    assert rec.passed, rec.to_markdown()
    plan, out, rec = run_ops(src, [("reply_to_comments", {}, REPLY),
                                   ("replace_text", {"scope": ["document"]},
                                    {"find": "No comment here", "replace": "Nothing here", "tracked": False})])
    assert rec.passed, rec.to_markdown()


# ---- catalog, the user's words, pipeline, render ---------------------------------------------------------------------

def test_catalog_and_the_users_words():
    from spec_surgeon.compiler import placed_words
    from spec_surgeon.editspec import SpecError, validate_spec
    cat = op_catalog()
    for op in ("reply_to_comments", "resolve_comments"):
        assert op in cat["ops"] and COMMON_ASSUMPTIONS[op] and op in REQUEST_ONLY_OPS
    spec = {"request": "r", "summary": "s", "mode": "untracked",
            "ops": [{"op": "reply_to_comments", "params": {**REPLY, "which": "authors", "authors": ["Jack"]}}]}
    validate_spec(spec)
    assert placed_words(spec) == [REPLY["text"], REPLY["author"], REPLY["initials"]]
    with pytest.raises(SpecError):
        validate_spec({**spec, "ops": [{"op": "reply_to_comments", "params": {**REPLY, "tracked": True}}]})
    with pytest.raises(SpecError):
        validate_spec({**spec, "ops": [{"op": "resolve_comments", "params": {"action": "close"}}]})


def test_the_reply_s_author_and_initials_are_the_user_s_words_too():
    """Review of batch 6: only the reply's text was traced, so a reviewer name the model made up reached Word's comment
    balloon as the author of the user's reply."""
    from spec_surgeon.compiler import untraceable
    request = "Reply 'Revised in 2.3 — see the new wording.' to Jack's comments as Abe Borg, initials AB."
    spec = {"ops": [{"op": "reply_to_comments", "params": {**REPLY, "which": "authors", "authors": ["Jack"]}}]}
    assert untraceable(spec, [request]) == []
    invented = {"ops": [{"op": "reply_to_comments", "params": {**REPLY, "author": "Claude AI Reviewer",
                                                               "initials": "CAI"}}]}
    assert untraceable(invented, [request]) == ["Claude AI Reviewer", "CAI"]
    no_initials = {"ops": [{"op": "reply_to_comments", "params": {"text": REPLY["text"], "author": "Abe Borg"}}]}
    assert untraceable(no_initials, ["Reply 'Revised in 2.3 — see the new wording.' as Abe Borg"]) == []


def test_specs_run_through_the_batch_pipeline(src, tmp_path):
    from spec_surgeon.batch import run_batch
    path = tmp_path / "21 13 13 – WET-PIPE SPRINKLER SYSTEMS.docx"
    path.write_bytes(src)
    for n, (mode, ops) in enumerate((("untracked", [{"op": "reply_to_comments", "params": REPLY}]),
                                     ("formatting", [{"op": "resolve_comments", "params": {}}]))):
        spec = {"request": "r", "summary": "s", "mode": mode, "ops": ops}
        batch = run_batch([str(path)], "edit-spec", {}, tmp_path / f"out{n}", workers=1, renderer="none",
                          do_render=False, spec=spec)
        f = batch.files[0]
        assert f.status == "PASS", f.record.to_markdown() if f.record else f.exception


@pytest.mark.render
@pytest.mark.skipif(shutil.which("soffice") is None, reason="LibreOffice not installed")
def test_libreoffice_opens_and_renders_the_output(tmp_path):
    from spec_surgeon.render import libreoffice
    _, out, rec = reply(build_commented(side=False))
    assert rec.passed, rec.to_markdown()
    _, out, rec = resolve(out, which="authors", authors=["Jack"])
    assert rec.passed, rec.to_markdown()
    (tmp_path / "out.docx").write_bytes(out)
    pdfs = libreoffice.render_pdfs([tmp_path / "out.docx"], tmp_path / "pdf", workers=1)
    if len(pdfs) != 1:
        pytest.skip("LibreOffice did not render here")
    pdf = pdfs[tmp_path / "out.docx"]
    assert pdf.exists() and pdf.stat().st_size > 1000 and pdf.read_bytes().startswith(b"%PDF")
