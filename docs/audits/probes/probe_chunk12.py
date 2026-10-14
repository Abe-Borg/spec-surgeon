"""Independent probes for AUD-01..AUD-06 (Chunks 1-2). Read-only; writes only under tmp."""
import copy, io, json, sys, tempfile, zipfile, traceback
from pathlib import Path
import pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3] / "tests"))
from lxml import etree
from make_fixtures import build, build_rich, build_template
from test_adaptive_messy import _build_messy_inline_spec, mutate_xml
from spec_surgeon.package import DocxPackage, W
from spec_surgeon.fingerprint import fingerprint
from spec_surgeon.profile import profile, MAX_PROFILE_BYTES, _clip, bound
from spec_surgeon.editspec import validate_spec, plan_spec, SpecError, EXPECT_CHECKS
from spec_surgeon.ops import JobException, get_op
from spec_surgeon import compiler as C
from spec_surgeon.batch import run_batch, load_set_aside
AUTHOR = "Abraham Borg"

def section(t): print("\n" + "="*8, t, "="*8)
def show(label, val): print(f"  {label}: {val}")

# ---------------- AUD-01 fingerprint ----------------
section("AUD-01 fingerprint")
clean = DocxPackage(build("21 05 00", "COMMON WORK RESULTS"), name="clean.docx")
fp_clean = fingerprint(clean)
show("clean pattern_id", fp_clean["pattern_id"])
show("clean body_hygiene", json.dumps(fp_clean["body_hygiene"]))
show("'body_structure' in clean pattern src?", "body_structure" in fp_clean["pattern"])

# P1: NBSP-only mess (standalone anchors intact) -> same pattern_id as clean?
def nbsp_only(root):
    for p in root.find(W+"body").findall(W+"p"):
        for t in p.iter(W+"t"):
            if t.text and "The auditor is FM " in t.text:
                t.text = t.text.replace("FM ", "FM  ")
nb = DocxPackage(mutate_xml(build("21 05 00", "COMMON WORK RESULTS"), "word/document.xml", nbsp_only), name="nbsp.docx")
fp_nb = fingerprint(nb)
show("NBSP-only file: has_nbsp", fp_nb["body_hygiene"]["has_nbsp"])
show("NBSP-only file shares clean pattern_id?", fp_nb["pattern_id"] == fp_clean["pattern_id"])

# P2: tab followed by a space counts as multi-space?
def tab_space(root):
    for p in root.find(W+"body").findall(W+"p"):
        for t in p.iter(W+"t"):
            if t.text and t.text.startswith("The Owner's insurer"):
                t.text = " " + t.text   # "A.\t The Owner's..." -> tab + space
                return
ts = DocxPackage(mutate_xml(build("21 05 00", "COMMON WORK RESULTS"), "word/document.xml", tab_space), name="ts.docx")
show("tab+space file: has_multi_space (false positive?)", fingerprint(ts)["body_hygiene"]["has_multi_space"])

# P3: nested textbox paragraph with a SECTION line is ignored? and table-cell anchor shape
def add_textbox_and_table(root):
    body = root.find(W+"body")
    first = body.find(W+"p")
    # nested w:p inside a run (textbox-like nesting)
    r = etree.SubElement(first, W+"r")
    pict = etree.SubElement(r, W+"pict")
    txbx = etree.SubElement(pict, W+"txbxContent")
    np_ = etree.SubElement(txbx, W+"p")
    nr = etree.SubElement(np_, W+"r")
    etree.SubElement(nr, W+"t").text = "SECTION 99 99 99 - NESTED TEXTBOX TITLE"
tb = DocxPackage(mutate_xml(build("21 05 00", "COMMON WORK RESULTS"), "word/document.xml", add_textbox_and_table), name="tb.docx")
fp_tb = fingerprint(tb)
show("nested textbox SECTION line ignored (anchor shape stays)", fp_tb["body_hygiene"]["section_anchor_shape"])
show("textbox file shares clean pattern_id?", fp_tb["pattern_id"] == fp_clean["pattern_id"])

# P3b: 4-level MasterFormat number "SECTION 21 13 13.13" standalone -> anchor shape?
def four_level(root):
    for t in root.find(W+"body").iter(W+"t"):
        if t.text and t.text.strip().startswith("SECTION 21 05 00"):
            t.text = t.text.replace("21 05 00", "21 05 00.13"); return
fl = DocxPackage(mutate_xml(build("21 05 00", "COMMON WORK RESULTS"), "word/document.xml", four_level), name="fl.docx")
show("'SECTION 21 05 00.13' standalone -> anchor shape", fingerprint(fl)["body_hygiene"]["section_anchor_shape"])

# P3c: first SECTION line is standalone but a later paragraph mentions 'SECTION 21 13 13' inline -> shape?
messy = DocxPackage(_build_messy_inline_spec(), name="messy.docx")
fp_messy = fingerprint(messy)
show("messy anchor shape / pattern", (fp_messy["body_hygiene"]["section_anchor_shape"], fp_messy["pattern_id"]))
show("messy pattern src body_structure", json.dumps(fp_messy["pattern"].get("body_structure")))

# ---------------- AUD-02 profile ----------------
section("AUD-02 profile")
show("MAX_PROFILE_BYTES (spreadsheet says 18,000)", MAX_PROFILE_BYTES)
show("_clip keeps NBSP+double space", repr(_clip("A  B  C\r\nD")))
show("_clip strips a LEADING NBSP (str.strip)", repr(_clip(" lead")))
prof_messy = profile(messy)
show("messy profile hygiene", json.dumps(prof_messy.get("hygiene")))
show("messy profile size bytes", len(json.dumps(prof_messy, ensure_ascii=False)))
prof_clean = profile(clean)
show("clean profile has hygiene key?", "hygiene" in prof_clean)
# bound(): with a small limit, is hygiene dropped after first_paragraphs is emptied?
pm = copy.deepcopy(prof_messy)
pm["hygiene"] = {"has_nbsp": True}
pm["first_paragraphs"] = ["x"*80]*8
import json as _j
sizes = []
for lim in (5000, 4000, 3000, 2000, 1500, 1200, 900):
    b = bound(copy.deepcopy(pm), lim)
    sizes.append((lim, len(_j.dumps(b, ensure_ascii=False)), "hygiene" in b, len(b.get("first_paragraphs", [])) if "first_paragraphs" in b else "gone"))
show("bound(limit) -> (limit, size, hygiene kept?, first_paragraphs)", sizes)

# ---------------- AUD-03 expect ranges ----------------
section("AUD-03 expect ranges")
pkg = DocxPackage(build("21 05 00", "COMMON WORK RESULTS"))
base = {"request": "r", "summary": "s", "mode": "content",
        "ops": [{"op": "replace_text", "select": {"scope": ["document"]},
                 "params": {"find": "FM Global", "replace": "GRC", "author": AUTHOR}, "expect": {}}]}
show("EXPECT_CHECKS", EXPECT_CHECKS)
def try_expect(exp):
    s = copy.deepcopy(base); s["ops"][0]["expect"] = exp
    try:
        validate_spec(s); plan_spec(pkg, s); return "OK (planned)"
    except SpecError as e: return f"SpecError[{e.code}]"
    except JobException as e: return f"JobException[{e.code}]"
for exp in [{"text_diffs": {"max": 4}}, {"text_diffs": {"max": 3}}, {"text_diffs": {"min": True}},
            {"text_diffs": {"min": 1.5}}, {"text_diffs": {"min": 5, "max": 1}}, {"text_diffs": {"min": -3}},
            {"text_diffs": {}}, {"property_targets": {"min": 1}}, {"header_footer_slots": {"min": 1}},
            {"rev_delta": {"ins": {"min": 1, "max": 4}, "delx": 0}}, {"rev_delta": {"ins": True}},
            {"members_added": {"min": 0, "max": 0}}]:
    show(f"expect {exp}", try_expect(exp))

# ---------------- AUD-04 compiler dry-plan gate ----------------
section("AUD-04 compiler dry-plan gate")
tmp = Path(tempfile.mkdtemp())
d = tmp / "batch"; d.mkdir()
(d / "21 05 00 – CLEAN.docx").write_bytes(build("21 05 00", "COMMON WORK RESULTS"))
(d / "21 13 13 – MESSY.docx").write_bytes(_build_messy_inline_spec())
only_messy = tmp / "only_messy"; only_messy.mkdir()
(only_messy / "21 13 13 – MESSY.docx").write_bytes(_build_messy_inline_spec())
def gate(spec, folder, sources):
    b = C._Batch(sorted(folder.glob("*.docx")), None)
    return C.check_submission(spec, b, sources)
# (a) spans-runs on the only representative: neither fatal nor pattern-sensitive
spec_spans = {"request": "replace FM Global with GRC", "summary": "s", "mode": "content",
              "ops": [{"op": "replace_text", "select": {"scope": ["document"]},
                       "params": {"find": "FM Global", "replace": "GRC", "author": AUTHOR}}]}
try:
    get_op("replace_text").plan(DocxPackage(_build_messy_inline_spec()), {"scope": ["document"]}, spec_spans["ops"][0]["params"])
except JobException as e:
    show("dry plan of spec on MESSY alone raises", e.code)
show("check_submission (only MESSY in batch, 0 files plan, code spans-runs)", gate(spec_spans, only_messy, ["replace FM Global with GRC"]))
# (b) not-found on the only representative (pattern-sensitive) -> rejected
spec_nf = copy.deepcopy(spec_spans); spec_nf["ops"][0]["params"]["find"] = "ZZZ NOT THERE"; spec_nf["request"] = "replace ZZZ NOT THERE with GRC"
show("check_submission (only CLEAN, code not-found)", gate(spec_nf, d, ["replace ZZZ NOT THERE with GRC"]))
# (c) tracked-format on the only representative (rich fixture) -> ?
rich_dir = tmp / "rich"; rich_dir.mkdir(); (rich_dir / "rich.docx").write_bytes(build_rich())
spec_tf = {"request": "replace Hangers with Supports", "summary": "s", "mode": "content",
           "ops": [{"op": "replace_text", "select": {}, "params": {"find": "Hangers", "replace": "Supports", "author": AUTHOR}}]}
try:
    get_op("replace_text").plan(DocxPackage(build_rich()), {}, spec_tf["ops"][0]["params"])
except JobException as e:
    show("dry plan on rich raises", e.code)
show("check_submission (only rich, code tracked-format)", gate(spec_tf, rich_dir, ["replace Hangers with Supports"]))
# (d) fatal on a minority file: conflict (two ops touching the same block) -> rejected even though... 
spec_conf = {"request": "replace FM Global with GRC and Global with World", "summary": "s", "mode": "content",
             "ops": [{"op": "replace_text", "select": {"scope": ["document"]}, "params": {"find": "FM Global", "replace": "GRC", "author": AUTHOR}},
                     {"op": "replace_text", "select": {"scope": ["document"]}, "params": {"find": "Global", "replace": "World", "author": AUTHOR}}]}
show("check_submission (CLEAN+MESSY, conflict on CLEAN)", gate(spec_conf, d, ["replace FM Global with GRC and Global with World"]))
# (e) zero-file batch
empty = tmp / "empty"; empty.mkdir()
show("check_submission (EMPTY batch)", gate(spec_spans, empty, ["replace FM Global with GRC"]))
# (f) every JobException code the ops can raise vs. the gate's two sets
import re, glob
codes = set()
for f in [str(p) for p in (pathlib.Path(__file__).resolve().parents[3] / "src" / "spec_surgeon" / "ops").glob("*.py")] + [str(pathlib.Path(__file__).resolve().parents[3] / "src" / "spec_surgeon" / "editspec.py")]:
    txt = Path(f).read_text()
    for m in re.finditer(r'JobException\((?:[^()]|\([^()]*\))*?,\s*"([a-z][a-z-]+)"', txt, re.S):
        codes.add(m.group(1))
unclassified = sorted(codes - C.FATAL_SPEC_CODES - C.PATTERN_SENSITIVE_CODES)
show("codes ops can raise", sorted(codes))
show("codes the gate silently ignores (neither fatal nor pattern-sensitive)", unclassified)

# ---------------- AUD-05 set-aside manifest / FAIL outputs ----------------
section("AUD-05 set-aside manifest")
out1 = tmp / "pass1"
spec_hf = json.loads(Path(str(pathlib.Path(__file__).resolve().parents[3] / "examples" / "specs" / "apply-header-footer-template.json")).read_text(encoding="utf-8"))
tpl = tmp / "00 00 00 TEMPLATE.docx"; tpl.write_bytes(build_template())
spec_hf["ops"][0]["params"]["template"] = str(tpl)
b1 = run_batch([str(d)], "edit-spec", {}, out1, workers=1, renderer="none", do_render=False, spec=spec_hf)
show("pass1 counts", b1.counts())
man = load_set_aside(out1)
show("manifest keys", sorted(man))
item = man["files"][0]
show("set-aside item keys", sorted(item))
show("set-aside item code/category", (item["code"], item["category"]))
show("files in outputs/ after pass1 (EXCEPTION file should be absent)", sorted(p.name for p in (out1/"outputs").iterdir()))
show("load_set_aside(dir/run) resolution", load_set_aside(out1 / "run") if (out1/"run").exists() else "no run/ subdir in this layout")
try:
    load_set_aside(tmp / "nowhere")
except FileNotFoundError as e:
    show("load_set_aside(missing)", f"FileNotFoundError: {e}")
# a verification FAIL leaves its output in outputs/? (monkeypatch run_suite to fail V2)
import spec_surgeon.batch as B
real_run_suite = B.run_suite
def failing_suite(src, out, plan, s, o):
    rec = real_run_suite(src, out, plan, s, o)
    from spec_surgeon.verify import CheckResult
    rec.set(CheckResult("V2", "Text preservation", "FAIL", "planted"))
    return rec
B.run_suite = failing_suite
out_fail = tmp / "failrun"
spec_rt = {"request": "replace FM Global with GRC", "summary": "s", "mode": "content",
           "ops": [{"op": "replace_text", "select": {"scope": ["document"]}, "params": {"find": "FM Global", "replace": "GRC", "author": AUTHOR}}]}
clean_dir = tmp / "clean_only"; clean_dir.mkdir(); (clean_dir / "21 05 00 – CLEAN.docx").write_bytes(build("21 05 00", "COMMON WORK RESULTS"))
bf = run_batch([str(clean_dir)], "edit-spec", {}, out_fail, workers=1, renderer="none", do_render=False, spec=spec_rt)
B.run_suite = real_run_suite
show("planted V2 FAIL: counts", bf.counts())
show("planted V2 FAIL: files left in outputs/", sorted(p.name for p in (out_fail/"outputs").iterdir()))
mf = load_set_aside(out_fail)
show("planted V2 FAIL: manifest code/category/details", (mf["files"][0]["code"], mf["files"][0]["category"], mf["files"][0]["details"]))
show("planted V2 FAIL: V9 status", [c.status + ":" + c.summary[:60] for c in bf.files[0].record.checks if c.id == "V9"])
print("\nTMP:", tmp)
