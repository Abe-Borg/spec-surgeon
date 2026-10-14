import copy, json, shutil, sys, tempfile
from pathlib import Path
import pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3] / "tests"))
from lxml import etree
from make_fixtures import build
from test_adaptive_messy import mutate_xml
from spec_surgeon.package import DocxPackage, W
from spec_surgeon.ops import get_op, merge_fragments
from spec_surgeon.verify import run_suite
from spec_surgeon.verify.reader import VPackage
from spec_surgeon.batch import run_batch
AUTHOR = "Abraham Borg"
def show(label, val): print(f"  {label}: {val}")
def fails(rec): return [c.id for c in rec.checks if c.status == "FAIL"]
def run_op(src_bytes, op, select, params):
    pkg = DocxPackage(src_bytes); frag = get_op(op).plan(pkg, select, params); plan = merge_fragments([frag])
    work = pkg.copy(); get_op(op).apply(work, frag); out = work.output_bytes()
    return plan, out, run_suite(src_bytes, out, plan.to_dict(), "s.docx", "o.docx")

print("== V5c: does anything check that rPrChange sits LAST in rPr? ==")
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
plan, out, rec = run_op(tf, "replace_text", {"paragraphs": {"text_regex": "Owner's insurer"}, "scope": ["document"]},
                        {"find": "FM Global", "replace": "XYZ", "allow_tracked_format_split": True, "author": AUTHOR})
def move_first(root):
    for chg in root.iter(W+"rPrChange"):
        rpr = chg.getparent(); rpr.remove(chg); rpr.insert(0, chg)
bad = mutate_xml(out, "word/document.xml", move_first)
r2 = run_suite(tf, bad, plan.to_dict(), "s", "o")
show("PLANTED: every rPrChange moved to FIRST child of rPr", ("caught by " + str(fails(r2))) if not r2.passed else "NOT CAUGHT")
show("  V5c status/summary", (r2.get("V5c").status, r2.get("V5c").summary[:120]))
def drop_snapshot(root):
    for chg in root.iter(W+"rPrChange"):
        for c in list(chg): chg.remove(c)
r3 = run_suite(tf, mutate_xml(out, "word/document.xml", drop_snapshot), plan.to_dict(), "s", "o")
show("PLANTED: rPrChange snapshots emptied", ("caught by " + str(fails(r3))) if not r3.passed else "NOT CAUGHT")

print("\n== V6/V7 on the new ops with a real render ==")
soffice = shutil.which("soffice") or shutil.which("libreoffice")
show("soffice", soffice)
if soffice:
    tmp = Path(tempfile.mkdtemp()); d = tmp / "in"; d.mkdir()
    (d / "21 05 00 – CLEAN.docx").write_bytes(build("21 05 00", "COMMON WORK RESULTS"))
    spec = {"request": "insert 'F.\tProvide hydraulic calculation submittals.' after 1.01 SUMMARY", "summary": "s", "mode": "content",
            "ops": [{"op": "insert_paragraph", "select": {"paragraphs": {"text_regex": r"^1\.01\tSUMMARY"}},
                     "params": {"text": "F.\tProvide hydraulic calculation submittals.", "position": "after", "author": AUTHOR}}]}
    b = run_batch([str(d)], "edit-spec", {}, tmp / "out", workers=1, renderer="libreoffice", do_render=True, spec=spec)
    f = b.files[0]
    show("insert_paragraph batch status", f.status)
    for cid in ("V6", "V7", "V9"):
        c = f.record.get(cid); show(f"  {cid}", (c.status, c.summary[:150]))
    spec2 = {"request": "delete A.", "summary": "s", "mode": "content",
             "ops": [{"op": "delete_paragraphs", "select": {"paragraphs": {"text_regex": r"^A\.\tThe Owner's insurer"}}, "params": {"tracked": True, "author": AUTHOR}}]}
    b2 = run_batch([str(d)], "edit-spec", {}, tmp / "out2", workers=1, renderer="libreoffice", do_render=True, spec=spec2)
    f2 = b2.files[0]
    show("delete_paragraphs batch status", f2.status)
    for cid in ("V6", "V7"):
        c = f2.record.get(cid); show(f"  {cid}", (c.status, c.summary[:150]))

print("\n== does the compiler's catalog / system prompt tell the model about expect ranges and the new params? ==")
from spec_surgeon.catalog import op_catalog
cat = json.dumps(op_catalog(examples=False))
for needle in ['"min"', "min/max", "range", "flexible_whitespace", "ignore_formatting_noise", "allow_tracked_format_split", "first_match", "has_bold", "between_paragraphs", "occurrence", "PRIOR PASS"]:
    show(f"catalog mentions {needle!r}", needle in cat)
from spec_surgeon import compiler as C
show("SYSTEM prompt mentions 'expect'", "expect" in C.SYSTEM)
show("catalog 'expect_checks'", op_catalog(examples=False)["expect_checks"])
