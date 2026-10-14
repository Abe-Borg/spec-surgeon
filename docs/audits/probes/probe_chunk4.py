"""Independent probes for AUD-11..AUD-13 (Chunk 4)."""
import copy, io, json, re, sys, zipfile
import pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3] / "tests"))
from lxml import etree
from make_fixtures import build, build_rich
from test_adaptive_messy import _build_messy_inline_spec, mutate_xml
from spec_surgeon.package import DocxPackage, W
from spec_surgeon.ooxml import block_full_text, paragraphs
from spec_surgeon.ops import JobException, get_op, merge_fragments
import spec_surgeon.ops.selectors as SEL
from spec_surgeon.ops.selectors import StyleSheet, paragraph_selected
from spec_surgeon.verify import run_suite
from spec_surgeon.verify.reader import VPackage, STYLES_REL
from spec_surgeon.verify.props import para_matches, PartView, Styles
AUTHOR = "Abraham Borg"
def section(t): print("\n" + "="*8, t, "="*8)
def show(label, val): print(f"  {label}: {val}")
def run_op(src_bytes, op, select, params):
    pkg = DocxPackage(src_bytes); frag = get_op(op).plan(pkg, select, params); plan = merge_fragments([frag])
    work = pkg.copy(); get_op(op).apply(work, frag); out = work.output_bytes()
    rec = run_suite(src_bytes, out, plan.to_dict(), "s.docx", "o.docx"); return plan, out, rec
def fails(rec): return [c.id for c in rec.checks if c.status == "FAIL"]
def verdict(rec): return "PASS" if rec.passed else "FAIL " + str(fails(rec))

# ---------------- fixture with style-driven bold/caps, rStyle, overrides, deleted bold, textbox ----------------
def styles_mut(root):
    for sid, kind, el in (("BoldHead", "paragraph", "b"), ("CapsHead", "paragraph", "caps"), ("BoldChar", "character", "b")):
        st = etree.SubElement(root, W+"style"); st.set(W+"type", kind); st.set(W+"styleId", sid)
        etree.SubElement(st, W+"name").set(W+"val", sid)
        rpr = etree.SubElement(st, W+"rPr"); etree.SubElement(rpr, W+el)
def body_mut(root):
    body = root.find(W+"body"); anchor = body.findall(W+"p")[5]
    def para(text, pstyle=None, rstyle=None, direct=None, deleted_bold_run=False):
        p = etree.Element(W+"p")
        if pstyle:
            ppr = etree.SubElement(p, W+"pPr"); etree.SubElement(ppr, W+"pStyle").set(W+"val", pstyle)
        r = etree.SubElement(p, W+"r")
        if rstyle or direct:
            rpr = etree.SubElement(r, W+"rPr")
            if rstyle: etree.SubElement(rpr, W+"rStyle").set(W+"val", rstyle)
            if direct:
                for tag, val in direct:
                    e = etree.SubElement(rpr, W+tag)
                    if val is not None: e.set(W+"val", val)
        etree.SubElement(r, W+"t").text = text
        if deleted_bold_run:
            d = etree.SubElement(p, W+"del"); d.set(W+"id", "777"); d.set(W+"author", "Jack"); d.set(W+"date", "2026-01-01T00:00:00Z")
            rd = etree.SubElement(d, W+"r"); etree.SubElement(etree.SubElement(rd, W+"rPr"), W+"b"); etree.SubElement(rd, W+"delText").text = "DELETED BOLD"
        return p
    new = [
        para("style bold heading", pstyle="BoldHead"),                       # bold via pStyle
        para("style caps heading", pstyle="CapsHead"),                       # caps via pStyle (text is lowercase)
        para("char style bold", rstyle="BoldChar"),                          # bold via rStyle
        para("style bold but direct off", pstyle="BoldHead", direct=[("b", "0")]),  # override
        para("plain with deleted bold run", deleted_bold_run=True),          # bold only inside w:del
        para("MIXED Case TITLE 123"),                                       # not all caps
        para("1.01"),                                                        # digits only
        para("ALL CAPS NO STYLE"),
        para("bold val 1 explicit", direct=[("b", "1")]),
    ]
    for p in reversed(new):
        anchor.addnext(p)
src = mutate_xml(mutate_xml(build("21 05 00", "X"), "word/styles.xml", styles_mut), "word/document.xml", body_mut)

section("AUD-11 selector agreement (transform vs verifier) over every paragraph")
pkg = DocxPackage(src); root = pkg.xml("word/document.xml"); pars = paragraphs(root); sheet = StyleSheet(pkg)
vp = VPackage(src); view = PartView(vp.xml("word/document.xml")); vst = Styles(vp.xml(vp.related(STYLES_REL)))
preds = [{"has_bold": True}, {"has_bold": False}, {"is_all_caps": True}, {"is_all_caps": False},
         {"after_heading_regex": r"^GENERAL$"}, {"between_paragraphs": {"start_regex": r"^GENERAL$", "end_regex": r"^2\.01"}},
         {"between_paragraphs": {"start_regex": r"^GENERAL$", "end_regex": r"^2\.01", "include_bounds": True}},
         {"between_paragraphs": {"start_regex": r"^NOPE$"}}, {"between_paragraphs": {"start_regex": r"^GENERAL$"}},
         {"text_regex": r"^A\.\tThe Owner"}, {"text_regex": r"^1\.01\tSUMMARY"}, {"text_regex": r"SECTION\s+21\s+05"},
         {"has_bold": True, "is_all_caps": True}]
mismatch = 0
for pred in preds:
    t_sel = [i for i, p in enumerate(pars) if paragraph_selected(pred, p, i, block_full_text(p), sheet, all_pars=pars)]
    v_sel = [i for i, p in enumerate(view.pars) if para_matches(pred, p, i, view, vst)]
    ok = t_sel == v_sel
    mismatch += (not ok)
    show(f"{json.dumps(pred)}", f"{'agree' if ok else 'DISAGREE'} transform={t_sel}" + ("" if ok else f" verifier={v_sel}"))
show("total disagreements", mismatch)
texts = {i: block_full_text(p)[:40] for i, p in enumerate(pars) if 5 <= i <= 15}
show("paragraphs 5..15", texts)
hb = [i for i, p in enumerate(pars) if paragraph_selected({"has_bold": True}, p, i, block_full_text(p), sheet, all_pars=pars)]
show("has_bold selects (expect style/rStyle/explicit bold; NOT the override or deleted-run paragraphs)", [(i, block_full_text(pars[i])[:30]) for i in hb])
ac = [i for i, p in enumerate(pars) if paragraph_selected({"is_all_caps": True}, p, i, block_full_text(p), sheet, all_pars=pars)]
show("is_all_caps selects", [(i, block_full_text(pars[i])[:30]) for i in ac])

section("AUD-11 divergence test: a WRONG transform-side has_bold (direct w:b only) must be caught by V10")
orig = SEL._paragraph_has_bold
SEL._paragraph_has_bold = lambda p, sheet: any(r.find(W+"rPr/"+W+"b") is not None for r in SEL._live_runs(p))
try:
    plan, out, rec = run_op(src, "set_font", {"paragraphs": {"has_bold": True}}, {"font": "Courier New", "author": AUTHOR})
    show("wrong has_bold on both plan and apply", verdict(rec) + f" | V10: {rec.get('V10').summary[:160]}")
finally:
    SEL._paragraph_has_bold = orig
plan, out, rec = run_op(src, "set_font", {"paragraphs": {"has_bold": True}}, {"font": "Courier New", "author": AUTHOR})
show("correct has_bold", verdict(rec))

# ---------------- AUD-12 delete_paragraphs ----------------
section("AUD-12 delete_paragraphs refusals")
base = build("21 05 00", "COMMON WORK RESULTS")
def try_plan(src_bytes, op, select, params):
    try:
        f = get_op(op).plan(DocxPackage(src_bytes), select, params); return f"planned ({len(f.diffs)} diffs, rev {dict(f.rev_delta)})"
    except JobException as e:
        return f"refused [{e.code}]"
show("tracked, paragraph with pending insertion (C.)", try_plan(base, "delete_paragraphs", {"paragraphs": {"text_regex": r"^C\.\tSprinklers"}}, {"tracked": True, "author": AUTHOR}))
show("untracked, same paragraph (drops Jeremy's insertion, flagged)", try_plan(base, "delete_paragraphs", {"paragraphs": {"text_regex": r"^C\.\tSprinklers"}}, {"tracked": False}))
show("tracked, paragraph with a REF field (E.)", try_plan(base, "delete_paragraphs", {"paragraphs": {"text_regex": r"^E\.\tSee Section"}}, {"tracked": True, "author": AUTHOR}))
show("untracked, table cell 'Rev' (only paragraph of its cell)", try_plan(base, "delete_paragraphs", {"paragraphs": {"text_regex": r"^Rev$"}}, {"tracked": False}))
show("tracked, table cell 'Rev'", try_plan(base, "delete_paragraphs", {"paragraphs": {"text_regex": r"^Rev$"}}, {"tracked": True, "author": AUTHOR}))
show("tracked, 'all'", try_plan(base, "delete_paragraphs", {"paragraphs": "all"}, {"tracked": True}))
show("tracked, is_empty (sectPr paragraph among them)", try_plan(base, "delete_paragraphs", {"paragraphs": {"is_empty": True}}, {"tracked": True, "author": AUTHOR}))
def add_bookmark(root):
    for p in root.find(W+"body").findall(W+"p"):
        if any((t.text or "").startswith("GENERAL") for t in p.iter(W+"t")):
            bs = etree.Element(W+"bookmarkStart"); bs.set(W+"id", "0"); bs.set(W+"name", "_GoBack"); p.insert(1, bs)
            be = etree.Element(W+"bookmarkEnd"); be.set(W+"id", "0"); p.append(be); return
bk = mutate_xml(base, "word/document.xml", add_bookmark)
show("tracked, paragraph with a _GoBack bookmark (very common in real files)", try_plan(bk, "delete_paragraphs", {"paragraphs": {"text_regex": r"^GENERAL$"}}, {"tracked": True, "author": AUTHOR}))
show("untracked, same bookmarked paragraph", try_plan(bk, "delete_paragraphs", {"paragraphs": {"text_regex": r"^GENERAL$"}}, {"tracked": False}))

section("AUD-12 delete_paragraphs planted defects")
sel = {"paragraphs": {"text_regex": r"^A\.\tThe Owner's insurer"}}
plan, out, rec = run_op(base, "delete_paragraphs", sel, {"tracked": True, "author": AUTHOR})
show("good tracked deletion", verdict(rec) + f"; geometry_reference={plan.geometry_reference!r}; V7={rec.get('V7').status}")
def unwrap_runs(root):   # mark del kept, runs NOT deleted (text stays live)
    for d in list(root.iter(W+"del")):
        if d.getparent().tag == W+"p":
            parent = d.getparent(); idx = parent.index(d)
            for r in list(d):
                for dt in r.findall(W+"delText"): dt.tag = W+"t"
                parent.insert(idx, r); idx += 1
            parent.remove(d)
bad = mutate_xml(out, "word/document.xml", unwrap_runs)
show("PLANTED: paragraph mark deleted but runs left live", verdict(run_suite(base, bad, plan.to_dict(), "s", "o")))
def move_to_neighbor(root):  # the deletion lands on the NEXT paragraph instead
    pars = root.find(W+"body").findall(W+"p")
    for i, p in enumerate(pars):
        if p.find(W+"del") is not None:
            nxt = pars[i+1]
            # undo on p
            unwrap_runs(root); rpr = p.find(W+"pPr/"+W+"rPr")
            if rpr is not None and rpr.find(W+"del") is not None: rpr.remove(rpr.find(W+"del"))
            # apply on nxt
            mrpr = nxt.find(W+"pPr")
            if mrpr is None: mrpr = etree.Element(W+"pPr"); nxt.insert(0, mrpr)
            r_ = etree.SubElement(mrpr, W+"rPr"); d = etree.SubElement(r_, W+"del"); d.set(W+"id", "9001"); d.set(W+"author", AUTHOR); d.set(W+"date", "2026-01-01T00:00:00Z")
            runs = nxt.findall(W+"r"); dw = etree.Element(W+"del"); dw.set(W+"id", "9002"); dw.set(W+"author", AUTHOR); dw.set(W+"date", "2026-01-01T00:00:00Z")
            pos = list(nxt).index(runs[0])
            for r in runs:
                nxt.remove(r)
                for t in r.findall(W+"t"): t.tag = W+"delText"
                dw.append(r)
            nxt.insert(pos, dw); return
bad2 = mutate_xml(out, "word/document.xml", move_to_neighbor)
show("PLANTED: deletion applied to the neighbouring paragraph", verdict(run_suite(base, bad2, plan.to_dict(), "s", "o")))
plan_u, out_u, rec_u = run_op(base, "delete_paragraphs", sel, {"tracked": False})
show("good untracked deletion", verdict(rec_u) + f"; V5b={rec_u.get('V5b').status} V8={rec_u.get('V8').status}")
def extra_removal(root):
    pars = root.find(W+"body").findall(W+"p")
    for p in pars:
        if any((t.text or "").startswith("B.") for t in p.iter(W+"t")) or any(t.tail is None and (t.text or "") == "B." for t in p.iter(W+"t")):
            p.getparent().remove(p); return
bad3 = mutate_xml(out_u, "word/document.xml", extra_removal)
show("PLANTED: untracked deletion also removed paragraph B.", verdict(run_suite(base, bad3, plan_u.to_dict(), "s", "o")))

# ---------------- AUD-13 insert_paragraph ----------------
section("AUD-13 insert_paragraph")
sel_i = {"paragraphs": {"text_regex": r"^1\.01\tSUMMARY"}}
text = " F.\tProvide hydraulic calculation submittals. "
plan, out, rec = run_op(base, "insert_paragraph", sel_i, {"text": text, "position": "after", "tracked": True, "author": AUTHOR})
show("good tracked insert (text with edge spaces + a tab character)", verdict(rec) + f"; geometry_reference={plan.geometry_reference!r}; V7={rec.get('V7').status}")
oroot = etree.fromstring(VPackage(out).read("word/document.xml"))
for p in oroot.iter(W+"p"):
    if any("hydraulic" in (t.text or "") for t in p.iter(W+"t")):
        show("inserted paragraph XML", etree.tostring(p).decode()[:600])
        break
# anchor with direct paragraph formatting (ind) and run formatting (Arial 9 bold): is any of it inherited?
def format_anchor(root):
    for p in root.find(W+"body").findall(W+"p"):
        if any((t.text or "") == "1.01" for t in p.iter(W+"t")):
            ppr = p.find(W+"pPr")
            if ppr is None: ppr = etree.Element(W+"pPr"); p.insert(0, ppr)
            ind = etree.SubElement(ppr, W+"ind"); ind.set(W+"left", "720"); ind.set(W+"hanging", "720")
            for r in p.findall(W+"r"):
                rpr = r.find(W+"rPr")
                if rpr is None: rpr = etree.Element(W+"rPr"); r.insert(0, rpr)
                rf = etree.SubElement(rpr, W+"rFonts"); rf.set(W+"ascii", "Courier New"); rf.set(W+"hAnsi", "Courier New")
                etree.SubElement(rpr, W+"b"); etree.SubElement(rpr, W+"sz").set(W+"val", "18")
            return
fa = mutate_xml(base, "word/document.xml", format_anchor)
plan, out, rec = run_op(fa, "insert_paragraph", sel_i, {"text": "F.\tNew clause.", "position": "after", "tracked": False})
oroot = etree.fromstring(VPackage(out).read("word/document.xml"))
for p in oroot.iter(W+"p"):
    if any("New clause" in (t.text or "") for t in p.iter(W+"t")):
        show("inserted after a Courier-New/bold/9pt, hanging-indent anchor → new paragraph XML", etree.tostring(p).decode())
        break
show("  (suite)", verdict(rec))
# planted defects
plan, out, rec = run_op(base, "insert_paragraph", sel_i, {"text": "F.\tNew clause.", "position": "after", "tracked": True, "author": AUTHOR})
def wrong_position(root):
    for p in root.iter(W+"p"):
        if any("New clause" in (t.text or "") for t in p.iter(W+"t")):
            nxt = p.getnext(); p.getparent().remove(p); nxt.addnext(p); return
show("PLANTED: inserted one paragraph too late", verdict(run_suite(base, mutate_xml(out, "word/document.xml", wrong_position), plan.to_dict(), "s", "o")))
def drop_ins_wrapper(root):
    for p in root.iter(W+"p"):
        if any("New clause" in (t.text or "") for t in p.iter(W+"t")):
            ins = p.find(W+"ins"); idx = list(p).index(ins)
            for r in list(ins): p.insert(idx, r); idx += 1
            p.remove(ins); return
show("PLANTED: inserted text not wrapped in w:ins (mark still inserted)", verdict(run_suite(base, mutate_xml(out, "word/document.xml", drop_ins_wrapper), plan.to_dict(), "s", "o")))
def drop_mark_ins(root):
    for p in root.iter(W+"p"):
        if any("New clause" in (t.text or "") for t in p.iter(W+"t")):
            rpr = p.find(W+"pPr/"+W+"rPr"); rpr.remove(rpr.find(W+"ins")); return
show("PLANTED: paragraph mark not marked inserted (w:ins wrapper kept)", verdict(run_suite(base, mutate_xml(out, "word/document.xml", drop_mark_ins), plan.to_dict(), "s", "o")))
def wrong_style(root):
    for p in root.iter(W+"p"):
        if any("New clause" in (t.text or "") for t in p.iter(W+"t")):
            ppr = p.find(W+"pPr"); ps = etree.Element(W+"pStyle"); ps.set(W+"val", "Heading1"); ppr.insert(0, ps); return
show("PLANTED: inserted paragraph given a style the anchor lacks", verdict(run_suite(base, mutate_xml(out, "word/document.xml", wrong_style), plan.to_dict(), "s", "o")))
# refusals / edge
sect_p = [i for i, p in enumerate(paragraphs(DocxPackage(base).xml("word/document.xml"))) if p.find(W+"pPr/"+W+"sectPr") is not None]
show("paragraph indices carrying sectPr", sect_p)
show("insert AFTER the sectPr paragraph", try_plan(base, "insert_paragraph", {"paragraphs": {"index_range": [sect_p[0], sect_p[0]+1]}}, {"text": "X", "position": "after"}))
show("insert BEFORE the sectPr paragraph", try_plan(base, "insert_paragraph", {"paragraphs": {"index_range": [sect_p[0], sect_p[0]+1]}}, {"text": "X", "position": "before"}))
show("insert before paragraph 0", try_plan(base, "insert_paragraph", {"paragraphs": {"index_range": [0, 1]}}, {"text": "X", "position": "before"}))
show("unknown style", try_plan(base, "insert_paragraph", sel_i, {"text": "X", "style": "NoSuchStyle"}))
show("text '   ' (blank)", try_plan(base, "insert_paragraph", sel_i, {"text": "   "}))
show("insert with two anchors (2.01 and 3.01), after", try_plan(base, "insert_paragraph", {"paragraphs": {"text_regex": r"^(2|3)\.01\t"}}, {"text": "X"}))
# conflicts between the new ops and others in the same part
def two(op_a, sel_a, par_a, op_b, sel_b, par_b):
    pkg = DocxPackage(base)
    try:
        fa_ = get_op(op_a).plan(pkg, sel_a, par_a); fb_ = get_op(op_b).plan(pkg, sel_b, par_b)
        merge_fragments([fa_, fb_]); return "merged (no conflict)"
    except JobException as e:
        return f"refused [{e.code}]"
show("two insert_paragraph ops, different anchors, same part", two("insert_paragraph", {"paragraphs": {"text_regex": r"^2\.01\t"}}, {"text": "X", "author": AUTHOR}, "insert_paragraph", {"paragraphs": {"text_regex": r"^5\.01\t"}}, {"text": "Y", "author": AUTHOR}))
show("tracked delete + replace_text in other paragraphs, same part", two("delete_paragraphs", {"paragraphs": {"text_regex": r"^2\.01\t"}}, {"tracked": True, "author": AUTHOR}, "replace_text", {"scope": ["document"]}, {"find": "FM Global", "replace": "GRC", "author": AUTHOR}))
show("untracked delete + untracked replace_text, same part", two("delete_paragraphs", {"paragraphs": {"text_regex": r"^2\.01\t"}}, {"tracked": False}, "replace_text", {"scope": ["document"]}, {"find": "FM Global", "replace": "GRC", "tracked": False}))
show("insert + set_font on other paragraphs, same part", two("insert_paragraph", {"paragraphs": {"text_regex": r"^2\.01\t"}}, {"text": "X", "author": AUTHOR}, "set_font", {"paragraphs": {"text_regex": r"^5\.01\t"}}, {"font": "Courier New", "author": AUTHOR}))

# proofErr paragraph-wide strip (corrected target)
section("AUD-08 (redo) proofErr stripped paragraph-wide?")
def many_proof(root):
    for p in root.find(W+"body").findall(W+"p"):
        if any((t.text or "") == "The auditor is FM " for t in p.iter(W+"t")):
            runs = p.findall(W+"r")
            for k in range(3):
                pe = etree.Element(W+"proofErr"); pe.set(W+"type", "spellStart"); runs[-1].addnext(pe)
            rpr = etree.Element(W+"rPr"); etree.SubElement(rpr, W+"noProof"); runs[1].insert(0, rpr); return
mp = mutate_xml(base, "word/document.xml", many_proof)
cnt = lambda data: sum(1 for _ in etree.fromstring(VPackage(data).read("word/document.xml")).iter(W+"proofErr"))
plan, out, rec = run_op(mp, "replace_text", {"scope": ["document"]}, {"find": "FM Global", "replace": "GRC", "ignore_formatting_noise": True, "author": AUTHOR})
show("proofErr count source→output (3 markers at the END of the paragraph, away from the match)", f"{cnt(mp)}→{cnt(out)}; suite {verdict(rec)}")
