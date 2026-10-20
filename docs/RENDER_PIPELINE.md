# Render pipeline — one renderer process per batch, cached renders

What the window waits on most is page rendering. Before this change every file was rendered by a fresh Word
instance (a spawned Python process per file: DispatchEx, open, export, quit) and the run rendered again what the
preview had rendered, source and output both.

Rules:

1. **One Word instance per batch.** `render.word_worker.run_word_batch(files, out_dir)` spawns ONE worker process
   that starts ONE private Word instance (DispatchEx, never Dispatch), renders every file (open read-only → export
   PDF → close), and quits. Per-file failures are reported per file; the batch goes on. The deadline is per file,
   enforced by the parent (a stuck file ends the worker; the parent then renders the remaining files in a new
   worker). The private-instance ownership proof and cleanup are unchanged.
2. **LibreOffice keeps its profile.** The per-worker user profile lives under the app's config folder
   (`render-profiles/<n>`) and is reused across calls, not created and deleted every time.
3. **A render cache.** `render.cache.RenderCache(root)` keys a PDF by the sha256 of the .docx bytes plus the
   renderer identity (backend and version). `render_pdfs(...)` consults it first and renders only misses; sources
   rendered for the preview are not rendered again for the run, and an unchanged source attached again tomorrow
   is not rendered again either. The cache lives under the output root (`<out_root>/.render-cache`), is bounded
   (oldest entries evicted past 2 GB), and is never read for a .docx whose bytes differ.
4. **Both sides are still compared.** V6/V7 and the before/after pictures keep reading a source PDF and an output
   PDF; where they come from is the cache's business.

## What was built (2026-10-20, branch `wip/render-pipeline`)

### The cache — `src/spec_surgeon/render/cache.py`

- `RenderCache(root, limit_bytes=2 GB)`; `RenderCache.for_out_dir(out_dir)` is what the preview and the batch
  use (None when the cache is off). `get(docx, pdf, renderer)` writes the cached render to `pdf` (the name the
  caller asked for, in the caller's folder) and returns it, or None; `put(docx, pdf, renderer)` keeps a fresh
  render; `trim()` enforces the bound, least recently used first (a hit refreshes the entry's mtime).
- **Key:** `<root>/<renderer>/<sha256 of the .docx bytes>.pdf`, where *renderer* is
  `render.renderer_identity(backend)` — `libreoffice <version from version.ini/versionrc>` (falling back to the
  executable's size and mtime) or `word <VersionToReport from the Click-to-Run registry key | WINWORD.EXE's file
  version | Word.Application.<major>>`, read from the registry so a hit costs no Word start; `unknown` when no
  version can be read. A sidecar `<digest>.json` records the .docx digest, the renderer, and the PDF's size and
  digest; an entry whose sidecar does not describe it (truncated, tampered, orphaned) is a miss and is removed.
- **Never for different bytes:** `get` hashes the file as it is now, and `put` hashes it again after the render
  and refuses to store when the bytes moved in between.
- **Where:** `cache_root_for(out_dir)` — `<default run root>/.render-cache` when the preview or run folder is
  under `settings.default_run_root()`; `<out_root>/.render-cache` when the folder's parent is a session folder
  (`gui.Session` stamps them `YYYYMMDD-HHMMSS[-n]`) under a root the user chose; otherwise (the command line's
  `--out`, a test folder) a `.render-cache` beside the output folder itself, never above a folder the caller named.
- **Off switch:** `SPEC_SURGEON_RENDER_CACHE=0` (also `false`, `no`, `off`).
- `render.render_pdfs(files, out_dir, backend, workers, log, detail, cache=None)` serves hits, renders only the
  misses (a renderer failure still returns the hits), stores the fresh renders and trims. The preview
  (`preview.render_samples`) and the run (`batch._render_and_compare`) pass `RenderCache.for_out_dir(out_dir)`
  to every `render_pdfs` call — sources, outputs, headers/footers-only copies and removal references alike —
  and say in their logs where the cache is and how many PDFs it served.

### Word — `src/spec_surgeon/render/word_worker.py`, `word_com.py`

- `run_word_batch(files, out_dir, timeout_per_file)` → `WordBatchResult` (`results: {path: pdf | WordWorkerError}`,
  `seconds`, `version`, `workers`, `start_error`). One spawned process runs `_batch_worker`: one `WordSession`
  (DispatchEx), `file_started`/`file_done` per file, `finished` after Quit. The parent publishes each PDF
  atomically when its `file_done` arrives, and holds the deadline: startup, each file (reset at its `file_started`)
  and the quit each get `timeout_per_file` (`SPEC_SURGEON_WORD_TIMEOUT_SECONDS`, default 120 s). A stuck file is
  recorded `word-timeout`, the worker is terminated and the owned private Word with it (`_close_private_handle`,
  unchanged), and the remaining files go on in a fresh worker; a worker that dies mid-file (`word-worker-died`) and a
  COM error whose HRESULT says the instance is gone (`INSTANCE_LOST_HRESULTS`) are handled the same way. Only a Word
  that never starts, with nothing rendered at all, is `start_error`; `word_com.render_pdfs` raises it so the
  renderer-selection wrapper reports the backend as unavailable once, as before.
- `word_com.render_pdfs` now makes one batch call for all its files; `run_word` and `accept_reject_text` are as
  they were (one action, one worker, one deadline).
- `word_com.installed_version()` reads Word's version from the registry for the cache key.

### LibreOffice — `src/spec_surgeon/render/libreoffice.py`

- Profiles live under `settings.config_dir()/render-profiles/<n>`; chunk *n* of a call runs on profile *n*, and so
  do its one-by-one retries. A `ProfileLease` holds a lock file (`<n>.lock`, released by the OS when the process
  ends) for the life of a soffice run, so a second process rendering at the same time takes the next free index
  instead of sharing — two soffice on one profile hand their work to each other and lose it.
- A profile soffice cannot start from — it exits with an error and no PDF has ever come out of that profile in
  this call — is wiped and recreated once (`render.profile_reset`), and the chunk is tried again on the fresh one
  before the ordinary one-file-at-a-time retry. A timeout, or an error after a PDF came out, never wipes one.
- `_convert_batch` and the N-parallel-chunks design are unchanged.

### Measured (Linux, LibreOffice 24.2.7.2, `make_fixtures.py --count 4` = 6 files, `change-all-font-arial`)

`preview.make_preview(do_render=True)` over all 6 samples, one process, idle machine (run 1 also carries the
one-time imports; the same four runs beside a running test suite gave 6.1 / 1.8 / 4.7 / 4.8 s):

| run | cache | profiles | wall | renders |
| --- | --- | --- | --- | --- |
| 1 | cold | cold | 5.9 s | 12 (6 sources + 6 outputs) |
| 2 | warm | warm | 1.9 s | 0 — "6 of 6 PDF(s) came from earlier renders" for sources and for outputs |
| 3 | off | warm | 4.5 s | 12 |
| 4 | off | cold | 4.8 s | 12 |

The outputs are served too because a formatting spec applied plain gives byte-identical outputs on the same source
(repack by substitution, no clock); a tracked-changes spec stamps the revision time, so its outputs are hits only
within the same second. Profile reuse saved almost nothing on this Linux build (LibreOffice creates a profile in
well under a second here); its value is on Windows, where profile creation and first-start quirks are slower, and
that remains to be measured on the owner's machine, as does the one-instance Word batch, which cannot run on Linux.

Tests: `tests/test_render_cache.py`, `tests/test_render_word_batch.py` (a fake worker behind a real spawned process
and pipe), `tests/test_render_libreoffice_profiles.py` (the last test runs the real soffice when installed).
