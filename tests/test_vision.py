"""Claude looks at pages (vision.py): ``view_pages`` in the conversation, and Claude's visual check of the preview
samples — advisory findings accompany preview approval and never replace engine verification. Scripted client only."""
from __future__ import annotations

import copy
import inspect
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from fake_claude import FakeClient, api_error, raise_on_enter, resp, text, tool
from make_fixtures import build
from spec_surgeon import llm, render, vision
from spec_surgeon.editspec import load_spec
from spec_surgeon.gui import Session
from spec_surgeon.preview import GeneratedPreview, SampleView, make_preview, render_samples
from spec_surgeon.vision import VisualReview, review_samples

SPECS = Path(__file__).resolve().parent.parent / "examples" / "specs"
REMOVE = json.loads((SPECS / "remove-all-headers-footers.json").read_text(encoding="utf-8"))


def make_pdf(path: Path, pages=1, size=(612, 792), words="page") -> Path:
    from spec_surgeon.verify.geometry import fitz
    doc = fitz.open()
    for i in range(pages):
        pg = doc.new_page(width=size[0], height=size[1])
        pg.insert_text((72, 72), f"{words} {i + 1}", fontsize=14)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture
def folder(tmp_path) -> Path:
    d = tmp_path / "specs"
    d.mkdir()
    for i in range(2):
        (d / f"21 0{i} 00 – SPEC {i}.docx").write_bytes(build(f"21 0{i} 00", f"SPEC {i}"))
    return d


def sample(tmp_path, name="21 00 00 – SPEC 0.docx", pages=(1,)):
    return SampleView(source=tmp_path / name, output=tmp_path / "o.docx", passed=True,
                      source_pdf=make_pdf(tmp_path / "pdf" / "before" / "x.pdf", words="before"),
                      output_pdf=make_pdf(tmp_path / "pdf" / "after" / "x.pdf", words="after"), look_pages=list(pages))


def test_pages_are_rasterised_small_enough_for_many_image_requests(tmp_path):
    pdf = make_pdf(tmp_path / "tabloid.pdf", size=(792, 1224))           # 11 x 17 in
    png = vision.rasterize_pages(pdf, [1], tmp_path / "png")[0]
    w, h = vision.png_size(png)
    assert max(w, h) <= vision.LONG_EDGE_PX and vision.est_tokens(png) <= vision.TOKENS_PER_IMAGE_CAP
    block = vision.image_block(png)
    assert block["type"] == "image" and block["source"]["media_type"] == "image/png"


def test_the_visual_check_sends_labelled_before_and_after_pages_and_validates_findings(tmp_path):
    s = sample(tmp_path)
    fake = FakeClient(resp(tool("submit_review", {"summary": "The header is gone on page 1.", "findings": [
        {"severity": "block", "file": s.source.name, "page": 1, "text": "Body text is white on white."},
        {"severity": "loud", "file": "made-up.docx", "page": 7, "text": "Something odd."},
        {"severity": "info", "text": ""}]})))
    rv = review_samples("REQUEST: remove the headers", [s], tmp_path / "review", lambda: fake)
    req = fake.requests[0]
    content = req["messages"][0]["content"]
    assert [b["type"] for b in content] == ["text", "image", "text", "image", "text"]
    assert content[0]["text"].endswith("page 1 — BEFORE") and content[2]["text"].endswith("page 1 — AFTER")
    assert "remove the headers" in content[-1]["text"] and "tool_choice" not in req
    # one call, never repeated: its system prompt is cached for the next preview, its page images are plain input
    assert "cache_control" not in req and req["system_blocks"][0]["cache_control"] == llm.SYSTEM_CACHE
    assert content[1]["source"]["data"] != content[3]["source"]["data"]           # before and after are different pages
    assert rv.findings == [{"severity": "block", "file": s.source.name, "page": 1, "text": "Body text is white on white."},
                           {"severity": "warn", "file": "", "page": None, "text": "Something odd."}]
    assert len(rv.blocking()) == 1 and rv.pages_sent == 2
    assert "advisory" in rv.to_text() and "PASS" not in rv.to_text()


def test_the_visual_check_is_told_how_many_pages_were_rendered_and_which_it_is_shown(tmp_path):
    """The deep-dive stress test: the check sees page 1 and the first page whose words moved, and said later pages
    "were not rendered" when they were rendered and not sent. It is now told each sample's page counts, the pages it
    is shown, and to call a page it cannot see not shown."""
    s = sample(tmp_path, pages=(1, 3))
    s.pages_before, s.pages_after = 17, 19
    s.source_pdf = make_pdf(tmp_path / "pdf" / "before" / "x.pdf", pages=17, words="before")
    s.output_pdf = make_pdf(tmp_path / "pdf" / "after" / "x.pdf", pages=19, words="after")
    unknown = sample(tmp_path / "b", name="21 05 00 – OTHER.docx")
    fake = FakeClient(resp(tool("submit_review", {"summary": "Looked.", "findings": []})))
    rv = review_samples("REQUEST: widen the footer boxes", [s, unknown], tmp_path / "review", lambda: fake)
    last = fake.requests[0]["messages"][0]["content"][-1]["text"]
    assert not rv.skipped and rv.pages_sent == 6
    assert "PAGES: every page of each sample was rendered, before and after" in last
    assert "  21 00 00 – SPEC 0.docx: 17 page(s) BEFORE, 19 page(s) AFTER; shown here: page 1, 3" in last
    assert "  21 05 00 – OTHER.docx: an unknown number of pages BEFORE, an unknown number of pages AFTER; " \
           "shown here: page 1" in last
    assert "never that it was not rendered" in last
    assert last.index("PAGES:") < last.index("widen the footer boxes")


def test_visual_review_preserves_the_selected_model_response_policy(tmp_path):
    fake = FakeClient(resp(tool("submit_review", {"summary": "Reviewed.", "findings": []})))
    review = review_samples("format", [sample(tmp_path)], tmp_path / "review", lambda: fake,
                            model="smaller-model", max_output_tokens=8192, effort="high")
    assert not review.skipped
    assert fake.requests[0]["model"] == "smaller-model"
    assert fake.requests[0]["max_tokens"] == 8192
    assert fake.requests[0]["output_config"] == {"effort": "high"}


@pytest.mark.parametrize("client, why", [
    (lambda: (_ for _ in ()).throw(llm.ClaudeUnavailable("no key")), "Claude is not available: no key"),
    (lambda: (_ for _ in ()).throw(RuntimeError("no key")), "Claude could not be set up: RuntimeError: no key"),
    (lambda: FakeClient(raise_on_enter(RuntimeError("503"))), "request to Claude failed"),
    (lambda: FakeClient(raise_on_enter(api_error("Overloaded", 503, "req_3"))), "HTTP 503 · request id req_3"),
    (lambda: FakeClient(resp(text("Looks fine to me."), stop="end_turn")), "without a review"),
    (lambda: FakeClient(resp(text("x"), stop="refusal", details=SimpleNamespace(category="cyber", explanation=""))),
     "declined"),
])
def test_the_visual_check_is_skipped_never_raised(tmp_path, client, why):
    rv = review_samples("r", [sample(tmp_path)], tmp_path / "review", client)
    assert rv.skipped and why in rv.skipped and rv.blocking() == []


def test_the_visual_check_keeps_its_exchange_without_the_page_images(tmp_path):
    fake = FakeClient(resp(tool("submit_review", {"summary": "Fine.", "findings": []})))
    rv = review_samples("r", [sample(tmp_path)], tmp_path / "review", lambda: fake)
    assert not rv.skipped and [e.get("role") for e in rv.transcript] == ["user", "assistant"]
    assert [b["type"] for b in rv.transcript[0]["content"]] == ["text", "image", "text", "image", "text"]
    assert rv.transcript[0]["content"][1] == {"type": "image"} and rv.transcript[1]["content"][0]["name"] == "submit_review"
    saved = json.loads(vision.save_review(rv, tmp_path).read_text(encoding="utf-8"))
    sent = fake.requests[0]["messages"][0]["content"][1]["source"]["data"]
    assert saved["transcript"] == rv.transcript and saved["seconds"] >= 0
    assert "base64" not in json.dumps(saved) and sent[:40] not in json.dumps(saved)
    defect = review_samples("r", [sample(tmp_path)], tmp_path / "r2", lambda: FakeClient(raise_on_enter(KeyError("x"))))
    assert "request to Claude failed" in defect.skipped and "Traceback" in defect.transcript[-1]["traceback"]
    api = review_samples("r", [sample(tmp_path)], tmp_path / "r3",
                         lambda: FakeClient(raise_on_enter(api_error("Overloaded", 529))))
    assert "traceback" not in api.transcript[-1] and api.transcript[-1]["error"].endswith("HTTP 529")


def test_no_images_no_call(tmp_path):
    fake = FakeClient()
    rv = review_samples("r", [SampleView(source=tmp_path / "a.docx")], tmp_path, lambda: fake)
    assert "no page images" in rv.skipped and fake.requests == []


def test_visual_findings_remain_visible_without_a_separate_delivery_gate(folder, tmp_path):
    s = Session(documents=sorted(folder.glob("*.docx")), out_root=tmp_path / "out")
    s._set_outcome(spec=load_spec(SPECS / "remove-all-headers-footers.json"), source="remove-all-headers-footers")
    s.make_preview(render=False, log=lambda m: None)
    s.preview.review = VisualReview(findings=[{"severity": "block", "file": "", "page": None, "text": "logo squashed"}])
    assert "logo squashed" in s.preview.to_text()
    assert s.run_blocked_reason() == ""
    assert "PASS 2" in s.run(False, False, lambda m: None)
    s.make_preview(render=False, log=lambda m: None)
    s.preview.review = VisualReview(findings=[{"severity": "block", "file": "", "page": None, "text": "again"}])
    assert s.run_blocked_reason() == ""


def test_visual_findings_do_not_bypass_custom_transform_source_identity(folder, tmp_path):
    s = Session(documents=sorted(folder.glob("*.docx")), out_root=tmp_path / "out")
    s.generated, s.generated_token = SimpleNamespace(source="x", select={}, params={}), "tok"
    s.generated_preview = SimpleNamespace(review=VisualReview(findings=[{"severity": "block", "text": "x"}]), review_ack="")
    assert s.run_blocked_reason() == ""
    with pytest.raises(ValueError, match="changed.*since the AI-written transform"):
        s.run(False, False, lambda m: None)


def test_view_pages_in_the_conversation_is_an_image_tool_result_with_a_budget(folder, tmp_path, monkeypatch):
    from spec_surgeon.conversation import Conversation
    monkeypatch.setattr(vision, "render_pdf", lambda docx, work, renderer="auto", log=None:
                        make_pdf(Path(work) / Path(docx).stem / "r.pdf", pages=4))
    monkeypatch.setattr(vision, "VIEW_BUDGET_TOKENS", 9000)
    name = sorted(folder.glob("*.docx"))[0].name
    fake = FakeClient(resp(tool("view_pages", {"file": name, "pages": [1, 2]}, "v1")),
                      resp(tool("view_pages", {"file": name, "pages": [3, 4]}, "v2")),
                      resp(tool("submit_editspec", {"spec": REMOVE}, "s1")))
    c = Conversation("remove all headers and footers", sorted(folder.glob("*.docx")), client=fake,
                     work_dir=tmp_path / "chat")
    t = c.start()
    first = fake.requests[1]["messages"][-1]["content"][0]
    assert first["tool_use_id"] == "v1" and [b["type"] for b in first["content"]] == ["text", "image", "text", "image"]
    second = fake.requests[2]["messages"][-1]["content"][0]
    assert second["is_error"] and "page budget" in second["content"]
    assert t.status == "ok" and len(t.images) == 2 and all(Path(p).is_file() for p in t.images)
    looked = [e for e in c.exchange if e.get("tool_results") and e["tool_results"][0]["tool_use_id"] == "v1"]
    assert looked and looked[0]["tool_results"][0]["content"].count("[image]") == 2
    assert "iVBOR" not in json.dumps(c.exchange, default=str)                       # page images never reach a record
    one_shot = Conversation("x", sorted(folder.glob("*.docx")), client=FakeClient(), mode="compile")
    assert "view_pages" not in [tool_["name"] for tool_ in one_shot.tools]


def test_page_investigation_accepts_a_configured_larger_allowance(folder, tmp_path, monkeypatch):
    from spec_surgeon.conversation import Conversation
    monkeypatch.setattr(vision, "render_pdf", lambda docx, work, renderer="auto", log=None:
                        make_pdf(Path(work) / Path(docx).stem / "r.pdf", pages=4))
    monkeypatch.setattr(vision, "VIEW_BUDGET_TOKENS", 9000)
    name = sorted(folder.glob("*.docx"))[0].name
    fake = FakeClient(resp(tool("view_pages", {"file": name, "pages": [1, 2]}, "v1")),
                      resp(tool("view_pages", {"file": name, "pages": [3, 4]}, "v2")),
                      resp(tool("submit_editspec", {"spec": REMOVE}, "s1")))
    # The full operation catalog and four page images must fit in one epoch: this test is about the image
    # allowance, a new epoch is test_contexts' subject (the headroom itself: test_contexts' headroom guard).
    conversation = Conversation("remove all headers and footers", sorted(folder.glob("*.docx")), client=fake,
                                work_dir=tmp_path / "chat", max_image_tokens=12000, max_context_tokens=200000)
    result = conversation.start()
    second = fake.requests[2]["messages"][-1]["content"][0]
    assert not second.get("is_error") and len(result.images) == 4
    assert result.status == "ok" and conversation.batch._image_tokens > vision.VIEW_BUDGET_TOKENS
    assert conversation.to_dict()["resource_policy"]["max_image_tokens"] == 12000


def test_the_visual_check_rewrite_keeps_the_approval_inputs(tmp_path):
    from spec_surgeon.preview import make_preview, write_preview_files
    d = tmp_path / "in"
    d.mkdir()
    (d / "21 00 00 – A.docx").write_bytes(build("21 00 00", "A"))
    pv = make_preview(REMOVE, [str(d)], tmp_path / "pv", do_render=False)
    pv.review = SimpleNamespace(to_text=lambda: "VISUAL CHECK: fine", to_dict=lambda: {"verdict": "ok"})
    write_preview_files(pv)
    data = json.loads((tmp_path / "pv" / "preview.json").read_text(encoding="utf-8"))
    assert data["approval_inputs"]["files"] and data["visual_review"] == {"verdict": "ok"}
    assert data["log"] == "logs/preview.log"
    assert "VISUAL CHECK: fine" in (tmp_path / "pv" / "PREVIEW.md").read_text(encoding="utf-8")


@pytest.mark.skipif(not (shutil.which("soffice") or shutil.which("libreoffice")), reason="LibreOffice not installed")
def test_real_render_keeps_the_pdfs_and_the_pages_for_the_visual_check(folder, tmp_path):
    s = Session(documents=sorted(folder.glob("*.docx")), out_root=tmp_path / "out",
                client=FakeClient(resp(tool("submit_review", {"summary": "Headers removed.", "findings": []}))))
    s._set_outcome(spec=load_spec(SPECS / "remove-all-headers-footers.json"), source="remove-all-headers-footers")
    s.make_preview(render=True, log=lambda m: None)
    pv = s.preview
    assert pv.samples and all(x.source_pdf and x.output_pdf and 1 in x.look_pages for x in pv.samples)
    assert pv.review is not None and not pv.review.skipped and pv.review.findings == []
    assert (pv.out_dir / "visual_review.json").is_file() and "VISUAL CHECK" in (pv.out_dir / "PREVIEW.md").read_text()
    sent = s.client.requests[0]["messages"][0]["content"]
    images = [b["source"]["data"] for b in sent if b["type"] == "image"]
    assert len(images) >= 2 and images[0] != images[1]                         # the header was removed: pages differ


# ---- same-named samples are told apart -------------------------------------------------------------------------------

def one_page_pdf(path: Path, words: str) -> Path:
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
                              source_pdf=one_page_pdf(tmp_path / "pdf" / sub / "before.pdf", f"{sub} before"),
                              output_pdf=one_page_pdf(tmp_path / "pdf" / sub / "after.pdf", f"{sub} after"),
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


# ---- an answer the review cannot read is a skipped review, never an exception ----------------------------------------

def _pdf(path: Path, pages=1, size=(612, 792), words="page") -> Path:
    from spec_surgeon.verify.geometry import fitz
    doc = fitz.open()
    for i in range(pages):
        pg = doc.new_page(width=size[0], height=size[1])
        pg.insert_text((72, 72), f"{words} {i + 1}", fontsize=14)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(path))
    doc.close()
    return path


def _sample(tmp_path, name="21 00 00 – SPEC 0.docx") -> SampleView:
    return SampleView(source=tmp_path / name, output=tmp_path / "o.docx", passed=True,
                      source_pdf=_pdf(tmp_path / "pdf" / "before" / "x.pdf", words="before"),
                      output_pdf=_pdf(tmp_path / "pdf" / "after" / "x.pdf", words="after"), look_pages=[1])


def _odd(content=None, usage=None, stop="tool_use"):
    """A response with any content/usage (the scripted ``resp`` always builds well-formed ones)."""
    return SimpleNamespace(content=content, stop_reason=stop, stop_details=None, model="claude-opus-5-5", usage=usage)


def _review(**inp):
    return [tool("submit_review", inp)]


GOOD_USAGE = SimpleNamespace(input_tokens=10, output_tokens=2, cache_read_input_tokens=0, cache_creation_input_tokens=0,
                             iterations=None)
BAD_USAGE = SimpleNamespace(input_tokens="lots", output_tokens=2, cache_read_input_tokens=0,
                            cache_creation_input_tokens=0, iterations=None)
UNREADABLE = {
    "findings is true": resp(tool("submit_review", {"summary": "looked", "findings": True})),        # review-5
    "findings is a number": resp(tool("submit_review", {"summary": "x", "findings": 5})),            # merge-verify-2
    "findings is a string": resp(tool("submit_review", {"summary": "x", "findings": "none"})),
    "findings is an object": resp(tool("submit_review", {"summary": "x", "findings": {"severity": "block",
                                                                                       "text": "squashed logo"}})),
    "input is a list of pairs": resp(tool("submit_review", [["summary", "x"], ["findings", []]])),
    "input is a string": resp(tool("submit_review", "summary: fine")),
    "input is None": resp(tool("submit_review", None)),
    "usage cannot be read": _odd(_review(summary="x", findings=[]), BAD_USAGE),
    "content is not a list": _odd(5, GOOD_USAGE),
    "a block without a name": _odd([{"type": "tool_use", "id": "t1", "input": {"summary": "x", "findings": []}}],
                                   GOOD_USAGE),
    # a missing field is never read as empty (the deep-dive stress test's D1)
    "findings is missing": resp(tool("submit_review", {"summary": "Nothing to see."})),
    "summary is missing": resp(tool("submit_review", {"findings": []})),
    "findings is null": resp(tool("submit_review", {"summary": "x", "findings": None})),
    "summary is not text": resp(tool("submit_review", {"summary": ["x"], "findings": []})),
    "input is empty": resp(tool("submit_review", {})),
    "a wrapper beside a field": resp(tool("submit_review", {"summary": "x", "parameters": {
        "summary": "x", "findings": [{"severity": "block", "text": "Logo squashed."}]}})),
    "a wrapper without both fields": resp(tool("submit_review", {"parameters": {
        "findings": [{"severity": "block", "text": "Logo squashed."}]}})),
    "two wrappers": resp(tool("submit_review", {"a": {"summary": "x", "findings": []},
                                                "b": {"summary": "y", "findings": []}})),
}


@pytest.mark.parametrize("name", list(UNREADABLE))
def test_an_answer_that_cannot_be_read_is_a_skipped_review_never_an_exception(tmp_path, name):
    fake = FakeClient(copy.deepcopy(UNREADABLE[name]))
    rv = review_samples("REQUEST: remove the headers", [_sample(tmp_path)], tmp_path / "review", lambda: fake)
    assert len(fake.requests) == 1                                    # the call was made; reading its answer failed
    assert rv.skipped.startswith("Claude's answer could not be read (") and rv.findings == [] and rv.summary == ""
    assert rv.blocking() == [] and "skipped: Claude's answer could not be read" in rv.to_text()
    last = rv.transcript[-1]
    assert last["turn"] == 1 and last["read_error"] and "Traceback" in last["traceback"]
    assert [e.get("role") for e in rv.transcript[:2]] == ["user", "assistant"]      # the answer itself is kept
    saved = json.loads(vision.save_review(rv, tmp_path).read_text(encoding="utf-8"))
    assert saved["skipped"] == rv.skipped and saved["transcript"][-1]["traceback"] == last["traceback"]


def test_a_review_read_part_way_keeps_nothing(tmp_path, monkeypatch):
    """A reading failure after some findings were read must not leave them behind a skip (a skipped review shows no
    findings, so a hidden `block` would stop Run with nothing on the screen)."""
    calls = []
    real = vision._finding

    def flaky(f, sent):
        calls.append(f)
        if len(calls) == 2:
            raise RuntimeError("a defect while reading the second finding")
        return real(f, sent)
    monkeypatch.setattr(vision, "_finding", flaky)
    s = _sample(tmp_path)
    fake = FakeClient(resp(tool("submit_review", {"summary": "Two problems.", "findings": [
        {"severity": "block", "file": s.source.name, "page": 1, "text": "Logo squashed."},
        {"severity": "warn", "text": "Footer moved."}]})))
    rv = review_samples("r", [s], tmp_path / "review", lambda: fake)
    assert rv.findings == [] and rv.summary == "" and rv.blocking() == []
    assert "RuntimeError: a defect while reading the second finding" in rv.skipped
    assert "flaky" in rv.transcript[-1]["traceback"]


def test_finding_items_of_any_type_are_read_or_dropped(tmp_path):
    s = _sample(tmp_path)
    fake = FakeClient(resp(tool("submit_review", {"summary": "Looked.", "findings": [
        None, 3, "a string", ["block", "x"], True,
        {"severity": "block", "file": s.source.name, "page": True, "text": "Logo squashed."},     # a bool is no page
        {"severity": ["block"], "file": {"a": 1}, "page": "1", "text": {"what": "odd"}},
        {"severity": "info", "file": s.source.name, "page": 1.0, "text": "  fine  "},
        {"severity": "warn", "text": None}]})))
    rv = review_samples("r", [s], tmp_path / "review", lambda: fake)
    assert not rv.skipped and rv.summary == "Looked."
    assert rv.findings == [
        {"severity": "block", "file": s.source.name, "page": None, "text": "Logo squashed."},
        {"severity": "warn", "file": "", "page": None, "text": "{'what': 'odd'}"},
        {"severity": "info", "file": s.source.name, "page": None, "text": "fine"}]
    assert "p.True" not in rv.to_text()


def test_a_review_nested_under_one_key_keeps_its_blocking_findings(tmp_path):
    """The deep-dive stress test's D1: Claude put its whole review under ``parameters``; the reader took the absent
    top-level findings for none and showed a clean review, its two blocking findings gone. Nested under one key with
    both fields, the review is read as it is, every finding kept, and the transcript says it was nested."""
    s = _sample(tmp_path)
    nested = {"parameters": {"summary": "The footer title now overlaps the date and is clipped.", "findings": [
        {"severity": "block", "file": s.source.name, "page": 1, "text": "Footer title overlaps the revision date."},
        {"severity": "block", "file": s.source.name, "page": 1, "text": "Footer sentence is clipped at the right."},
        {"severity": "warn", "text": "The page number sits lower than before."}]}}
    fake = FakeClient(resp(tool("submit_review", nested)))
    rv = review_samples("r", [s], tmp_path / "review", lambda: fake)
    assert not rv.skipped and rv.summary == "The footer title now overlaps the date and is clipped."
    assert [f["severity"] for f in rv.findings] == ["block", "block", "warn"] and len(rv.blocking()) == 2
    assert rv.transcript[-1] == {"turn": 1, "unwrapped": "parameters"}
    assert "[block]" in rv.to_text() and "no findings" not in rv.to_text()


def test_a_review_with_both_fields_and_no_findings_is_a_clean_review(tmp_path):
    fake = FakeClient(resp(tool("submit_review", {"summary": "Nothing to see.", "findings": []})))
    rv = review_samples("r", [_sample(tmp_path)], tmp_path / "review", lambda: fake)
    assert not rv.skipped and rv.summary == "Nothing to see." and rv.findings == []
    assert not any("unwrapped" in e for e in rv.transcript)


def test_a_client_factory_error_is_classified_like_a_call_error(tmp_path):
    def raising(exc):
        def factory():
            raise exc
        return factory

    defect = review_samples("r", [_sample(tmp_path)], tmp_path / "r1", raising(KeyError("defect")))
    assert defect.skipped == "Claude could not be set up: KeyError: 'defect'"
    assert "not available" not in defect.skipped                      # a defect is never "Claude is not available"
    entry = defect.transcript[-1]
    assert entry["turn"] == 0 and entry["error"] == "KeyError: 'defect'" and "Traceback" in entry["traceback"]
    assert "raise exc" in entry["traceback"]

    plain = review_samples("r", [_sample(tmp_path)], tmp_path / "r2", raising(RuntimeError("no key")))
    assert plain.skipped.startswith("Claude could not be set up: RuntimeError") and "traceback" in plain.transcript[-1]

    missing = review_samples("r", [_sample(tmp_path)], tmp_path / "r3",
                             raising(llm.ClaudeUnavailable("install the Claude layer")))
    assert missing.skipped == "Claude is not available: install the Claude layer"
    assert missing.transcript == [{"turn": 0, "error": "ClaudeUnavailable: install the Claude layer", "seconds": 0.0}]

    api = review_samples("r", [_sample(tmp_path)], tmp_path / "r4", raising(api_error("no key", 401, "req_9")))
    assert api.skipped == "Claude could not be set up: APIStatusError: no key · HTTP 401 · request id req_9"
    assert "traceback" not in api.transcript[-1] and api.transcript[-1]["error"].endswith("request id req_9")
    for rv in (defect, plain, missing, api):
        assert rv.findings == [] and rv.usage.calls == 0 and rv.pages_sent == 0


def test_pages_that_cannot_be_sent_are_a_skip_with_their_traceback(tmp_path, monkeypatch):
    def unreadable(png):
        raise PermissionError(f"{Path(png).name} is locked")
    monkeypatch.setattr(vision, "image_block", unreadable)
    fake = FakeClient()
    rv = review_samples("r", [_sample(tmp_path)], tmp_path / "review", lambda: fake)
    assert rv.skipped == "the pages could not be prepared (PermissionError: p1.png is locked)" and fake.requests == []
    assert "Traceback" in rv.transcript[-1]["traceback"] and rv.transcript[-1]["error"].startswith("PermissionError")


def test_a_log_that_raises_never_stops_the_review(tmp_path):
    def broken(_m):
        raise RuntimeError("the window's log is gone")
    fake = FakeClient(resp(tool("submit_review", {"summary": "Fine.", "findings": []})))
    rv = review_samples("r", [_sample(tmp_path)], tmp_path / "review", lambda: fake, log=broken)
    assert not rv.skipped and rv.summary == "Fine."


def test_the_view_pages_budget_figure_is_derived_from_est_tokens(tmp_path):
    """The ample configurable allowance still reports actual image-token costs."""
    letter = vision.rasterize_pages(_pdf(tmp_path / "letter.pdf"), [1], tmp_path / "l")[0]
    tabloid = vision.rasterize_pages(_pdf(tmp_path / "tabloid.pdf", size=(792, 1224)), [1], tmp_path / "t")[0]
    a4 = vision.rasterize_pages(_pdf(tmp_path / "a4.pdf", size=(595.28, 841.89)), [1], tmp_path / "a")[0]
    assert vision.page_dpi(612, 792) == vision.TARGET_DPI and vision.png_size(letter) == (1275, 1650)
    assert vision.page_tokens() == vision.est_tokens(letter) == 2714
    assert vision.page_tokens(792, 1224) == vision.est_tokens(tabloid)
    assert vision.page_tokens(595.28, 841.89) == vision.est_tokens(a4)
    budget, per_letter = vision.VIEW_BUDGET_TOKENS, vision.page_tokens()
    one_at_a_time = budget // per_letter
    in_threes = 3 * (budget // (3 * per_letter))
    tabloids = budget // vision.page_tokens(792, 1224)
    assert (one_at_a_time, in_threes, tabloids) == (368, 366, 301)
    src = inspect.getsource(vision)
    start = src.index("TOKENS_PER_IMAGE_CAP = ")
    comment = " ".join(src[start:src.index("SEVERITIES = ", start)].replace("#", " ").split())
    assert "VIEW_BUDGET_TOKENS = DEFAULT_MAX_IMAGE_TOKENS" in comment
    assert "Configurable investigation allowance" in comment
    assert f"{per_letter:,}" in comment and f"{one_at_a_time} one at a time" in comment
    assert f"{in_threes} in calls of three" in comment and f"{tabloids} pages of 11×17" in comment


def test_page_growth_is_visible_and_advisory(tmp_path, monkeypatch):
    source, output = tmp_path / "source.docx", tmp_path / "output.docx"
    source.write_bytes(build("21 13 13", "SPRINKLERS"))
    output.write_bytes(source.read_bytes())
    before, after = make_pdf(tmp_path / "before.pdf", pages=2), make_pdf(tmp_path / "after.pdf", pages=3)
    monkeypatch.setattr(render, "pick", lambda _: "word")
    monkeypatch.setattr(render, "render_pdfs", lambda paths, folder, *a, **kw:
                        {p: before if folder.name == "source" else after for p in paths})
    sample = SampleView(source=source, output=output, passed=True)
    warnings = []
    render_samples([sample], tmp_path / "preview", "auto", True, warnings)
    assert sample.pages_before == 2 and sample.pages_after == 3 and sample.passed
    assert sample.to_dict()["pages_after"] == 3
    assert any("pages 2 → 3" in warning for warning in warnings)
    from spec_surgeon.preview import ChainPreview, GeneratedPreview
    assert "pages: 2 → 3" in GeneratedPreview(tmp_path, "custom", samples=[sample]).to_text()
    batch_plan = SimpleNamespace(to_text=lambda **kwargs: "Planned", spec={})
    assert "pages: 2 → 3" in ChainPreview(tmp_path, batch_plan, samples=[sample]).to_text()
