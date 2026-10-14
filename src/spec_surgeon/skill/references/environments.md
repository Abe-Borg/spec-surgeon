# Environments — acquisition, tooling, rendering, and the overwrite guard

The contract and verification suite never change. What changes by environment is how files get in and out, which tools exist, and the exact mechanics of the overwrite guard. Identify the environment at intake and apply its section. A and B are proven; C and D are **[unproven]** adaptations.

## Contents

1. Tooling — what each tool provides and what substitutes for it
2. Environment A — cloud container + device bridge (proven)
3. Environment B — cloud container + device shell (proven)
4. Environment C — local session on the user's Windows machine [unproven]
5. Environment D — attachment-only (no link to the user's computer) [unproven]

## 1. Tooling

The method depends on capabilities, not on particular programs. The table says what each capability is for, what provides it in the cloud container, and what to substitute elsewhere. Resolve the docx skill's SKILL.md location fresh each session; never hardcode its path.

| Capability | Cloud container | Substitute when absent |
|---|---|---|
| XML parse/serialize | `python3` + `lxml` | `pip install lxml` (Windows: `py -3 -m pip install lxml`). No substitute — the method is built on lxml. |
| Run coalescing before text matching | docx skill `scripts/merge_runs.py` — merges adjacent runs with identical `rPr` in `word/document.xml` only, strips rsids/proofErr; never merges across `w:ins`/`w:del` boundaries | A per-paragraph merge written into the transform, scoped to target paragraphs only — which is what the surgical path requires anyway (lessons §4). |
| XSD validation | docx skill `scripts/office/validate.py <file>` — ECMA-376 transitional schemas bundled under `scripts/office/schemas/` | `lxml.etree.XMLSchema` against the same schema set, copied from the docx skill if reachable or downloaded from ECMA-376 Part 4 (transitional; Word writes transitional, not strict). Validate every part the transform touched, plus `[Content_Types].xml` and the rels. |
| Redlining check (text changed outside a tracked revision) | `validate.py out.docx --original src.docx --author "<name>"` — undoes the revisions that are new relative to the original and compares the resulting text to the original, part by part | Reimplement in the verifier: for each text part, remove the contents of every `w:ins` whose `w:id` is not in the source, unwrap every new `w:del` (turning `w:delText` back into `w:t`), extract text, compare to the source part's text; any difference is an untracked edit. Keep it independent of the transform. |
| Render to PDF | docx skill `scripts/office/soffice.py` wrapper (handles the sandbox's socket restriction and user profile) around `soffice` | LibreOffice directly (`soffice --headless --convert-to pdf --outdir <dir> <file>`); on the user's machine, Word itself (section 4). |
| Rasterize pages / page text / word boxes | `pdftoppm -jpeg`, `pdftotext -layout`, `pdftotext -bbox` (poppler) | PyMuPDF (`pip install pymupdf`): `page.get_pixmap(dpi=110).save(...)`, `page.get_text("text")`, `page.get_text("words")` → `(x0, y0, x1, y1, word, ...)` in points from the top-left, which gives the V7 y-deltas. |
| Independent accept/reject oracle | `pandoc in.docx --track-changes=accept|reject -t plain` | pandoc on Windows if installed; else Word (section 4). The XML simulation alone is not independent of your own reading of the grammar — say so in the report if it is the only oracle. |
| Accept-all copies for inspection | docx skill `scripts/accept_changes.py` (LibreOffice macro; NOT an oracle for nested revisions) | The XML simulation; Word on a copy (section 4). |
| Comments | docx skill `scripts/comment.py` | Hand-build per `element-types.md` → Comments, every part. |

## 2. Environment A — cloud container + device bridge (proven)

Work happens in the cloud container; source files live on the user's machine in the session's connected project folder.

* Find the target subfolder with `device_list_dir` and copy folder/file names exactly as listed — names have contained en-dashes (not hyphens) and doubled spaces; never retype them from memory.
* Acquire: `device_list_dir` → `device_stage_files` (record each file's `mtimeMs`), copy staged files out of the read-only uploads dir.
* `~` in bash = /root in the container; Write/Read tool paths may resolve elsewhere (e.g., /home/claude). Keep one working dir and use absolute paths.
* Deliver: `SendUserFile`, then `device_commit_files` back to the same path with `expectedMtimeMs` from staging — after confirming the file is closed in Word.
* Overwrite guard mechanics: before committing, `device_list_dir` again. If mtime/size moved since you staged: re-stage, unpack, diff against the base you built on, rebase, re-verify, then commit with the fresh mtime. Never commit over a drifted file, never use `force`. Lock-file check: `ls ~$*` in the folder via `device_bash` when available (Environment B); without a shell, `device_list_dir` the folder and look for a `~$`-prefixed entry.
* New-file deliveries (copies into a new folder) still get the guard's first half: re-list and confirm the SOURCES haven't drifted since staging, because the outputs derive from them; only the `expectedMtimeMs` step drops away.
* OneDrive keeps version history on these files — the last-resort rollback.

## 3. Environment B — cloud container + device shell (proven)

When `device_bash` is available, discovery runs in place on the user's machine (python3 + lxml are there; soffice and validate.py are not): dump every paragraph's text with `{+ins+}`/`[-del-]` markers, grep the sibling documents, `stat` mtimes, check for Word lock files — no staging. Stage only for the transform → validate → render cycle, which needs the container's tooling. Delivery and the overwrite guard are as in Environment A; the lock-file check is `ls ~$*` in the document's folder, empty = closed.

## 4. Environment C — local session on the user's Windows machine [unproven]

A Claude Code (or similar) session running directly on the user's computer. Files are on disk; there is no staging and no bridge. Everything else in the method applies unchanged.

**Working area.** Use a scratch directory outside any synced folder (`%LOCALAPPDATA%\Temp\docx-job-<date>` or the session's scratchpad) — never inside the project's OneDrive/SharePoint folder, which would sync scratch files and unpacked packages to the client-facing tree. Scripts live in the scratch dir, not the project folder, and are never named after stdlib modules.

**Acquire.** Record `os.stat(path).st_size` and `st_mtime_ns` plus md5 at the start; copy the file into the scratch dir and work on the copy. Paths with en-dashes and doubled spaces: take them from a directory listing (`os.listdir`), never from memory.

**Tooling.** Check what exists before planning the verification: `py -3 -c "import lxml"`, `where soffice pandoc pdftotext`, `Test-Path "C:\Program Files\LibreOffice\program\soffice.exe"`. The docx skill's scripts may be installed under the user's skills directory; if so, use them exactly as in the container (their `validate.py` needs `lxml` and `defusedxml`). Otherwise use the substitutes in section 1.

**Word as the renderer and oracle.** Word is on this machine, and Word's rendering is the truth the LibreOffice render only approximates. Through COM (`pip install pywin32`):

* Always `win32com.client.DispatchEx("Word.Application")` — a NEW instance — never `Dispatch`, which attaches to the user's running Word and makes `Quit()` close their session. Set `Visible = False`, work only on COPIES in the scratch dir, `doc.Close(SaveChanges=False)`, then `app.Quit()` on the instance you created.
* Render: `doc.ExportAsFixedFormat(out_pdf, 17)` (17 = PDF). This replaces soffice for V6/V7 and removes the LO caveats (page counts, nested-revision display, pStyle-linked numbering).
* Oracle: on a copy, `doc.Revisions.AcceptAll()` then read `doc.Content.Text`; on another copy `doc.Revisions.RejectAll()`; compare to the XML simulation's accept/reject strings. This is the oracle that agrees with Word by construction, and it handles nested revisions.
* Word's `Content.Text` normalizes some characters (cell marks, field results); compare per paragraph and expect to normalize the same way on both sides.

**Overwrite guard mechanics.** Immediately before writing back: re-`stat` the source and compare size and `st_mtime_ns` to the acquisition record; glob the folder for `~$*` (Word's owner lock file — it is hidden, so use `os.listdir`/`Get-ChildItem -Force`; Word builds its name from the document name with the first one or two characters replaced by `~$` for names longer than six characters, so match the glob plus the tail of the name, not an exact string). Drifted → rebase and re-verify. Lock present → stop and ask the user to close the document. Write via a temp file in the same directory and `os.replace`, so the write is atomic and OneDrive sees one new version. OneDrive/SharePoint version history remains the last-resort rollback.

**Delivery.** The file written in place is the delivery; there is nothing to send. Tell the user the path, the new size/md5, and that they should reopen the document fresh.

## 5. Environment D — attachment-only [unproven]

The user attached the .docx to the conversation and the session has no link to their computer.

* Acquire: attachments are under the uploads directory and are read-only — copy out before unpacking. Record size and md5; there is no meaningful mtime.
* Tooling: as in the cloud container (section 1, first column).
* Deliver: `SendUserFile`. The user replaces the file themselves, so the overwrite guard becomes a message: state the size and md5 of the source you worked from, ask them to confirm the file on their machine has not changed since they attached it, and to close it in Word before replacing it. If they have edited it since, ask for the current file and rebase — the transform is scripted and cheap.
