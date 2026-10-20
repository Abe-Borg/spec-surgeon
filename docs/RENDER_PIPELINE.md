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
