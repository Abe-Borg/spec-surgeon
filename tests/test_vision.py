"""Claude looks at pages (vision.py): ``view_pages`` in the conversation, and Claude's visual check of the preview
samples — advisory: it can stop a run until the user acknowledges it, never pass anything. Scripted client only."""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from fake_claude import FakeClient, api_error, raise_on_enter, resp, text, tool
from make_fixtures import build
from spec_surgeon import llm, vision
from spec_surgeon.gui import Session
from spec_surgeon.preview import SampleView
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
    assert content[1]["source"]["data"] != content[3]["source"]["data"]           # before and after are different pages
    assert rv.findings == [{"severity": "block", "file": s.source.name, "page": 1, "text": "Body text is white on white."},
                           {"severity": "warn", "file": "", "page": None, "text": "Something odd."}]
    assert len(rv.blocking()) == 1 and rv.pages_sent == 2
    assert "advisory" in rv.to_text() and "PASS" not in rv.to_text()


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


def test_a_blocking_finding_stops_run_until_acknowledged_for_this_preview(folder, tmp_path):
    s = Session(folder=folder, out_root=tmp_path / "out")
    s.load_spec(SPECS / "remove-all-headers-footers.json")
    s.make_preview(render=False, log=lambda m: None)
    s.preview.review = VisualReview(findings=[{"severity": "block", "file": "", "page": None, "text": "logo squashed"}])
    with pytest.raises(ValueError, match="visual check flagged 1 problem"):
        s.run(False, False, lambda m: None)
    with pytest.raises(ValueError, match="not the current one"):
        s.acknowledge_review("an-old-token")
    s.acknowledge_review(s.preview.token)
    assert s.run_blocked_reason() == ""
    assert "PASS 2" in s.run(False, False, lambda m: None)
    s.make_preview(render=False, log=lambda m: None)                      # a new preview: a new review, no carry-over
    s.preview.review = VisualReview(findings=[{"severity": "block", "file": "", "page": None, "text": "again"}])
    assert s.run_blocked_reason()


def test_the_gate_covers_the_custom_transform_branch_too(folder, tmp_path):
    s = Session(folder=folder, out_root=tmp_path / "out")
    s.generated, s.generated_token = SimpleNamespace(source="x", select={}, params={}), "tok"
    s.generated_preview = SimpleNamespace(review=VisualReview(findings=[{"severity": "block", "text": "x"}]), review_ack="")
    with pytest.raises(ValueError, match="visual check"):
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
    conversation = Conversation("remove all headers and footers", sorted(folder.glob("*.docx")), client=fake,
                                work_dir=tmp_path / "chat", max_image_tokens=12000)
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
    s = Session(folder=folder, out_root=tmp_path / "out",
                client=FakeClient(resp(tool("submit_review", {"summary": "Headers removed.", "findings": []}))))
    s.load_spec(SPECS / "remove-all-headers-footers.json")
    s.make_preview(render=True, log=lambda m: None)
    pv = s.preview
    assert pv.samples and all(x.source_pdf and x.output_pdf and 1 in x.look_pages for x in pv.samples)
    assert pv.review is not None and not pv.review.skipped and pv.review.findings == []
    assert (pv.out_dir / "visual_review.json").is_file() and "VISUAL CHECK" in (pv.out_dir / "PREVIEW.md").read_text()
    sent = s.client.requests[0]["messages"][0]["content"]
    images = [b["source"]["data"] for b in sent if b["type"] == "image"]
    assert len(images) >= 2 and images[0] != images[1]                         # the header was removed: pages differ
