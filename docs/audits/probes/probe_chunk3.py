"""Independent probes for AUD-07..AUD-10 (Chunk 3) + the V4 duplicate-id question."""
import copy, io, json, re, sys, zipfile
import pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3] / "tests"))
from lxml import etree
from make_fixtures import build, build_rich, build_template
from test_adaptive_messy import _build_messy_inline_spec, mutate_xml
from spec_surgeon.package import DocxPackage, W
from spec_surgeon.ooxml import flexible_regex_from_literal, merge_adjacent_runs, rpr_key, NOISE_RPR_LOCALS
from spec_surgeon.ops import JobException, get_op, merge_fragments
from spec_surgeon.ops.slots import extract_fill
from spec_surgeon.verify import run_suite
from spec_surgeon.verify.reader import VPackage
from spec_surgeon.verify.template import fill_value
from spec_surgeon import compiler as C
AUTHOR = "Abraham Borg"
def section(t): print("\n" + "="*8, t, "="*8)
def show(label, val): print(f"  {label}: {val}")
def run_op(src_bytes, op, select, params):
    pkg = DocxPackage(src_bytes); frag = get_op(op).plan(pkg, select, params); plan = merge_fragments([frag])
    work = pkg.copy(); get_op(op).apply(work, frag); out = work.output_bytes()
    rec = run_suite(src_bytes, out, plan.to_dict(), "s.docx", "o.docx"); return plan, out, rec
def statuses(rec): return {c.id: c.status for c in rec.checks}
def fails(rec): return [c.id for c in rec.checks if c.status == "FAIL"]

# ---------------- AUD-07 flexible whitespace ----------------
section("AUD-07 flexible_regex_from_literal")
show("regex for 'FM Global - Risk'", flexible_regex_from_literal("FM Global - Risk"))
rx = re.compile(flexible_regex_from_literal("FM Global"))
for probe in ["FM Global", "FM Global", "FM    Global", "FM\tGlobal (tab CHARACTER in w:t)", "FM\nGlobal"]:
    show(f"matches {probe!r}", bool(rx.search(probe)))
# w:tab ELEMENT between words: live text has no character for it
def tab_between(root):
    for p in root.find(W+"body").findall(W+"p"):
        for t in p.iter(W+"t"):
            if t.text and t.text.startswith("The Owner's insurer is FM Global"):
                r = t.getparent(); a, b = t.text.split("FM Global", 1)
                t.text = a + "FM"; r2 = copy.deepcopy(r); r3 = copy.deepcopy(r)
                r2.find(W+"t").text = None; r2.remove(r2.find(W+"t")); etree.SubElement(r2, W+"tab")
                r3.find(W+"t").text = "Global" + b
                r.addnext(r2); r2.addnext(r3); return
tb = mutate_xml(build("21 05 00", "X"), "word/document.xml", tab_between)
try:
    frag = get_op("replace_text").plan(DocxPackage(tb), {"scope": ["document"]}, {"find": "FM Global", "replace": "GRC", "flexible_whitespace": True, "author": AUTHOR})
    show("flexible find across a w:tab ELEMENT", f"planned {len(frag.diffs)} diffs (the tab-split occurrence is NOT matched; only others)")
except JobException as e:
    show("flexible find across a w:tab ELEMENT", f"refused [{e.code}]")

# ---------------- AUD-08 noise coalescing: is w:cs really non-visual? ----------------
section("AUD-08 noise coalescing")
show("NOISE_RPR_LOCALS", sorted(NOISE_RPR_LOCALS))
def add_cs_split(root):
    """Split 'FM Global' across two runs where the 2nd run carries <w:cs/> (complex-script flag) and nothing else."""
    for p in root.find(W+"body").findall(W+"p"):
        for t in p.iter(W+"t"):
            if t.text and t.text.startswith("The Owner's insurer is FM Global"):
                r = t.getparent(); a, b = t.text.split("Global", 1)
                t.text = a; r2 = copy.deepcopy(r); r2.find(W+"t").text = "Global" + b
                rpr = r2.find(W+"rPr")
                if rpr is None:
                    rpr = etree.Element(W+"rPr"); r2.insert(0, rpr)
                etree.SubElement(rpr, W+"cs")
                r.addnext(r2); return
cs_src = mutate_xml(build("21 05 00", "X"), "word/document.xml", add_cs_split)
try:
    get_op("replace_text").plan(DocxPackage(cs_src), {"scope": ["document"]}, {"find": "FM Global", "replace": "GRC", "author": AUTHOR})
    show("without ignore_formatting_noise", "planned (unexpected)")
except JobException as e:
    show("without ignore_formatting_noise", f"refused [{e.code}]")
plan, out, rec = run_op(cs_src, "replace_text", {"scope": ["document"]}, {"find": "FM Global", "replace": "GRC", "ignore_formatting_noise": True, "author": AUTHOR})
show("with ignore_formatting_noise: suite", "PASS" if rec.passed else fails(rec))
oroot = etree.fromstring(VPackage(out).read("word/document.xml"))
for p in oroot.iter(W+"p"):
    if any((t.text or "").startswith("The Owner's insurer") for t in p.iter(W+"t")):
        show("edited paragraph runs (rPr children → text)", [([etree.QName(c).localname for c in (r.find(W+'rPr') if r.find(W+'rPr') is not None else [])], "".join(x.text or "" for x in r.iter(W+"t", W+"delText"))) for r in p.iter(W+"r")])
        break
# proofErr stripped paragraph-wide?
def many_proof(root):
    for p in root.find(W+"body").findall(W+"p"):
        if any((t.text or "").startswith("B.\tThe auditor is FM ") for t in p.iter(W+"t")):
            runs = p.findall(W+"r")
            for k in range(3):
                pe = etree.Element(W+"proofErr"); pe.set(W+"type", "spellStart"); runs[-1].addnext(pe)
            rpr = etree.Element(W+"rPr"); etree.SubElement(rpr, W+"noProof"); runs[1].insert(0, rpr)
            return
mp = mutate_xml(build("21 05 00", "X"), "word/document.xml", many_proof)
plan, out, rec = run_op(mp, "replace_text", {"scope": ["document"]}, {"find": "FM Global", "replace": "GRC", "ignore_formatting_noise": True, "author": AUTHOR})
cnt = lambda data: sum(1 for _ in etree.fromstring(VPackage(data).read("word/document.xml")).iter(W+"proofErr"))
show("proofErr count source→output (3 trailing markers far from the match)", f"{cnt(mp)}→{cnt(out)}; suite {'PASS' if rec.passed else fails(rec)}")

# ---------------- AUD-09 tracked-format split: prediction at run start / middle / end ----------------
section("AUD-09 allow_tracked_format_split")
def add_rprchange(root):
    for p in root.find(W+"body").findall(W+"p"):
        for t in p.iter(W+"t"):
            if t.text and t.text.startswith("The Owner's insurer is FM Global"):
                r = t.getparent(); rpr = r.find(W+"rPr")
                if rpr is None:
                    rpr = etree.Element(W+"rPr"); r.insert(0, rpr)
                etree.SubElement(rpr, W+"b")
                chg = etree.SubElement(rpr, W+"rPrChange"); chg.set(W+"id", "900"); chg.set(W+"author", "Jeremy"); chg.set(W+"date", "2026-09-22T10:15:00Z")
                etree.SubElement(chg, W+"rPr"); return
tf = mutate_xml(build("21 05 00", "X"), "word/document.xml", add_rprchange)
for find in ["The Owner's", "FM Global", "FM Approved."]:
    plan, out, rec = run_op(tf, "replace_text", {"paragraphs": {"text_regex": "Owner's insurer"}, "scope": ["document"]},
                            {"find": find, "replace": "XYZ", "allow_tracked_format_split": True, "author": AUTHOR})
    show(f"find {find!r} (start/middle/end of the rPrChange run)", f"predicted rPrChange Δ{plan.rev_delta.get('rPrChange', 0)}; suite {'PASS' if rec.passed else fails(rec)}")
# planted defect: split fragments keep the SAME w:id on their rPrChange copies
plan, out, rec = run_op(tf, "replace_text", {"paragraphs": {"text_regex": "Owner's insurer"}, "scope": ["document"]},
                        {"find": "FM Global", "replace": "XYZ", "allow_tracked_format_split": True, "author": AUTHOR})
def dup_ids(root):
    for chg in root.iter(W+"rPrChange"):
        chg.set(W+"id", "900")
bad = mutate_xml(out, "word/document.xml", dup_ids)
bad_rec = run_suite(tf, bad, plan.to_dict(), "s.docx", "o.docx")
n_dup = sum(1 for c in etree.fromstring(VPackage(bad).read("word/document.xml")).iter(W+"rPrChange") if c.get(W+"id") == "900")
show(f"PLANTED: {n_dup} rPrChange elements all with w:id=900", "CAUGHT by " + str(fails(bad_rec)) if not bad_rec.passed else "**NOT CAUGHT — suite PASSES with duplicate revision ids**")
show("  V4 summary", bad_rec.get("V4").summary[:200])
# also: does the module docstring match? (docstring says fragments DROP the duplicate rPrChange)
import spec_surgeon.ops.replace_text as RT
show("replace_text docstring claims", [ln.strip() for ln in RT.__doc__.splitlines() if "rPrChange" in ln][:2])

# ---------------- AUD-10 first_match / verifier leniency / NBSP normalization ----------------
section("AUD-10 slot fill rules")
src_pkg = DocxPackage(_build_messy_inline_spec("21 13 13", "WET-PIPE SPRINKLER SYSTEMS"))
vsrc = VPackage(src_pkg.data)
chain_bad = {"rule": "first_match", "rules": [{"rule": "regex", "source": "document", "pattern": "("},
                                               {"rule": "regex", "source": "document", "pattern": r"^SECTION\s+\d{2}\s+\d{2}\s+\d{2}\s*[-–—]\s*(.+)$"}]}
try:
    extract_fill(src_pkg, "T", chain_bad); show("transform: chain with a bad sub-regex", "extracted (unexpected)")
except JobException as e:
    show("transform: chain with a bad sub-regex", f"refused [{e.code}] immediately")
try:
    show("verifier: same chain", f"returns {fill_value(vsrc, 'T', chain_bad)!r} (skips the bad sub-rule; moot since the transform refuses first)")
except Exception as e:
    show("verifier: same chain", f"raises {type(e).__name__}: {e}")
# NBSP inside the extracted value is normalized to a plain space on BOTH sides
def nbsp_title(root):
    for t in root.find(W+"body").iter(W+"t"):
        if t.text and "WET-PIPE" in t.text:
            t.text = t.text.replace("WET-PIPE SPRINKLER", "WET-PIPE SPRINKLER"); return
nb_pkg = DocxPackage(mutate_xml(src_pkg.data, "word/document.xml", nbsp_title))
rule = {"rule": "regex", "source": "document", "pattern": r"^SECTION\s+\d{2}\s+\d{2}\s+\d{2}\s*[-–—]\s*(.+)$"}
v1, v2 = extract_fill(nb_pkg, "T", rule), fill_value(VPackage(nb_pkg.data), "T", rule)
show("fill from a title containing NBSP (transform, verifier)", (repr(v1), repr(v2), "NBSP→space" if " " not in v1 else "NBSP kept"))
rule_strict = dict(rule, flexible_whitespace=False)
show("same with flexible_whitespace=false", repr(extract_fill(nb_pkg, "T", rule_strict)))
# multi-anchor consensus: two anchors followed by DIFFERENT titles -> ambiguous; occurrence first -> first
def two_anchors(root):
    body = root.find(W+"body"); pars = body.findall(W+"p")
    p = etree.Element(W+"p"); r = etree.SubElement(p, W+"r"); etree.SubElement(r, W+"t").text = "SECTION 21 13 13"
    p2 = etree.Element(W+"p"); r2 = etree.SubElement(p2, W+"r"); etree.SubElement(r2, W+"t").text = "A DIFFERENT TITLE"
    pars[6].addnext(p); p.addnext(p2)
clean2 = DocxPackage(mutate_xml(build("21 13 13", "WET-PIPE SPRINKLER SYSTEMS"), "word/document.xml", two_anchors))
pa = {"rule": "paragraph_after", "source": "document", "anchor_regex": r"^SECTION \d{2} \d{2} \d{2}$"}
for rl in (pa, dict(pa, occurrence="first")):
    try:
        t_val = extract_fill(clean2, "T", rl)
    except JobException as e:
        t_val = f"refused [{e.code}]"
    try:
        v_val = fill_value(VPackage(clean2.data), "T", rl)
    except Exception as e:
        v_val = f"raises {type(e).__name__}"
    show(f"paragraph_after two anchors, different titles, occurrence={rl.get('occurrence','all')}", (t_val, v_val))
# placed_words recursion
spec = {"ops": [{"op": "apply_header_footer_template", "params": {"slot_fills": {"S": {"rule": "first_match", "rules": [
    {"rule": "regex", "pattern": "x"}, {"rule": "first_match", "rules": [{"rule": "literal", "value": "DEEP LITERAL"}]}]}}}}]}
show("placed_words sees a literal nested two levels deep", C.placed_words(spec))
