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

The default run root is `%LOCALAPPDATA%\Spec Surgeon\runs` on Windows. A results
folder the user chooses instead is supported; the window warns
(`settings.output_root_warnings`) about common synced folders and roots deep enough to
put generated paths near the Windows path limit.

New conversation directories and content-addressed evidence files use 16-character
disk identifiers. The complete SHA-256 remains in their JSON metadata, references
and identity checks. Short-name collisions fail without replacing evidence. Reads
also accept older files named with all 64 digest characters; conversation folders
from older versions are copied to their shorter location before new checkpoints.
The frozen executable declares `longPathAware` in its application manifest.
User-selected roots and source filenames can still exceed platform limits; the
window shows its output warning before a run starts.

Active conversation checkpoints and sealed epochs reference a shared prompt/tool
catalog by its complete digest. Signed provider messages remain unchanged. Catalog
and archive reads verify their complete identities, and old inline catalogs remain
readable. Resuming an epoch compares its exact original schema and provider-message
prefix before writing an updated checkpoint.

## Desktop-safe workers

`run_batch`'s `worker_mode` (`batch.worker_plan`) defaults to `auto`
(`batch.DEFAULT_WORKER_MODE`), what the window's runs use: a batch of three or more
files (`PROCESS_MIN_FILES`) is shared over spawned worker processes, one per core
up to eight (`MAX_PROCESS_WORKERS`); fewer files run inline, where starting a worker
costs more than it saves. Each file's acquire, plan, apply and verify run whole in
one worker, exactly as inline, so a file's result does not depend on which worker had
it (`tests/test_runtime_reliability.py` compares every output byte and check with an
inline run). `thread` runs one worker unless `workers=N` asks for more: the work holds
the GIL, and on eight 53-page sections eight threads took 19% longer than one and 2.7
times the memory. `serial` runs inline; `process` asks for processes outright. The
test suite runs `thread` (`tests/conftest.py`): a test's patches never reach a
spawned worker. Measured on eight 50-page sections on four cores (rendering off): a
run 4.6 s inline, 1.6 s in `auto`.

`batch.map_files` gives the same sharing to work that is not a run: the preview plans
a batch of twelve or more files (`planner.PLAN_PROCESS_MIN_FILES`; planning alone is a
fraction of a file's run) and checks three or more samples (`preview._preview_sample`,
each sample's events in its own journal) side by side, with the same results as
inline; a preview of eight samples went from 4.8 s to 2.2 s. A call whose worker
stopped abruptly is made again in the app's process.

The packaged app runs processes too. It once swapped processes for threads because
its crash log held "Windows fatal exception: access violation" at every worker
spawn (B3 of the 1.0 live runs); W2 (2026-10-22) showed those are handled
first-chance exceptions inside CreateProcess, every worker running to its end, and
paused the crash log around the Word worker's start. `diagnostics.com_quiet` now
counts the threads inside it (the first pauses the crash log, the last resumes it),
and the pools submit their work inside it, since a spawn-context pool starts its
processes on submit. The frozen Windows launcher calls
`multiprocessing.freeze_support()` before anything else, so a worker never opens a
window or takes the app mutex; the installed-app smoke test runs a real two-worker
batch and a map from the frozen executable. Isolated retries of a file whose worker
died remain part of process mode.

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
