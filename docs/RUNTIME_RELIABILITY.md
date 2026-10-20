# Runtime and document-package reliability

These changes apply to Spec Surgeon. No source was copied from the reference repositories.

## Bounded DOCX intake

`DocxPackage` checks the archive byte size and counts central-directory records
before the ZIP library allocates a member object for every entry (including when
the declared count is inaccurate). Declared expansion sizes are checked before
reading member content. It then streams every member occurrence in
bounded chunks, checking actual expansion, declared sizes and ZIP CRC/header checks.
Oversized input produces an `archive-limit` refusal for that file. No member is
extracted to disk, normalized, or removed by these checks. Existing inert members,
unusual names and duplicate inert names remain in source order.

Defaults can be adjusted with positive integer environment variables:

| Variable | Default |
| --- | ---: |
| `SPEC_SURGEON_ARCHIVE_MAX_MEMBERS` | 10,000 |
| `SPEC_SURGEON_ARCHIVE_MAX_MEMBER_BYTES` | 134,217,728 (128 MiB) |
| `SPEC_SURGEON_ARCHIVE_MAX_TOTAL_BYTES` | 536,870,912 (512 MiB) |
| `SPEC_SURGEON_ARCHIVE_MAX_ARCHIVE_BYTES` | 268,435,456 (256 MiB) |

Library callers can pass an explicit `ArchiveLimits` to `DocxPackage` or
`DocxPackage.from_path`. The latter bounds the initial file read too. Invalid limit
settings fail explicitly. The limits do not authorize document edits or change
the relationship-based resolution of Word parts.

## Archive fidelity and independent checking

No-op repacks, including equal-byte substitutions, return the original archive
bytes. A changed document preserves archive/member comments, timestamps,
compression methods, creator/extractor versions, internal/external attributes and
opaque extra metadata. ZIP64 size/offset fields are regenerated. Members with the
same inert filename retain their individual bytes instead of being read repeatedly
through the last occurrence's name.

V1 independently checks the retained members' safe metadata in addition to its
existing content hashes, member order, allowed changes and package integrity.
Its report includes `metadata_changes`. The verifier does not import the repacker
or its metadata filtering helper.

Changed archives still use Python's ZIP writer: compressed streams, ZIP headers,
size/CRC fields and descriptor flags may be regenerated. This is not raw ZIP record
preservation, and it is not proof of human-accepted appearance in Word.

## Local run storage and short evidence paths

The default run root is `%LOCALAPPDATA%\Spec Surgeon\runs` on Windows. The
conversation window always uses it. Explicitly selected roots (`--out`) remain
supported on the command line, which warns about common synced folders and roots
deep enough to put generated paths near the Windows path limit.

New conversation directories and content-addressed evidence files use 16-character
disk identifiers. The complete SHA-256 remains in their JSON metadata, references
and identity checks. Short-name collisions fail without replacing evidence. Reads
also accept older files named with all 64 digest characters; conversation folders
from older versions are copied to their shorter location before new checkpoints.
Both frozen executables declare `longPathAware` in their application manifest.
User-selected roots and source filenames can still exceed platform limits; the
command line prints its output warning before a run starts.

Active conversation checkpoints and sealed epochs reference a shared prompt/tool
catalog by its complete digest. Signed provider messages remain unchanged. Catalog
and archive reads verify their complete identities, and old inline catalogs remain
readable. Resuming an epoch compares its exact original schema and provider-message
prefix before writing an updated checkpoint.

## Desktop-safe workers

Ordinary transforms and verification use threads by default, with single-worker
and `--worker-mode serial` execution inline. `--worker-mode process` explicitly
opts into spawned processes in an unfrozen Python installation. The packaged app
always substitutes threads for a requested process pool, avoiding the worker-spawn
access violations observed in the live Windows runs. Isolated retries remain part
of the explicit process mode. The frozen Windows launcher still calls
`multiprocessing.freeze_support()` before UI startup for other isolated workers.

## Word COM deadlines

Each rendered document runs in a fresh spawned Python process and a private Word
instance created with `DispatchEx`. The deadline covers startup, open, export,
close, and quit; the default is 120 seconds. Set
`SPEC_SURGEON_WORD_TIMEOUT_SECONDS` to a positive finite duration, or pass
`timeout` to the Word renderer/oracle API. A timed-out file is reported and the
next file starts with a fresh worker. Errors retain a code and structured details.

The source is copied into a scratch directory. Word macros are disabled for the
private instance. A PDF is published atomically only after Word has closed and
the worker has successfully exited. Old PDFs are removed before rendering; failed
renders cannot return a stale file as success. `word_com.accept_reject_text` uses
the same process deadline for the Word oracle. `WordSession` is a low-level COM
adapter used inside the worker and by isolated tests; direct use does not provide
a deadline.

Timeout cleanup terminates our Python worker. A Word process may be terminated
only through an OS handle tied to the new `DispatchEx` instance: its PID must not
have existed before dispatch and its creation time must match. The retained handle
prevents PID reuse from selecting another process. There is no process-name kill,
process-tree kill, or attachment to a user's existing Word instance. If ownership
cannot be proved (including a hang before Word exposes its window handle), Word
is left running and cleanup unavailability is recorded. Scratch cleanup is best
effort when such a process still holds a file open.

The worker's COM checkpoints are carried into the run's diagnostics after its
scratch directory is removed. Version lookup is advisory; failures do not prevent
document rendering.

## Evidence and limits

Automated tests cover declared and actual expansion, configuration, exact no-op
bytes, retained metadata, duplicate inert members, planted metadata loss detected
by V1, ordinary/retry spawn execution, and mocked Word success/failure/timeout,
stale outputs, process ownership and PID reuse. Linux tests do not qualify real
Word rendering or a Windows installer. Existing native-Windows installer and
Word acceptance checks remain necessary for those platform claims.
