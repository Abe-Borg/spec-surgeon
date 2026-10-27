"""Safe mode's buttons (``recipes``): every button builds a fixed spec from its form
and the attachments, the spec passes the one gate every submission passes, and the window's preview → approve → run →
verify carries it out with no AI call — no visual check, no recovery — on clean and dirty documents alike. What a
button cannot do on a document is said beside the copies, and can be handed to Default mode."""
from __future__ import annotations

from pathlib import Path

import pytest
from docx import Document
from docx.shared import Pt

from make_fixtures import DIRTY_KINDS, build, build_dirty, build_reference_spec, build_rich, build_sized, build_template
from spec_surgeon import compiler, llm, preview, recipes, vision
from spec_surgeon.document_task import SAFE_NO_CONVERSATION, WORK_TODO, DocumentTask
from spec_surgeon.gui import Session
from spec_surgeon.package import DocxPackage
from spec_surgeon.settings import MODES, Settings, load_settings, save_settings


@pytest.fixture(scope="module")
def folder(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("recipes")
    for num, title in (("21 13 13", "WET-PIPE SPRINKLER SYSTEMS"), ("21 13 16", "DRY-PIPE SPRINKLER SYSTEMS")):
        (d / f"{num} – {title}.docx").write_bytes(build(num, title))
    (d / "tpl").mkdir()
    (d / "tpl" / "00_00_00_TEMPLATE.docx").write_bytes(build_template())
    (d / "ref").mkdir()
    (d / "ref" / "08 71 00 - DOOR HARDWARE (ACME).docx").write_bytes(build_reference_spec())
    return d


def docs(folder: Path):
    return sorted(folder.glob("*.docx"))


def quiet(_m):
    pass


def built_and_gated(recipe_id, inputs, files, template=None, references=()):
    ctx = recipes.Context(list(files), template, list(references))
    built = recipes.build(recipe_id, inputs, ctx)
    outcome, errors = compiler.compose_submission(built.spec, compiler._Batch(files, template, input_files=list(references)),
                                                  [built.request, *built.sources])
    return built, outcome, errors


def plain_template(path: Path) -> Path:
    """A Word file with no header or footer at all: nothing to copy."""
    doc = Document()
    doc.add_paragraph("SECTION 01 00 00")
    doc.add_paragraph("GENERAL")
    doc.save(path)
    return path


# ---- the catalog ----------------------------------------------------------------------------------------------------

def test_every_button_is_in_a_group_with_a_form_the_panel_can_render():
    seen = set()
    for item in recipes.catalog():
        assert item["group"] in recipes.GROUPS and item["id"] not in seen
        seen.add(item["id"])
        assert item["label"] and item["summary"].endswith(".")
        for f in item["fields"]:
            assert f["kind"] in {"text", "pairs", "choice", "check", "picture", "table", "slots", "roles"}
            if f["kind"] == "choice":
                assert f["default"] in {o["value"] for o in f["options"]}
    assert seen == set(recipes.BY_ID)
    # the owner's list (2026-10-23): first set and second set, nothing dropped back in; and the body button
    assert seen == {"headers_footers_template", "remove_headers_footers", "watermark", "swap_logo", "font_template",
                    "find_replace", "accept_changes", "remove_comments", "remove_highlight", "remove_hidden_text",
                    "remove_empty_paragraphs", "name_files", "title_property", "track_changes_off", "body_formatting",
                    "clean_pdf"}


def test_tracking_is_offered_only_where_the_operation_can_track_and_is_off_by_default():
    with_check = {r.id for r in recipes.RECIPES if any(f.name == "tracked" for f in r.fields)}
    assert with_check == {"find_replace", "remove_hidden_text", "remove_empty_paragraphs"}
    assert recipes.TRACKED.default is False


# ---- the template kinds ---------------------------------------------------------------------------------------------

def test_a_prepared_template_fills_number_and_title_from_each_document_and_asks_for_the_rest(folder):
    kind = recipes.template_kind(folder / "tpl" / "00_00_00_TEMPLATE.docx")
    assert kind["kind"] == "prepared" and kind["sections"] == 2
    assert kind["typed_slots"] == ["ISSUED FOR PERMIT"]
    assert set(kind["filled_slots"]) == {"XX XX XX", "SECTION_NUMBER", "SECTION_TITLE"}


def test_an_ordinary_spec_is_a_reference_template(folder):
    kind = recipes.template_kind(folder / "ref" / "08 71 00 - DOOR HARDWARE (ACME).docx")
    assert kind["kind"] == "reference" and set(kind["reference_slots"]) == {"SECTION_NUMBER", "SECTION_TITLE"}


def test_a_template_without_headers_or_footers_is_unusable_with_the_reason(tmp_path):
    kind = recipes.template_kind(plain_template(tmp_path / "plain.docx"))
    assert kind["kind"] == "unusable" and "no header or footer" in kind["reason"]
    assert recipes.template_kind(None)["kind"] == "none"


def test_slot_names_for_the_number_and_title(folder):
    assert [recipes.slot_role(n) for n in ("XX XX XX", "SECTION_NUMBER", "section number", "{{TITLE}}"[2:-2],
                                           "ISSUED FOR PERMIT")] == ["number", "number", "number", "title", ""]


# ---- every button through the gate and the engine -------------------------------------------------------------------

CASES = {
    "headers_footers_template": {"slots": {"ISSUED FOR PERMIT": "ISSUED FOR CONSTRUCTION"}},
    "remove_headers_footers": {},
    "watermark": {"text": "NOT FOR CONSTRUCTION"},
    "find_replace": {"pairs": [{"find": "sprinkler", "replace": "fire sprinkler"}]},
    "accept_changes": {},
    "remove_comments": {},
    "remove_highlight": {},
    "remove_hidden_text": {},
    "remove_empty_paragraphs": {},
    "name_files": {},
    "title_property": {"pattern": "SECTION {number} - {title}"},
    "track_changes_off": {},
    "font_template": {},
}


@pytest.mark.parametrize("recipe_id", sorted(CASES))
def test_each_button_previews_and_runs_to_verified_copies_without_any_ai_call(recipe_id, folder, tmp_path,
                                                                               monkeypatch):
    def never(*a, **k):
        raise AssertionError("a Safe-mode button makes no AI call")
    monkeypatch.setattr(vision, "review_samples", never)
    monkeypatch.setattr(llm, "client", never)
    monkeypatch.setattr(preview, "render_samples", lambda *a, **k: None)   # the pages, without a renderer
    template = folder / ("ref/08 71 00 - DOOR HARDWARE (ACME).docx" if recipe_id == "font_template"
                         else "tpl/00_00_00_TEMPLATE.docx")
    task = DocumentTask(Session(out_root=tmp_path / "out"), render=True, mode="safe")   # key "present" (conftest)
    task.attach(docs(folder))
    task.attach([template], template=True)
    before = {p: p.read_bytes() for p in docs(folder)}
    view = task.perform("recipe", recipe=recipe_id, inputs=CASES[recipe_id])
    assert view["stage"] == "preview", [m["text"] for m in task.messages]
    assert task.session.model_free and view["action"] == "Create edited copies"
    assert view["messages"][-1]["role"] == "user"           # the button's sentence, as what the user asked
    view = task.perform("primary", token=view["token"])
    assert view["stage"] == "done" and len(task.results) == 2, view["left_untouched"]
    assert view["left_untouched"] == []
    assert {p: p.read_bytes() for p in before} == before     # originals untouched
    for record in task.session.last_batch.files:
        assert record.status == "PASS"
    assert task.history and task.generation == 1             # the next button builds on the copies


def test_the_button_sentence_is_what_a_later_conversation_hears(folder, tmp_path):
    task = DocumentTask(Session(out_root=tmp_path / "out", visual_check=False), render=False, mode="safe")
    task.attach(docs(folder))
    view = task.perform("recipe", recipe="remove_highlight", inputs={})
    task.perform("primary", token=view["token"])
    assert any("highlight" in line.lower() for line in task._history_in_scope())


def test_dirty_documents_plan_or_are_left_untouched_with_a_reason_never_half_edited(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "client", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no AI")))
    files = []
    for kind in DIRTY_KINDS:
        path = tmp_path / f"dirty-{kind}.docx"
        path.write_bytes(build_dirty(kind))
        files.append(path)
    task = DocumentTask(Session(out_root=tmp_path / "out", visual_check=False), render=False, mode="safe")
    task.attach(files)
    view = task.perform("recipe", recipe="title_property", inputs={"pattern": "SECTION {number} - {title}"})
    assert view["stage"] == "preview"
    untouched = {i["name"] for i in view["set_aside"]}
    assert "dirty-empty_body.docx" in untouched                # no SECTION line to read: set aside, with its reason
    assert all(i["reason"] for i in view["set_aside"])
    task.perform("primary", token=view["token"])
    left = {e["name"]: e["reason"] for e in task.view()["left_untouched"]}
    assert "dirty-empty_body.docx" in left and left["dirty-empty_body.docx"]
    for record in task.session.last_batch.files:
        assert record.status in {"PASS", "EXCEPTION"}            # a copy passes every check, or none is made


def test_an_unusable_form_is_said_once_and_nothing_is_previewed(folder, tmp_path):
    task = DocumentTask(Session(out_root=tmp_path / "out", visual_check=False), render=False, mode="safe")
    task.attach(docs(folder))
    view = task.perform("recipe", recipe="find_replace", inputs={"pairs": [{"find": "owner", "replace": "Owner"}]})
    assert view["stage"] == "empty" and task.session.spec is None
    assert "changes only capitals" in task.messages[-1]["text"]
    view = task.perform("recipe", recipe="headers_footers_template", inputs={})
    assert "Attach your template first" in task.messages[-1]["text"] and task.session.spec is None
    view = task.perform("recipe", recipe="watermark", inputs={"action": "add", "text": ""})
    assert "watermark's words" in task.messages[-1]["text"]


def test_a_template_with_nothing_to_fill_is_refused_with_default_mode_offered(folder, tmp_path):
    files = docs(folder)
    with pytest.raises(recipes.RecipeError, match="Default mode"):
        recipes.build("headers_footers_template", {}, recipes.Context(files, plain_template(tmp_path / "p.docx")))


def test_a_prepared_template_needs_its_typed_values(folder):
    files = docs(folder)
    template = folder / "tpl" / "00_00_00_TEMPLATE.docx"
    with pytest.raises(recipes.RecipeError, match="ISSUED FOR PERMIT"):
        recipes.build("headers_footers_template", {"slots": {}}, recipes.Context(files, template))
    built, outcome, errors = built_and_gated("headers_footers_template",
                                             {"slots": {"ISSUED FOR PERMIT": "ISSUED FOR BID"}}, files, template)
    assert errors == [] and built.spec["mode"] == "untracked" and built.sources == ["ISSUED FOR BID"]
    fills = built.spec["ops"][0]["params"]["slot_fills"]
    assert fills["ISSUED FOR PERMIT"] == {"rule": "literal", "value": "ISSUED FOR BID"}
    assert fills["XX XX XX"] == recipes.REFERENCE_FILLS["SECTION_NUMBER"]


def test_a_reference_template_fills_only_the_values_its_headers_show(folder):
    files = docs(folder)
    template = folder / "ref" / "08 71 00 - DOOR HARDWARE (ACME).docx"
    built, outcome, errors = built_and_gated("headers_footers_template", {}, files, template)
    params = built.spec["ops"][0]["params"]
    assert errors == [] and params["slot_markers"] == ["reference"] and built.spec["mode"] == "formatting"
    assert set(params["slot_fills"]) == {"SECTION_NUMBER", "SECTION_TITLE"} and built.sources == []


# ---- the words a button places are the user's, and only those -------------------------------------------------------

def test_typed_words_are_the_only_words_a_button_may_place(folder):
    files = docs(folder)
    built, _, errors = built_and_gated("find_replace", {"pairs": [{"find": "sprinkler", "replace": "fire sprinkler"},
                                                                  {"find": "SYSTEMS", "replace": ""}]}, files)
    assert errors == [] and built.sources == ["fire sprinkler"]
    built, _, errors = built_and_gated("watermark", {"text": "DRAFT"}, files)
    assert errors == [] and built.sources == ["DRAFT"]
    # a spec whose placed words were not typed is refused by the gate (the same gate the conversation passes)
    built.spec["ops"][0]["params"]["text"] = "PRELIMINARY"
    _, errors = compiler.compose_submission(built.spec, compiler._Batch(files, None), [built.request, *built.sources])
    assert errors and "never gave them verbatim" in " ".join(errors)


def test_the_tracked_checkbox_sets_the_mode_and_clean_copies_are_the_default(folder):
    files = docs(folder)
    built, _, _ = built_and_gated("remove_hidden_text", {}, files)
    assert built.spec["mode"] == "untracked" and built.spec["ops"][0]["params"]["tracked"] is False
    built, _, errors = built_and_gated("remove_hidden_text", {"tracked": True}, files)
    assert errors == [] and built.spec["mode"] == "content" and built.spec["ops"][0]["params"]["tracked"] is True
    built, _, errors = built_and_gated("find_replace", {"pairs": [{"find": "sprinkler", "replace": "x"}],
                                                        "tracked": True}, files)
    assert errors == [] and built.spec["mode"] == "content"


def test_the_font_button_reads_the_template_body_font_and_the_batch_body_sizes(tmp_path):
    nine = tmp_path / "nine.docx"
    nine.write_bytes(build_sized())
    ten = tmp_path / "ten.docx"
    ten.write_bytes(build("21 13 16", "DRY-PIPE SPRINKLER SYSTEMS"))
    template = tmp_path / "ref.docx"
    template.write_bytes(build_reference_spec())
    built, _, errors = built_and_gated("font_template", {}, [nine, ten], template)
    assert errors == []
    from spec_surgeon.profile import body_text_fonts
    want = body_text_fonts(DocxPackage.from_path(template))[0]
    sizes = sorted({body_text_fonts(DocxPackage.from_path(p))[0]["size_pt"] for p in (nine, ten)})
    op = built.spec["ops"][0]["params"]
    assert op["font"] == want["font"] and op["size"] == want["size_pt"] and op["size_from"] == sizes
    assert built.spec["assertions"][0]["size_from"] == sizes


def test_a_template_with_no_one_body_font_is_refused(tmp_path, folder):
    doc = Document()
    for i, (font, size) in enumerate((("Arial", 10), ("Times New Roman", 12), ("Calibri", 11))):
        run = doc.add_paragraph().add_run(f"Body text in its own font number {i}")
        run.font.name, run.font.size = font, Pt(size)
    doc.save(tmp_path / "mixed.docx")
    with pytest.raises(recipes.RecipeError, match="no one body font"):
        recipes.build("font_template", {}, recipes.Context(docs(folder), tmp_path / "mixed.docx"))


def test_files_the_form_names_must_be_attached(folder, tmp_path):
    files = docs(folder)
    picture = tmp_path / "logo.png"
    picture.write_bytes(b"\x89PNG\r\n\x1a\n")
    with pytest.raises(recipes.RecipeError, match="files you attached"):
        recipes.build("swap_logo", {"image": str(tmp_path / "other.png")}, recipes.Context(files, None, [picture]))
    with pytest.raises(recipes.RecipeError, match="must be one of the files you attached"):
        recipes.build("find_replace", {"table": str(tmp_path / "pairs.csv")}, recipes.Context(files, None, []))


# ---- Safe mode in the task ------------------------------------------------------------------------------------------

def test_safe_mode_has_no_conversation_and_the_buttons_are_safe_modes(folder, tmp_path):
    task = DocumentTask(Session(out_root=tmp_path / "out", visual_check=False), render=False, mode="safe")
    task.attach(docs(folder))
    with pytest.raises(ValueError, match="Safe mode has no conversation"):
        task.perform("send", text="Make the fonts Arial")
    assert SAFE_NO_CONVERSATION
    task.set_mode("default")
    with pytest.raises(ValueError, match="Safe mode's"):
        task.perform("recipe", recipe="remove_highlight", inputs={})
    with pytest.raises(ValueError):
        task.set_mode("obliterate")


def test_changed_attachments_void_a_button_preview_and_say_so(folder, tmp_path):
    task = DocumentTask(Session(out_root=tmp_path / "out", visual_check=False), render=False, mode="safe")
    task.attach(docs(folder)[:1])
    view = task.perform("recipe", recipe="remove_highlight", inputs={})
    assert view["stage"] == "preview"
    task.attach(docs(folder)[1:])
    assert task.stage == "empty" and task.decisions == [] and task.session.spec is None
    assert "Press the button again" in task.messages[-1]["text"]
    assert task.view()["action"] == ""                       # nothing to continue without the conversation


def test_what_a_button_left_untouched_can_be_handed_to_default_mode(tmp_path):
    good = tmp_path / "21 13 13.docx"
    good.write_bytes(build("21 13 13", "WET-PIPE SPRINKLER SYSTEMS"))
    empty = tmp_path / "empty.docx"
    empty.write_bytes(build_dirty("empty_body"))
    task = DocumentTask(Session(out_root=tmp_path / "out", visual_check=False), render=False, mode="safe")
    task.attach([good, empty])
    view = task.perform("recipe", recipe="title_property", inputs={"pattern": "SECTION {number} - {title}"})
    with pytest.raises(ValueError, match="before handing"):
        task.hand_off([str(empty)])                          # a preview waits: create the copies first
    task.perform("primary", token=view["token"])
    view = task.view()
    assert [e["name"] for e in view["left_untouched"]] == ["empty.docx"]
    assert view["todo"] == []                                # a button's refusals stay in scope until handed over
    with pytest.raises(ValueError, match="not the ones"):
        task.hand_off([str(good)])
    task.hand_off([str(empty.resolve())])
    view = task.view()
    assert task.mode == "default" and view["mode"] == "default" and view["left_untouched"] == []
    assert [t["name"] for t in view["todo"]] == ["empty.docx"]
    entry = task.todo[str(empty.resolve())]
    assert entry["decisions"] == ["Set the document title property to “SECTION {number} - {title}”"]
    assert entry["why"] == "refused" and entry["reason"].startswith("The button could not change it: ")
    assert view["todo"][0]["reason"] == entry["reason"]      # the to-do card says why, as it does for a conversation's
    assert WORK_TODO in task.messages[-1]["text"]


def test_setting_every_document_aside_in_safe_mode_creates_nothing_and_starts_no_conversation(folder, tmp_path):
    task = DocumentTask(Session(out_root=tmp_path / "out", visual_check=False), render=False, mode="safe")
    files = docs(folder)
    task.attach(files)
    view = task.perform("recipe", recipe="remove_highlight", inputs={})
    task.perform("primary", token=view["token"], hold=[str(f) for f in files])
    view = task.view()
    assert task.mode == "safe" and task.stage == "empty" and task.decisions == [] and task.focus is None
    assert sorted(t["name"] for t in view["todo"]) == sorted(f.name for f in files)
    assert not task.results and "no copy was created" in task.messages[-1]["text"]


def test_a_removed_document_leaves_the_untouched_list_and_cannot_be_handed_over(tmp_path):
    good = tmp_path / "21 13 13.docx"
    good.write_bytes(build("21 13 13", "WET-PIPE SPRINKLER SYSTEMS"))
    empty = tmp_path / "empty.docx"
    empty.write_bytes(build_dirty("empty_body"))
    task = DocumentTask(Session(out_root=tmp_path / "out", visual_check=False), render=False, mode="safe")
    task.attach([good, empty])
    view = task.perform("recipe", recipe="title_property", inputs={"pattern": "SECTION {number} - {title}"})
    task.perform("primary", token=view["token"])
    assert [e["name"] for e in task.view()["left_untouched"]] == ["empty.docx"]
    stale = task.view()["left_untouched"][0]["path"]
    task.remove(empty)
    assert task.view()["left_untouched"] == []
    task.left_untouched = [{"path": stale, "name": "empty.docx", "why": "refused", "reason": "x", "request": "r"}]
    with pytest.raises(ValueError, match="not the ones"):    # a page out of date names a document no longer attached
        task.hand_off([stale])
    assert task.todo == {} and task.mode == "safe"


def test_a_default_plan_approved_in_safe_mode_takes_no_ai_step(folder, tmp_path, monkeypatch):
    task = DocumentTask(Session(out_root=tmp_path / "out", visual_check=False), render=False, mode="default")
    task.attach(docs(folder))
    _built, outcome, errors = built_and_gated("remove_highlight", {}, task.documents())
    assert not errors
    s = task.session
    task.decisions = ["Remove the highlighting"]
    s._set_outcome(spec=outcome, source="conversation")       # a plan the conversation made, previewed in Default
    task._settle(quiet)
    assert task.stage == "preview" and not s.model_free
    task.set_mode("safe")                                    # the preview stays; Safe mode makes no AI call

    def never(*a, **k):
        raise AssertionError("an AI step in Safe mode")
    monkeypatch.setattr(s, "resolve_remaining", never)
    monkeypatch.setattr(s, "resolve_next_layout", never)
    real_run = s.run

    def run_one_failing(*a, **k):
        real_run(*a, **k)
        s.last_batch.files[0].exception = {"code": "planted", "reason": "a planted failure"}   # Default recovers it
    monkeypatch.setattr(s, "run", run_one_failing)
    task.perform("primary", token=task.view()["token"])
    view = task.view()
    assert task.stage == "done" and task.todo == {}
    assert [(e["name"], e["reason"]) for e in view["left_untouched"]] == [(Path(s.last_batch.files[0].source).name,
                                                                           "a planted failure")]


def test_a_button_no_document_can_take_previews_nothing_to_create(tmp_path):
    empty = tmp_path / "empty.docx"
    empty.write_bytes(build_dirty("empty_body"))
    task = DocumentTask(Session(out_root=tmp_path / "out", visual_check=False), render=False, mode="safe")
    task.attach([empty])
    view = task.perform("recipe", recipe="name_files", inputs={})
    assert view["stage"] == "preview" and view["action"] == "" and view["set_aside"]
    assert "None of the documents can take this change" in task.messages[-1]["text"]


def test_a_dirty_rich_file_refused_by_the_template_is_left_untouched_not_guessed(tmp_path):
    rich = tmp_path / "rich.docx"
    rich.write_bytes(build_rich())
    plain = tmp_path / "plain.docx"
    plain.write_bytes(build("21 13 16", "DRY-PIPE SPRINKLER SYSTEMS"))
    template = tmp_path / "tpl.docx"
    template.write_bytes(build_template())
    task = DocumentTask(Session(out_root=tmp_path / "out", visual_check=False), render=False, mode="safe")
    task.attach([rich, plain])
    task.attach([template], template=True)
    view = task.perform("recipe", recipe="headers_footers_template", inputs={"slots": {"ISSUED FOR PERMIT": "BID"}})
    reasons = {i["name"]: i["reason"] for i in view["set_aside"]}
    assert "rich.docx" in reasons and "odd" in reasons["rich.docx"].lower()   # the even/odd pages setting differs


# ---- the mode is remembered; Obliterate never is -------------------------------------------------------------------

def test_the_window_opens_in_the_mode_last_used_and_safe_mode_first(tmp_path):
    assert Settings().mode == "safe" and MODES == ("safe", "default")
    path = tmp_path / "settings.json"
    save_settings(Settings(mode="default"), path)
    assert load_settings(path).mode == "default"
    path.write_text('{"mode": "obliterate"}', encoding="utf-8")
    assert load_settings(path).mode == "safe"


def test_the_mock_servers_buttons_are_the_apps():
    """frontend/mock/recipes.json is what the page is developed and tested against; it is the catalog as the app
    serves it (regenerate: json.dumps({"recipes": recipes.catalog(), "groups": list(recipes.GROUPS)}, indent=1))."""
    import json
    mock = Path(__file__).resolve().parent.parent / "frontend" / "mock" / "recipes.json"
    assert json.loads(mock.read_text(encoding="utf-8")) == {"recipes": recipes.catalog(), "groups": list(recipes.GROUPS)}


def test_a_sample_a_later_step_refused_is_left_untouched_beside_a_buttons_copies(tmp_path, monkeypatch):
    # A button's spec passes the gate that turns overlapping operations into ordered steps, so a later step can refuse a
    # sample. Create does not run that document (Session._without_refused)
    # and Left untouched names it with the step's reason, never a document dropped without a word.
    a, b = tmp_path / "21 13 13.docx", tmp_path / "21 13 16.docx"
    a.write_bytes(build("21 13 13", "WET-PIPE SPRINKLER SYSTEMS"))
    b.write_bytes(build("21 13 16", "DRY-PIPE SPRINKLER SYSTEMS"))
    before = b.read_bytes()
    real = preview.make_preview

    def later_step_refuses(*args, **kwargs):
        pv = real(*args, **kwargs)
        refused = next(sv for sv in pv.samples if Path(sv.source).name == b.name)
        refused.passed = None
        refused.exception = {"code": "numbering-unsupported", "kind": "refusal",
                             "reason": "stage 2 (fonts): a list defined through a numbering style"}
        return pv
    monkeypatch.setattr(preview, "make_preview", later_step_refuses)
    task = DocumentTask(Session(out_root=tmp_path / "out", visual_check=False), render=False, mode="safe")
    task.attach([a, b])
    view = task.perform("recipe", recipe="title_property", inputs={"pattern": "SECTION {number} - {title}"})
    assert view["stage"] == "preview" and [e["name"] for e in view["set_aside"]] == [b.name]
    task.perform("primary", token=view["token"])
    view = task.view()
    assert list(task.results) == [str(a)] and b.read_bytes() == before
    assert [(e["name"], e["reason"]) for e in view["left_untouched"]] == [
        (b.name, "a list defined through a numbering style")]
