"""remove_hidden_text and remove_notes (docs/BUILTIN_OPS_BATCH2_DESIGN.md §5, §6): end to end tracked and untracked (the
whole suite must pass), every refusal, the container rules (the last section keeps a paragraph too) and the field
rules, nothing-to-do files, and planted defects the verifier must catch — a wrong output, and a wrong rule shared by
plan and apply."""
from __future__ import annotations

from typing import Any, Dict, List

import pytest
from lxml import etree

from fx_removal import W, build_hidden, build_hidden_case, build_last_section, build_notes
from spec_surgeon.editspec import apply_spec, plan_spec
from spec_surgeon.ops import JobException, get_op, merge_fragments
from spec_surgeon.ops import hidden as hidden_module
from spec_surgeon.package import DocxPackage
from spec_surgeon.verify import run_suite
from test_text_edits import accepted_text, failed, mutate, v10_errors

AUTHOR = "Abraham Borg"
DOC = "word/document.xml"


def run_op(name: str, src: bytes, params: Dict[str, Any], select: Dict[str, Any] = None):
    pkg = DocxPackage(src)
    frag = get_op(name).plan(pkg, select or {}, {"author": AUTHOR, **params})
    plan = merge_fragments([frag])
    work = pkg.copy()
    get_op(name).apply(work, frag)
    out = work.output_bytes()
    return plan, out, run_suite(src, out, plan.to_dict(), "src.docx", "out.docx")


def texts(data: bytes, part: str = DOC) -> List[str]:
    """What a reader sees with every change accepted, paragraph by paragraph (deleted paragraphs gone)."""
    root = DocxPackage(data).xml(part)
    out = []
    for p in root.iter(W + "p"):
        mark = p.find(f"{W}pPr/{W}rPr/{W}del")
        t = accepted_text(p)
        if mark is None or t:
            out.append(t)
    return out


def all_text(data: bytes, part: str = DOC) -> str:
    return " | ".join(texts(data, part))


def hidden_runs(data: bytes, part: str = DOC) -> List[str]:
    """Live runs that still carry w:vanish directly or through SecretChar (the fixture's two ways)."""
    root = DocxPackage(data).xml(part)
    out = []
    for r in root.iter(W + "r"):
        if any(a.tag in (W + "del", W + "moveFrom") for a in r.iterancestors()):
            continue
        rpr = r.find(W + "rPr")
        style = rpr.find(W + "rStyle") if rpr is not None else None
        if rpr is not None and (rpr.find(W + "vanish") is not None or
                                (style is not None and style.get(W + "val") == "SecretChar")):
            if "".join(t.text or "" for t in r.findall(W + "t")).strip():
                out.append("".join(t.text or "" for t in r.findall(W + "t")))
    return out


# ---- remove_hidden_text ----------------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def hidden_doc() -> bytes:
    return build_hidden()


@pytest.mark.parametrize("tracked", [True, False])
def test_hidden_text_end_to_end(hidden_doc, tracked):
    plan, out, rec = run_op("remove_hidden_text", hidden_doc, {"tracked": tracked})
    assert rec.passed, rec.to_markdown()
    body = all_text(out)
    for gone in ("secret words", "char-styled secret", "Specifier: this whole paragraph is hidden.", "hidden inside",
                 "the portal", "hidden cell note"):
        assert gone not in body
    for stays in ("Visible lead.  visible tail.", "Plain text ", "BeforeAfter", "Index entry here",
                  "Shown mark hidden", "Inserted: kept ", "See  now.", "Cell text", "END OF SECTION"):
        assert stays in body, body
    assert "hidden header words" not in all_text(out, _header(out))
    # every removed piece is listed for the preview
    listed = " ".join(plan.flags)
    for piece in ("«secret words»", "«char-styled secret»", "«Specifier: this whole paragraph is hidden.»", "«⇥»",
                  "«hidden inside»", "«the portal»", "«hidden header words»"):
        assert piece in listed, listed
    assert any("kept its paragraph mark" in f for f in plan.flags)               # the cell's only paragraph
    assert hidden_runs(out) == []                                               # nothing hidden is live any more
    assert "«{XE \"Sprinkler\"}»" in listed                                      # a removed field named by its code
    if tracked:
        assert plan.rev_delta["del_mark"] == 2 and plan.rev_delta["del"] >= 8
        assert rec.get("V8").status == "PASS"
    else:
        assert any(d.structure_only for d in plan.diffs)                         # the hidden tab: structure only
        assert len(list(DocxPackage(out).xml(DOC).iter(W + "p"))) == len(list(DocxPackage(hidden_doc).xml(DOC).iter(W + "p"))) - 2


def _header(data: bytes) -> str:
    return next(p for p in DocxPackage(data).text_parts() if "header" in p)


def test_hidden_text_through_a_spec_and_its_preview_list(hidden_doc):
    spec = {"request": "remove the hidden text", "summary": "s", "mode": "untracked",
            "ops": [{"op": "remove_hidden_text", "params": {"tracked": False, "author": AUTHOR}}]}
    pkg = DocxPackage(hidden_doc)
    plan, frags = plan_spec(pkg, spec)
    out = apply_spec(pkg, frags).output_bytes()
    rec = run_suite(hidden_doc, out, plan.to_dict(), "s.docx", "o.docx")
    assert rec.passed, rec.to_markdown()
    from spec_surgeon.planner import summarize_plan
    lines = summarize_plan(plan)
    assert any("«secret words»" in x for x in lines) and any(x.startswith("structure:") for x in lines)


def test_nothing_hidden_is_a_no_op_by_default_and_not_found_when_asked():
    src = build_hidden_case("plain")
    plan, out, rec = run_op("remove_hidden_text", src, {})
    assert rec.passed and out == src
    assert any("nothing to do" in n for n in plan.notes)
    with pytest.raises(JobException) as e:
        run_op("remove_hidden_text", src, {"allow_missing": False})
    assert e.value.code == "not-found"


@pytest.mark.parametrize("kind,tracked,code", [("ambiguous", True, "toggle-ambiguous"),
                                               ("partial-field", False, "field-result"),
                                               ("textbox", False, "textbox"),
                                               ("simple", True, "field-result")])
def test_hidden_text_refusals(kind, tracked, code):
    with pytest.raises(JobException) as e:
        run_op("remove_hidden_text", build_hidden_case(kind), {"tracked": tracked})
    assert e.value.code == code


def test_a_hidden_simple_field_goes_whole_untracked():
    src = build_hidden_case("simple")
    plan, out, rec = run_op("remove_hidden_text", src, {"tracked": False})
    assert rec.passed, rec.to_markdown()
    root = DocxPackage(out).xml(DOC)
    assert root.find(f".//{W}fldSimple") is None and "Title:  end." in all_text(out)


def test_a_hidden_paragraph_with_a_section_break_keeps_its_mark():
    src = build_hidden_case("section")
    for tracked in (True, False):
        plan, out, rec = run_op("remove_hidden_text", src, {"tracked": tracked})
        assert rec.passed, rec.to_markdown()
        assert any("section break" in f for f in plan.flags)
        root = DocxPackage(out).xml(DOC)
        assert len(list(root.iter(W + "sectPr"))) == 2                    # both sections are still there
        assert "hidden end of the first section" not in all_text(out)


def test_scope_leaves_the_header_alone_when_asked(hidden_doc):
    plan, out, rec = run_op("remove_hidden_text", hidden_doc, {"tracked": False}, {"scope": ["document"]})
    assert rec.passed
    assert "hidden header words" in all_text(out, _header(out))


# ---- planted defects: remove_hidden_text -------------------------------------------------------------------------

def test_defect_a_hidden_run_left_in_place(hidden_doc):
    plan, out, rec = run_op("remove_hidden_text", hidden_doc, {"tracked": True})

    def undelete(root):
        for d in root.iter(W + "del"):
            r = d.find(W + "r")
            if d.get(W + "author") == AUTHOR and r is not None and "".join(
                    t.text or "" for t in r.iter(W + "delText")) == "secret words":
                for t in r.findall(W + "delText"):
                    t.tag = W + "t"
                d.addprevious(r)
                d.getparent().remove(d)
                return
        raise AssertionError("no deleted secret")
    rec = run_suite(hidden_doc, mutate(out, DOC, undelete), plan.to_dict(), "s", "o")
    assert "V10" in failed(rec)
    assert any("removed_hidden_text" in e for e in v10_errors(rec))


def test_defect_a_visible_run_removed_too(hidden_doc):
    plan, out, rec = run_op("remove_hidden_text", hidden_doc, {"tracked": False})

    def drop_tail(root):
        for t in root.iter(W + "t"):
            if t.text == " visible tail.":
                r = t.getparent()
                r.getparent().remove(r)
                return
        raise AssertionError("no tail")
    rec = run_suite(hidden_doc, mutate(out, DOC, drop_tail), plan.to_dict(), "s", "o")
    assert "V2" in failed(rec) and "V10" in failed(rec)


def test_shared_defect_a_visible_run_counted_as_hidden(hidden_doc, monkeypatch):
    real = hidden_module.RemoveHiddenText.choose

    def wrong(self, part, bi, p, look, sheet, params):
        whole, runs = real(self, part, bi, p, look, sheet, params)
        tail = [r for r in hidden_module.content_runs(p)
                if "".join(t.text or "" for t in r.findall(W + "t")) == " visible tail."]
        return whole, runs + tail
    monkeypatch.setattr(hidden_module.RemoveHiddenText, "choose", wrong)
    plan, out, rec = run_op("remove_hidden_text", hidden_doc, {"tracked": False})
    assert " visible tail." not in all_text(out)                         # plan and apply agree …
    assert failed(rec) == ["V10"], rec.to_markdown()                    # … the verifier does not
    assert any("removed_hidden_text" in e and "the plan predicted" in e for e in v10_errors(rec))


def test_shared_defect_a_cell_left_without_its_last_paragraph(hidden_doc, monkeypatch):
    monkeypatch.setattr(hidden_module, "keep_rules", lambda blocks, chosen, tracked: [])
    plan, out, rec = run_op("remove_hidden_text", hidden_doc, {"tracked": False})
    assert "V10" in failed(rec)
    assert any("removed_hidden_text" in e for e in v10_errors(rec))


def test_shared_defect_a_partly_hidden_field_cut(monkeypatch):
    monkeypatch.setattr(hidden_module, "apply_field_rules", lambda p, chosen, spans, tracked, where: (list(chosen), []))
    src = build_hidden_case("partial-field")
    plan, out, rec = run_op("remove_hidden_text", src, {"tracked": False})
    assert "V10" in failed(rec)
    assert any("the op must refuse" in e for e in v10_errors(rec))


# ---- remove_notes ----------------------------------------------------------------------------------------------------

NOTE_RULES = {"rules": [{"style_in": ["CMT"]}, {"color_in": ["0000FF"]}, {"text_regex": r"^Delete\b"}],
              "runs": {"color_in": ["0000ff"]}}


@pytest.fixture(scope="module")
def notes_doc() -> bytes:
    return build_notes()


@pytest.mark.parametrize("tracked", [True, False])
def test_notes_end_to_end(notes_doc, tracked):
    plan, out, rec = run_op("remove_notes", notes_doc, {**NOTE_RULES, "tracked": tracked})
    assert rec.passed, rec.to_markdown()
    body = texts(out)
    assert "Retain paragraph below if the project has a fire pump." not in body
    assert "Coordinate with the Owner's insurer." not in body
    assert "Delete this paragraph when the system is wet-pipe only." not in body
    assert "Mixed: black and " in body                                  # not all blue: the blue run is inline
    assert "Pipe shall be  Schedule 40." in body
    assert "See the portal for data." in body                           # link text is never an inline note
    assert "Edit the value to suit." not in body and "Item" in body     # the cell keeps an empty paragraph
    listed = " ".join(plan.flags)
    for piece in ("«Retain paragraph below if the project has a fire pump.»", "«[Verify size]»", "«blue»",
                  "«Coordinate with the Owner's insurer.»"):
        assert piece in listed
    assert any("kept its paragraph mark" in f for f in plan.flags)


def test_note_rules_and_within_a_rule(notes_doc):
    plan, out, rec = run_op("remove_notes", notes_doc,
                            {"rules": [{"style_in": ["CMT"], "text_regex": "^Retain"}], "tracked": False})
    assert rec.passed, rec.to_markdown()
    body = texts(out)
    assert "Retain paragraph below if the project has a fire pump." not in body
    assert "Edit the value to suit." in body                            # CMT, but not "Retain"
    assert "Coordinate with the Owner's insurer." in body


@pytest.mark.parametrize("params", [{"rules": [{"color_in": ["blueish"]}]}, {"rules": [{"hidden": False}]},
                                    {"rules": [{}]}, {"rules": []}, {"rules": [{"style": ["CMT"]}]},
                                    {"rules": [{"text_regex": "("}]}, {"runs": {"style_in": ["CMT"]}}])
def test_note_params(notes_doc, params):
    with pytest.raises(JobException) as e:
        run_op("remove_notes", notes_doc, params)
    assert e.value.code == "bad-params"


def test_a_note_paragraph_with_a_field_is_refused():
    with pytest.raises(JobException) as e:
        run_op("remove_notes", build_notes(field_note=True), {"rules": [{"style_in": ["CMT"]}], "tracked": True})
    assert e.value.code == "field-result"


def test_notes_nothing_to_do_and_not_found(notes_doc):
    plan, out, rec = run_op("remove_notes", notes_doc, {"rules": [{"style_in": ["NoSuchStyle"]}]})
    assert rec.passed and out == notes_doc and any("nothing to do" in n for n in plan.notes)
    with pytest.raises(JobException) as e:
        run_op("remove_notes", notes_doc, {"rules": [{"style_in": ["NoSuchStyle"]}], "allow_missing": False})
    assert e.value.code == "not-found"


def test_notes_through_a_spec(notes_doc):
    spec = {"request": "strip the specifier notes", "summary": "s", "mode": "content",
            "ops": [{"op": "remove_notes", "params": {**NOTE_RULES, "author": AUTHOR}}]}
    pkg = DocxPackage(notes_doc)
    plan, frags = plan_spec(pkg, spec)
    out = apply_spec(pkg, frags).output_bytes()
    assert run_suite(notes_doc, out, plan.to_dict(), "s.docx", "o.docx").passed


@pytest.mark.parametrize("name,build", [("remove-hidden-text", build_hidden), ("remove-specifier-notes", build_notes),
                                        ("collapse-blank-lines", None)])
def test_the_worked_examples_validate_and_pass(name, build):
    from pathlib import Path
    from fx_removal import build_blank
    from spec_surgeon.editspec import load_spec, validate_spec
    spec = load_spec(Path(__file__).resolve().parent.parent / "examples" / "specs" / f"{name}.json")
    validate_spec(spec)
    src = (build or build_blank)()
    pkg = DocxPackage(src)
    plan, frags = plan_spec(pkg, spec)
    out = apply_spec(pkg, frags).output_bytes()
    rec = run_suite(src, out, plan.to_dict(), "s.docx", "o.docx")
    assert rec.passed, rec.to_markdown()
    assert out != src


# ---- planted defects: remove_notes ---------------------------------------------------------------------------------

def test_shared_defect_a_mixed_paragraph_taken_as_a_colour_note(notes_doc, monkeypatch):
    real = hidden_module.RemoveNotes._matches

    def loose(rule, p, content, look, sheet, where):
        if "color_in" in rule:
            wanted = hidden_module.color_names(rule["color_in"])
            return any(hidden_module.color_in(look.color(r.find(W + "rPr"), p), wanted) for r in content)
        return real(rule, p, content, look, sheet, where)
    monkeypatch.setattr(hidden_module.RemoveNotes, "_matches", staticmethod(loose))
    plan, out, rec = run_op("remove_notes", notes_doc, {**NOTE_RULES, "tracked": False})
    assert "Mixed: black and blue" not in " ".join(texts(out))          # deleted by plan and apply alike …
    assert failed(rec) == ["V10"], rec.to_markdown()                    # … not a note to the verifier


def test_shared_defect_link_text_taken_as_an_inline_note(notes_doc, monkeypatch):
    monkeypatch.setattr(hidden_module, "ancestor_below",
                        lambda el, p, tag: None if tag == W + "hyperlink" else _real_ancestor(el, p, tag))
    plan, out, rec = run_op("remove_notes", notes_doc, {"runs": {"color_in": ["0000FF"]}, "tracked": False})
    assert "the portal" not in " ".join(texts(out))
    assert failed(rec) == ["V10"], rec.to_markdown()


def _real_ancestor(el, p, tag):
    found, q = None, el.getparent()
    while q is not None and q is not p:
        if q.tag == tag:
            found = q
        q = q.getparent()
    return found


def test_defect_an_extra_paragraph_deleted(notes_doc):
    plan, out, rec = run_op("remove_notes", notes_doc, {**NOTE_RULES, "tracked": False})

    def drop(root):
        for p in root.iter(W + "p"):
            if "".join(t.text or "" for t in p.iter(W + "t")) == "A.Provide a fire pump.":   # the tab is a w:tab
                p.getparent().remove(p)
                return
        raise AssertionError("no A.")
    rec = run_suite(notes_doc, mutate(out, DOC, drop), plan.to_dict(), "s", "o")
    assert "V2" in failed(rec) and "V10" in failed(rec)


# ---- both: the last section keeps a paragraph (batch 6's keep rule 4) ---------------------------------------------

NOTE_TEXT = {"rules": [{"text_regex": "^NOTE"}]}
LAST_SECTION = [("remove_notes", "notes", NOTE_TEXT), ("remove_hidden_text", "hidden", {})]
LAST_SECTION_FLAG = "the last section is now empty and shows a blank page"
DROP_THE_PAGE = "remove the section break before it to drop the page"


def outline(data: bytes) -> List[str]:
    """The body as a reader sees it with every change accepted, paragraph by paragraph: "§" for one carrying a section
    break, "·" for an empty one, else its text; a tracked-deleted paragraph gone."""
    out = []
    for p in DocxPackage(data).xml(DOC).find(W + "body").iter(W + "p"):
        t = accepted_text(p)
        if p.find(f"{W}pPr/{W}rPr/{W}del") is not None and not t:
            continue
        out.append("§" if p.find(f"{W}pPr/{W}sectPr") is not None else t or "·")
    return out


def last_section_flags(plan) -> List[str]:
    return [f for f in plan.flags if f.startswith(LAST_SECTION_FLAG) and f.endswith(DROP_THE_PAGE)]


@pytest.mark.parametrize("tracked", [True, False])
@pytest.mark.parametrize("name,kind,params", LAST_SECTION)
def test_the_last_section_keeps_its_last_paragraph_demoted_and_flagged(name, kind, params, tracked):
    """Every paragraph after the body's last section break a note (a hidden paragraph): the last one stays, emptied —
    not the break's paragraph alone at the end of the body, which Word shows as a blank page with nothing on it to
    remove. Tracked, the document's last paragraph stayed already; the blank page is flagged either way."""
    src = build_last_section(kind)
    assert outline(src) == ["Cover", "§", "Body text", "§", "NOTE: one", "NOTE: two"]
    plan, out, rec = run_op(name, src, {**params, "tracked": tracked})
    assert rec.passed, rec.to_markdown()
    assert plan.property_assertions[0]["expected"][DOC] == {"delete": [4], "runs": {"5": [0]}, "simple": {}}
    assert outline(out) == ["Cover", "§", "Body text", "§", "·"]
    body = DocxPackage(out).xml(DOC).find(W + "body")
    kept = body[-2]
    assert body[-1].tag == W + "sectPr" and kept.find(f"{W}pPr/{W}sectPr") is None
    assert kept.find(f"{W}pPr/{W}rPr/{W}del") is None                          # its mark stays …
    assert (kept.find(f"{W}pPr/{W}rPr/{W}vanish") is not None) == (kind == "hidden")   # … as it was
    assert last_section_flags(plan) == [f"{LAST_SECTION_FLAG} (paragraph 5 stays); {DROP_THE_PAGE}"]
    assert any(f.startswith(f"{DOC} paragraph 5: kept its paragraph mark") for f in plan.flags)


@pytest.mark.parametrize("name,build,params", [("remove_notes", build_notes, NOTE_RULES),
                                               ("remove_hidden_text", build_hidden, {})])
def test_a_last_section_that_keeps_text_is_not_flagged(name, build, params):
    plan, out, rec = run_op(name, build(), {**params, "tracked": False})
    assert rec.passed and not last_section_flags(plan)


@pytest.mark.parametrize("tracked", [True, False])
def test_a_hidden_section_break_that_stays_still_ends_the_section_before_the_last(tracked):
    """The second break's paragraph is hidden too: it cannot be deleted (it carries the break), so it stays, emptied —
    and the last section after it still keeps its last paragraph: the rule runs once the paragraphs that cannot be
    deleted are kept, not before (then the break would not count, and the last section would seem to start earlier)."""
    plan, out, rec = run_op("remove_hidden_text", build_last_section("hidden", hidden_break=True), {"tracked": tracked})
    assert rec.passed, rec.to_markdown()
    assert plan.property_assertions[0]["expected"][DOC]["delete"] == [4]
    assert outline(out) == ["Cover", "§", "Body text", "§", "·"]
    assert any(f.startswith(f"{DOC} paragraph 3: kept its paragraph mark (it carries a section break)")
               for f in plan.flags)
    assert last_section_flags(plan) == [f"{LAST_SECTION_FLAG} (paragraph 5 stays); {DROP_THE_PAGE}"]


@pytest.mark.parametrize("tracked", [True, False])
def test_a_hidden_paragraph_that_cannot_be_deleted_is_what_the_last_section_keeps(tracked):
    """The hidden "NOTE: one" holds a field: it cannot be deleted, so it stays, emptied; the last section keeps a
    paragraph already and "NOTE: two" goes untracked — tracked, the document's last paragraph stays too."""
    plan, out, rec = run_op("remove_hidden_text", build_last_section("hidden", field_in="one"), {"tracked": tracked})
    assert rec.passed, rec.to_markdown()
    assert plan.property_assertions[0]["expected"][DOC]["delete"] == ([] if tracked else [5])
    assert outline(out) == ["Cover", "§", "Body text", "§", "·"] + (["·"] if tracked else [])
    stays = "paragraphs 4 and 5 stay" if tracked else "paragraph 4 stays"
    assert last_section_flags(plan) == [f"{LAST_SECTION_FLAG} ({stays}); {DROP_THE_PAGE}"]


@pytest.mark.parametrize("tracked", [True, False])
def test_the_note_the_last_section_keeps_is_emptied_not_refused(tracked):
    """remove_notes refuses a note paragraph it cannot delete (here: one holding a field) — but not the one the last
    section keeps, which is not deleted: it loses its runs (the field whole) and keeps its mark. Any other such note in
    that section still sets the file aside."""
    plan, out, rec = run_op("remove_notes", build_last_section("notes", field_in="two"),
                            {**NOTE_TEXT, "tracked": tracked})
    assert rec.passed, rec.to_markdown()
    assert outline(out) == ["Cover", "§", "Body text", "§", "·"]
    assert "«NOTE: two{REF _Ref9 \\h}21 10 00»" in " ".join(plan.flags)
    assert last_section_flags(plan) == [f"{LAST_SECTION_FLAG} (paragraph 5 stays); {DROP_THE_PAGE}"]
    with pytest.raises(JobException) as e:
        run_op("remove_notes", build_last_section("notes", field_in="one"), {**NOTE_TEXT, "tracked": tracked})
    assert e.value.code == "field-result"


# ---- planted defects: the last section ---------------------------------------------------------------------------

def _drop_the_kept_paragraph(root):
    body = root.find(W + "body")
    kept = body[-2]
    assert kept.tag == W + "p" and kept.find(f"{W}pPr/{W}sectPr") is None
    body.remove(kept)


def _delete_the_kept_mark(root):
    p = root.find(W + "body")[-2]
    ppr = p.find(W + "pPr")
    if ppr is None:
        ppr = etree.Element(W + "pPr")
        p.insert(0, ppr)
    rpr = ppr.find(W + "rPr")
    if rpr is None:
        rpr = etree.SubElement(ppr, W + "rPr")
    mark = etree.SubElement(rpr, W + "del")
    mark.set(W + "id", "9999")
    mark.set(W + "author", AUTHOR)
    mark.set(W + "date", "2026-10-21T00:00:00Z")


@pytest.mark.parametrize("tracked", [True, False])
@pytest.mark.parametrize("name,kind,params", LAST_SECTION)
def test_defect_the_last_sections_kept_paragraph_removed_too(name, kind, params, tracked):
    src = build_last_section(kind)
    plan, out, rec = run_op(name, src, {**params, "tracked": tracked})
    assert rec.passed, rec.to_markdown()
    wrong = mutate(out, DOC, _delete_the_kept_mark if tracked else _drop_the_kept_paragraph)
    assert outline(wrong) == ["Cover", "§", "Body text", "§"]                   # the last section is empty
    rec = run_suite(src, wrong, plan.to_dict(), "s", "o")
    assert "V10" in failed(rec), rec.to_markdown()
    assert any(f"removed_{'notes' if kind == 'notes' else 'hidden_text'}" in e for e in v10_errors(rec))


@pytest.mark.parametrize("name,kind,params", LAST_SECTION)
def test_shared_defect_the_last_section_emptied(name, kind, params, monkeypatch):
    monkeypatch.setattr(hidden_module, "last_section_keep", lambda blocks, chosen, keep, where: [])
    src = build_last_section(kind)
    plan, out, rec = run_op(name, src, {**params, "tracked": False})
    assert outline(out) == ["Cover", "§", "Body text", "§"]                     # plan and apply agree …
    assert not last_section_flags(plan)
    assert failed(rec) == ["V10"], rec.to_markdown()                           # … the verifier does not
    assert any("the plan predicted" in e for e in v10_errors(rec))


def test_shared_defect_the_last_section_measured_before_the_undeletable_are_kept(monkeypatch):
    real = hidden_module.last_section_keep
    monkeypatch.setattr(hidden_module, "last_section_keep",
                        lambda blocks, chosen, keep, where: real(blocks, chosen, [], where))
    src = build_last_section("hidden", hidden_break=True)
    plan, out, rec = run_op("remove_hidden_text", src, {"tracked": False})
    assert outline(out) == ["Cover", "§", "Body text", "§"]                     # the kept break did not count
    assert failed(rec) == ["V10"], rec.to_markdown()
    assert any("removed_hidden_text" in e and "the plan predicted" in e for e in v10_errors(rec))
