"""The render cache (render/cache.py): a PDF is keyed by the .docx's bytes and the renderer's identity and never
served for other bytes or another renderer; a broken entry is a miss that is removed; the cache is bounded, the
least recently used going first; SPEC_SURGEON_RENDER_CACHE=0 turns it off; it lives beside the session folders; and
render_pdfs serves hits and renders only misses — so the run does not render what the preview rendered."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from make_fixtures import build
from spec_surgeon import render as render_mod
from spec_surgeon.batch import run_batch
from spec_surgeon.preview import make_preview
from spec_surgeon.render import cache as cache_mod
from spec_surgeon.render import libreoffice
from spec_surgeon.render.cache import RenderCache
from spec_surgeon.settings import default_run_root
from test_vision import make_pdf

R = "libreoffice 24.2.7.2"
SPECS = Path(__file__).resolve().parent.parent / "examples" / "specs"
# a formatting spec applied plain (no revision stamps, so no clock in the output): the same source gives the same
# output bytes in the preview and in the run
FONT_SPEC = json.loads((SPECS / "change-all-font-arial.json").read_text(encoding="utf-8"))


def _docx(path: Path, number="21 13 13", title="WET-PIPE") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(build(number, title))
    return path


def _fake_backend(monkeypatch, converted, version="24.2.7.2"):
    """LibreOffice as a fake: every conversion is recorded by file name and comes out as a real one-page PDF."""
    def convert(soffice, files, out_dir, profile_dir, timeout):
        for f in files:
            converted.append(Path(f).name)
            make_pdf(Path(out_dir) / (Path(f).stem + ".pdf"), words=Path(f).stem)
        return subprocess.CompletedProcess(["soffice"], 0, b"", b"")
    monkeypatch.setattr(libreoffice, "find_soffice", lambda: "soffice")
    monkeypatch.setattr(libreoffice, "version", lambda soffice=None: version)
    monkeypatch.setattr(libreoffice, "_convert_batch", convert)


# ---- the cache itself ------------------------------------------------------------------------------------------------

def test_a_hit_is_served_under_the_requested_name_and_only_for_the_same_bytes_and_renderer(tmp_path):
    cache = RenderCache(tmp_path / "cache")
    a = _docx(tmp_path / "A.docx")
    pdf = make_pdf(tmp_path / "A.pdf", words="alpha")
    assert cache.get(a, tmp_path / "out" / "A.pdf", R) is None          # nothing yet
    assert cache.put(a, pdf, R)
    same_bytes = tmp_path / "elsewhere" / "02 Copy of A.docx"              # the preview's staged copy, the run's renamed one
    same_bytes.parent.mkdir()
    same_bytes.write_bytes(a.read_bytes())
    served = cache.get(same_bytes, tmp_path / "out" / "02 Copy of A.pdf", R)
    assert served == tmp_path / "out" / "02 Copy of A.pdf" and served.read_bytes() == pdf.read_bytes()
    other = _docx(tmp_path / "B.docx", "21 13 16", "DRY-PIPE")              # different bytes: never A's pages
    assert cache.get(other, tmp_path / "out" / "B.pdf", R) is None and not (tmp_path / "out" / "B.pdf").exists()
    assert cache.get(a, tmp_path / "out" / "A2.pdf", "libreoffice 25.8.0") is None   # another renderer: a miss
    assert cache.get(a, tmp_path / "out" / "A3.pdf", "word 16.0.17328") is None
    # the entry is content-addressed under the renderer, with a sidecar saying what it is
    entries = cache.entries()
    assert len(entries) == 1 and entries[0][0].parent.name == "libreoffice_24.2.7.2"
    facts = json.loads(entries[0][0].with_suffix(".json").read_text(encoding="utf-8"))
    assert facts["docx_sha256"] == cache_mod.sha256_of(a) and facts["renderer"] == R
    assert facts["pdf_bytes"] == pdf.stat().st_size and facts["pdf_sha256"] == cache_mod.sha256_of(pdf)


def test_a_file_whose_bytes_changed_under_the_renderer_is_not_cached_under_the_old_key(tmp_path):
    cache = RenderCache(tmp_path / "cache")
    a = _docx(tmp_path / "A.docx")
    assert cache.get(a, tmp_path / "A.pdf", R) is None                      # the lookup before the render
    a.write_bytes(build("21 13 13", "WET-PIPE (REVISED)"))                  # ... and the file changes meanwhile
    assert cache.put(a, make_pdf(tmp_path / "A.pdf"), R) is False and cache.entries() == []
    assert cache.get(a, tmp_path / "A2.pdf", R) is None
    missing = tmp_path / "nowhere.docx"                                     # unreadable: the renderer's to report
    assert cache.get(missing, tmp_path / "n.pdf", R) is None and cache.put(missing, tmp_path / "A.pdf", R) is False
    assert cache.put(a, tmp_path / "A.docx", R) is False                    # not a PDF: never stored


def test_a_broken_entry_is_a_miss_and_is_removed_so_the_next_render_replaces_it(tmp_path):
    cache = RenderCache(tmp_path / "cache")
    a = _docx(tmp_path / "A.docx")
    pdf = make_pdf(tmp_path / "A.pdf")

    def stored():
        assert cache.put(a, pdf, R)
        (entry, _size, _mtime), = cache.entries()
        return entry, entry.with_suffix(".json")
    entry, meta = stored()
    entry.write_bytes(entry.read_bytes()[:100])                              # truncated
    assert cache.get(a, tmp_path / "o1.pdf", R) is None and not entry.exists() and not meta.exists()
    entry, meta = stored()
    meta.write_text("{not json", encoding="utf-8")                           # sidecar unreadable
    assert cache.get(a, tmp_path / "o2.pdf", R) is None and not entry.exists()
    entry, meta = stored()
    entry.write_bytes(b"%PDF-" + os.urandom(entry.stat().st_size - 5))       # same size, other bytes
    assert cache.get(a, tmp_path / "o3.pdf", R) is None and not entry.exists()
    entry, meta = stored()
    meta.unlink()                                                             # no sidecar at all
    assert cache.get(a, tmp_path / "o4.pdf", R) is None and not entry.exists()
    entry, meta = stored()
    assert cache.get(a, tmp_path / "o5.pdf", R) == tmp_path / "o5.pdf"       # a sound entry is served


def test_the_cache_is_bounded_and_the_least_recently_used_entry_goes_first(tmp_path):
    cache = RenderCache(tmp_path / "cache", limit_bytes=10)
    docs = [_docx(tmp_path / f"{n}.docx", "21 13 13", f"TITLE {n}") for n in range(3)]
    pdfs = [make_pdf(tmp_path / f"{n}.pdf", words=f"doc{n}") for n in range(3)]
    for n, (d, p) in enumerate(zip(docs, pdfs, strict=True)):
        assert cache.put(d, p, R)
        entry = cache._entry(cache_mod.sha256_of(d), R)[0]
        os.utime(entry, (1000 + n, 1000 + n))                                # stored oldest first
    size = cache.size()
    assert size > 10 and cache.trim(limit_bytes=size) == 0                  # within the bound: nothing goes
    assert cache.get(docs[0], tmp_path / "hit0.pdf", R) is not None          # the oldest, used just now
    assert cache.trim(limit_bytes=size - 1) == 1                             # one over: one entry goes ...
    assert cache.get(docs[0], tmp_path / "hit0b.pdf", R) is not None         # ... not the one just used
    assert cache.get(docs[1], tmp_path / "hit1.pdf", R) is None              # but the least recently used
    assert cache.trim() == 2 and cache.entries() == []                       # the 10-byte bound evicts all


def test_the_environment_switch_turns_the_cache_off(tmp_path, monkeypatch):
    assert cache_mod.enabled() and RenderCache.for_out_dir(tmp_path / "out") is not None
    monkeypatch.setenv(cache_mod.ENV_SWITCH, "0")
    assert not cache_mod.enabled() and RenderCache.for_out_dir(tmp_path / "out") is None
    converted = []
    _fake_backend(monkeypatch, converted)
    a = _docx(tmp_path / "A.docx")
    cache = RenderCache(tmp_path / "cache")                                 # given explicitly: still not consulted
    for n in range(2):
        assert set(render_mod.render_pdfs([a], tmp_path / f"out{n}", "libreoffice", 1, cache=cache)) == {a}
    assert converted == ["A.docx", "A.docx"] and cache.entries() == []
    for value in ("false", "No", "OFF", " 0 "):
        monkeypatch.setenv(cache_mod.ENV_SWITCH, value)
        assert not cache_mod.enabled()
    monkeypatch.setenv(cache_mod.ENV_SWITCH, "1")
    assert cache_mod.enabled()


def test_the_cache_lives_beside_the_session_folders(tmp_path):
    runs = default_run_root()                                                # the window's default out_root
    assert cache_mod.cache_root_for(runs / "20261019-235959" / "preview") == runs / ".render-cache"
    assert cache_mod.cache_root_for(runs / "20261019-235959-2" / "run") == runs / ".render-cache"
    custom = tmp_path / "My Specs" / "runs"                                  # a root the user chose: beside its sessions
    assert cache_mod.cache_root_for(custom / "20261019-235959" / "preview") == custom / ".render-cache"
    assert cache_mod.cache_root_for(custom / "20261019-235959-123456-recovery" / "run") == custom / ".render-cache"
    out = tmp_path / "cli" / "out"                                           # the command line's --out: beside it
    assert cache_mod.cache_root_for(out) == tmp_path / "cli" / ".render-cache"
    assert RenderCache.for_out_dir(out).root == tmp_path / "cli" / ".render-cache"


# ---- render_pdfs over the cache --------------------------------------------------------------------------------------

def test_render_pdfs_serves_hits_and_renders_only_misses(tmp_path, monkeypatch):
    converted, details = [], []
    _fake_backend(monkeypatch, converted)
    a, b = _docx(tmp_path / "A.docx"), _docx(tmp_path / "B.docx", "21 13 16", "DRY-PIPE")
    cache = RenderCache(tmp_path / "cache")
    first = render_mod.render_pdfs([a, b], tmp_path / "out1", "libreoffice", 1, detail=details.append, cache=cache)
    assert set(first) == {a, b} and converted == ["A.docx", "B.docx"]
    assert any("kept 2 of 2 fresh render(s)" in d for d in details)
    second = render_mod.render_pdfs([a, b], tmp_path / "out2", "libreoffice", 1, detail=details.append, cache=cache)
    assert second == {a: tmp_path / "out2" / "A.pdf", b: tmp_path / "out2" / "B.pdf"}
    assert converted == ["A.docx", "B.docx"]                                 # nothing rendered again
    assert second[a].read_bytes() == first[a].read_bytes()
    assert any("2 of 2 PDF(s) came from earlier renders by libreoffice 24.2.7.2" in d for d in details)
    b.write_bytes(build("21 13 16", "DRY-PIPE (REVISED)"))                   # B changed: B renders, A is served
    third = render_mod.render_pdfs([a, b], tmp_path / "out3", "libreoffice", 1, cache=cache)
    assert set(third) == {a, b} and converted == ["A.docx", "B.docx", "B.docx"]
    monkeypatch.setattr(libreoffice, "version", lambda soffice=None: "25.8.0")   # the renderer was upgraded
    assert set(render_mod.render_pdfs([a, b], tmp_path / "out4", "libreoffice", 1, cache=cache)) == {a, b}
    assert converted == ["A.docx", "B.docx", "B.docx", "A.docx", "B.docx"]
    assert len(cache.entries()) == 5                                         # 24.2.7.2: A, B, B'; 25.8.0: A, B'


def test_a_renderer_that_fails_still_leaves_the_served_hits_and_an_unreadable_file_to_the_renderer(tmp_path, monkeypatch):
    converted, said = [], []
    _fake_backend(monkeypatch, converted)
    a = _docx(tmp_path / "A.docx")
    cache = RenderCache(tmp_path / "cache")
    assert set(render_mod.render_pdfs([a], tmp_path / "out1", "libreoffice", 1, cache=cache)) == {a}
    backend = libreoffice.render_pdfs

    def dies(*args, **kwargs):
        raise RuntimeError("soffice crashed")
    monkeypatch.setattr(libreoffice, "render_pdfs", dies)
    b = _docx(tmp_path / "B.docx", "21 13 16", "DRY-PIPE")
    got = render_mod.render_pdfs([a, b], tmp_path / "out2", "libreoffice", 1, log=said.append, cache=cache)
    assert got == {a: tmp_path / "out2" / "A.pdf"} and said == ["rendering with libreoffice failed: RuntimeError: soffice crashed"]
    monkeypatch.setattr(libreoffice, "render_pdfs", backend)
    missing = tmp_path / "nowhere.docx"
    got = render_mod.render_pdfs([a, missing], tmp_path / "out3", "libreoffice", 1, log=said.append, cache=cache)
    assert set(got) == {a, missing} and converted == ["A.docx", "nowhere.docx"]   # the renderer saw the missing file
    assert len(cache.entries()) == 1                                        # an unreadable file is never cached


def test_the_run_does_not_render_what_the_preview_rendered(tmp_path, monkeypatch):
    """A session folder as the window lays it out: <root>/<stamp>/preview then <root>/<stamp>/run. The run's
    source renders come from the preview's; a formatting op applied plain gives the same output bytes, so the
    run's output renders do too."""
    converted = []
    _fake_backend(monkeypatch, converted)
    folder = tmp_path / "specs"
    files = [_docx(folder / "A.docx"), _docx(folder / "B.docx", "21 13 16", "DRY-PIPE")]
    session = tmp_path / "runs" / "20261020-120000"
    pv = make_preview(FONT_SPEC, [str(f) for f in files], session / "preview", renderer="libreoffice", do_render=True)
    assert pv.renderer == "libreoffice" and all(s.source_pdf and s.output_pdf for s in pv.samples)
    assert sorted(converted) == ["A.docx", "A.docx", "B.docx", "B.docx"]       # two sources, two outputs
    cache = RenderCache.for_out_dir(session / "preview")
    assert cache.root == tmp_path / "runs" / ".render-cache" and len(cache.entries()) == 4
    before = len(converted)
    batch = run_batch([str(folder)], "edit-spec", {}, session / "run", workers=1, renderer="libreoffice",
                      do_render=True, spec=FONT_SPEC)
    assert batch.counts()["PASS"] == 2 and batch.renderer == "libreoffice"
    assert converted[before:] == []                                          # sources and outputs: all served
    log = (session / "run" / "logs" / "run.log").read_text(encoding="utf-8")
    assert "came from earlier renders" in log and str(cache.root) in log
    for fr in batch.files:
        v6 = fr.record.get("V6")
        assert v6.status == "PASS" and Path(v6.data["source_pdf"]).is_file() and Path(v6.data["output_pdf"]).is_file()
