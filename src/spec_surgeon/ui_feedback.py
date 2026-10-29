"""Plain-language presentation of existing results. Raw exceptions and verification records stay in diagnostics."""
import re
from pathlib import Path


CHECK_MESSAGES = {
    "V1": "A part of the Word file changed outside the approved plan.",
    "V2": "The copy's wording does not match the change predicted by the plan.",
    "V3": "The document's structure changed differently from the approved plan.",
    "V4": "The tracked changes do not match those predicted by the plan.",
    "V5a": "The copy contains a new Word file structure error that was not present in the original.",
    "V5b": "The copy contains an untracked change that the plan did not allow.",
    "V5c": "The tracked changes are not stored in a valid Word structure.",
    "V6": "The rendered pages did not pass their comparison check.",
    "V7": "The page layout does not match the layout predicted by the plan.",
    "V8": "Accepting or rejecting the tracked changes does not give the predicted result.",
    "V9": "The original file could not pass the check required before delivery.",
    "V10": "The copy's formatting, filename or another requested property does not match the approved plan.",
    "FINAL": "The copy does not meet one of the plan's final requirements.",
    "UNREAD": "The copy changes a part of the Word file that no check reads (a chart, an embedded file, stored data, "
              "the theme), so nothing proves what it now holds.",
}


def failed_preview_checks(sample):
    """Read actual failed verdicts, including earlier chain steps; never infer failure from a missing renderer."""
    if sample.passed is True:
        return []
    failed = {key for key, status in (getattr(sample, "checks", None) or {}).items() if status == "FAIL"}
    for stage in getattr(sample, "stages", None) or []:
        failed.update(key for key, status in stage.get("checks", {}).items() if status == "FAIL")
    if not failed:
        return [{"check": "Preview", "reason": "The preview could not complete its checks. The diagnostic record has the details."}]
    return [{"check": key, "reason": CHECK_MESSAGES.get(key, "A verification check did not pass. The diagnostic record has the details.")}
            for key in sorted(failed)]


PREVIEW_FAILED = "Its preview did not pass its checks"


def failed_sample_message(sample):
    """Why a sample set aside by its failed preview (``preview.failed_samples``) gets no copy, in the words of its
    failed checks: the sentence under Left untouched and on the to-do list."""
    reasons = [f"{f['reason'].rstrip('.')} ({f['check']})" if f["check"] != "Preview" else f["reason"].rstrip(".")
               for f in failed_preview_checks(sample)]
    return f"{PREVIEW_FAILED}, so no copy of it is made: " + "; ".join(reasons) + "."


def failed_samples_sentence(samples, labels=None):
    """What the chat says of the samples set aside because they failed (``preview.failed_samples``), in the app's
    words, before the button: each named (``labels``: ``str(source)`` → how the preview names it) with its failed
    checks, and that the others' copies can be created."""
    labels = labels or {}
    named = []
    for sample in samples:
        checks = [f["check"] for f in failed_preview_checks(sample) if f["check"] != "Preview"]
        name = labels.get(str(sample.source)) or Path(sample.source).name
        named.append(f"{name} ({', '.join(checks)})" if checks else name)
    several = len(named) > 1
    return (f"{', '.join(named)} did not pass {'their' if several else 'its'} preview checks, so no copy of "
            f"{'them' if several else 'it'} will be made: {'they wait' if several else 'it waits'} under Left "
            f"untouched, and the other documents' copies can be created.")


# ---- why a document was left untouched -------------------------------------------------------------------------------
# The Left untouched line, the to-do list and a run's results say which step refused the document and what exactly it
# looked for and did not find (the words, the SECTION line, the setting), with what the document holds instead where
# the engine recorded it (its first line, the different values it found). The engine's record is never changed.

# What a value read from the document is wanted for, by the operation that reads it
VALUE_USE = {
    "apply_header_footer_template": "for the headers and footers",
    "name_output": "for the file name",
    "set_document_properties": "for the document properties",
    "ensure_end_of_section": "for the END OF SECTION line",
}

# A saved recipe's step taken from a request is named so (``saved_recipes._label``); its own words name it here
REQUEST_STEP = "Your request"

SECTION_EXAMPLE = "“SECTION 21 13 13”"

# The names the app gives a document's own section number and title (a reference template's, a file name's); a
# prepared template's placeholder ("00 00 00", "Specification Name") is named on the line, as the template shows it
OWN_SLOT_NAMES = {"SECTION_NUMBER", "SECTION_TITLE", "number", "title"}


def _clip(text: str, limit: int = 80) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def _quoted(values, limit: int = 4) -> str:
    """“a”, “b” and 3 more: the document's or the user's own words, each clipped to a line."""
    from .ops.prose import join, quote
    values = list(values)
    shown = [quote(_clip(v)) for v in values[:limit]]
    return join(shown + ([f"{len(values) - limit} more"] if len(values) > limit else []))


def _step_prefix(exception) -> str:
    """``Step 2 of 3 (Find and replace): `` when a step of a chain refused the document (a saved recipe's step is named
    by its button, or by the user's words for a request), else empty."""
    details = exception.get("details") or {}
    number = details.get("chain_stage", exception.get("chain_stage"))
    if not isinstance(number, int) or isinstance(number, bool):
        found = re.match(r"step (\d+):", str(exception.get("reason") or ""))
        number = int(found.group(1)) if found else None
    if not number:
        return ""
    total = details.get("chain_steps")
    name = " ".join(str(details.get("step_name") or "").split())
    if re.fullmatch(r"(?i)step \d+", name):
        name = ""                                   # a composed chain's own numbering says nothing more
    elif name == REQUEST_STEP:
        words = str(details.get("step_request") or "").strip()
        name = f"“{_clip(words)}”" if words else ""
    head = f"Step {number}" + (f" of {total}" if isinstance(total, int) and total > 1 else "")
    return f"{head} ({name}): " if name else f"{head}: "


def _slot_role(slot: str, rule) -> str:
    """``number``, ``title`` or "" for the value a fill rule reads: by the rule (the usual SECTION rules) or by the
    slot's name (a prepared template's "00 00 00", "Specification Name", a file name's {number})."""
    from .ops.slots import REFERENCE_FILLS

    def patterns(r):
        if not isinstance(r, dict):
            return set()
        own = {r.get("pattern"), r.get("anchor_regex")} - {None}
        return own.union(*(patterns(sub) for sub in r.get("rules") or []))
    mine = patterns(rule)
    for name, role in (("SECTION_NUMBER", "number"), ("SECTION_TITLE", "title")):
        if mine and mine <= patterns(REFERENCE_FILLS[name]):
            return role
    try:
        from .recipes import slot_role
        return slot_role(slot)
    except Exception:  # noqa: BLE001 — a name the recipes cannot read is a value of its own
        return ""


def _slot_message(exception, details) -> str:
    """A value the change reads from the document (its own section number or title, a placeholder's fill) that it
    could not read: which value, what for, and why — none found (with the document's first line), several different
    ones (each named), or an empty one."""
    code = exception.get("code", "")
    slot = str(details.get("slot") or details.get("placeholder") or "")
    rule = details.get("rule") or {}
    role = _slot_role(slot, rule)
    operation = exception.get("op") or details.get("op") or ""
    use = VALUE_USE.get(operation, "for this change")
    if operation == "set_document_properties" and details.get("property"):
        use = f"for the document's {str(details['property']).replace('_', ' ').capitalize()} property"
    if operation == "apply_header_footer_template" and slot and slot not in OWN_SLOT_NAMES:
        use = f"for the headers and footers (where the template shows “{slot}”)"
    what = {"number": "its own section number", "title": "its own section title"}.get(role, f"the value for “{slot}”")
    values = [v for v in details.get("values") or [] if str(v).strip()]
    if code == "ambiguous" and len(values) > 1:
        found = (f"paragraphs starting with SECTION give {len(values)} different numbers, {_quoted(values)}"
                 if role == "number" else
                 f"its SECTION lines give {len(values)} different titles, {_quoted(values)}" if role == "title" else
                 f"it gives {len(values)} different ones, {_quoted(values)}")
        return f"This document's {what[4:]} {use} is not clear: {found}. Nothing is guessed."
    if code == "ambiguous":
        return f"This document's {what[4:]} {use} is not clear: more than one line could hold it. Nothing is guessed."
    if details.get("source"):
        kind, _, typ = str(details["source"]).partition(".")
        return f"This document has no {typ} {kind} to read {what} from {use}."
    if details.get("anchor"):
        return f"The line “{_clip(details['anchor'])}” has nothing after it, so {what} cannot be read {use}."
    if details.get("empty"):
        return f"{what[0].upper()}{what[1:]} reads as empty in this document, so there is nothing to put {use}."
    if role == "number":
        missing = (f"No paragraph of this document starts with SECTION and its section number (like "
                   f"{SECTION_EXAMPLE}), so its own section number cannot be read {use}.")
    elif role == "title":
        missing = (f"No line of this document gives its section title the usual way — a line that reads only SECTION "
                   f"and the number with the title on the line after it, or {SECTION_EXAMPLE[:-1]} - TITLE” on one "
                   f"line — so its own title cannot be read {use}.")
    else:
        missing = f"Nothing in this document matches what the value for “{slot}” is read from {use}."
    first = details.get("first_line")
    if first:
        return f"{missing} Its first line reads “{_clip(first)}”."
    if "first_line" in details:
        return f"{missing} The document has no text."
    return missing


def _scope_words(scope) -> str:
    from .ops.prose import SCOPE_WORDS, join
    kinds = [scope] if isinstance(scope, str) else list(scope or [])
    return join(SCOPE_WORDS.get(str(k), str(k)) for k in kinds) or "the document body"


def _not_found_message(exception, details) -> str:
    """What the change looked for and did not find, in the words the user gave it: the words to replace, the stamp,
    the paragraphs or tables a selection names."""
    operation = exception.get("op") or details.get("op") or ""
    scope = _scope_words(details.get("scope"))
    if operation == "replace_text" and details.get("find"):
        line = f"“{details['find']}” does not occur in {scope}"
        if details.get("whole_word") and details.get("longer_words"):
            line += f" as a whole word (only inside longer words, such as {_quoted(details['longer_words'], 3)})"
        if details.get("elsewhere"):
            line += f"; it occurs only in {details['elsewhere']}, which this change does not search"
        return line + "."
    if operation == "set_issue_stamp" and details.get("finds"):
        return f"The issue stamp was not found: {_quoted(details['finds'])} does not occur in {scope} as typed."
    if details.get("finds"):
        return f"None of the words to find occurs in {scope}: {_quoted(details['finds'])}."
    from .ops.prose import where
    # a selection the change needs and the document lacks (rows or columns inside the tables are the engine's words)
    for key, default in (("paragraphs", "every paragraph"), ("tables", "every table")):
        if isinstance(details.get(key), dict) and details[key] and not {"rows", "columns"} & set(details):
            words = where({key: details[key], "scope": details.get("scope")}, default=default)[3:]
            return f"This change needs {words}, and this document has none."
    return ""


def _plain_reason(reason: str, operation) -> str:
    """The engine's own reason without its prefixes and identifiers: the step and the op, a list of places in words."""
    from .ops.prose import SCOPE_WORDS, join
    reason = re.sub(r"^stage \d+ \([^)]+\):\s*", "", reason)
    reason = re.sub(r"^step \d+:\s*", "", reason)
    reason = re.sub(r"^op #\d+ \([^)]+\):\s*", "", reason)
    if operation:
        reason = re.sub(rf"^{re.escape(str(operation))}(?: \([^)]*\))?:\s*", "", reason)
    places = "|".join(SCOPE_WORDS)
    reason = re.sub(rf"\[(?:'(?:{places})'(?:, )?)+\]",
                    lambda m: join(SCOPE_WORDS[k] for k in re.findall(rf"'({places})'", m.group(0))), reason)
    return reason.strip()


EVEN_ODD = ("{document} different headers and footers on odd and even pages (Word's Different Odd & Even Pages), "
            "and the template {template}. That setting covers the whole document, so it is not changed on its own: "
            "change it in Word (Layout, Page Setup, Layout tab), or use a template set the same way.")


def refusal_message(exception, step: bool = True):
    """Why a document was left untouched, in plain words and specific: the step that refused it (``Step 2 of 3 (Find
    and replace): ``; ``step=False`` leaves it out, for a fixed plan the user never saw in steps), then what it looked
    for and what the document holds instead — no regexes or operation identifiers. This never changes the engine's
    record."""
    code = exception.get("code", "")
    reason = str(exception.get("reason") or "")
    details = exception.get("details") or {}
    operation = exception.get("op") or details.get("op")
    prefix = re.search(r"op #\d+ \(([^)]+)\):", reason)
    if operation is None and prefix:
        operation = prefix.group(1)
    head = _step_prefix(exception) if step else ""
    if code == "no-header":
        return head + "This file has no header to hold a watermark. Add a header in Word or use a header template first."
    if code == "unfilled-slot" and (operation == "fill_variables" or "slot" in details):
        slot = details.get("placeholder") or details.get("slot")
        named = f" for \"{slot}\"" if slot else ""
        return head + f"No value was given{named}. Add it to the values or the attached table, or ask to leave it as it is."
    if code == "unfilled-slot" and details.get("slots"):
        return head + (f"The template shows {_quoted(details['slots'])} where each document's own value goes, and no "
                       f"value was given or could be read for it. Fill it in the form, or use a template without it.")
    if code in {"not-found", "ambiguous"} and "slot" in details:
        return head + _slot_message(exception, details)
    if code == "even-odd" and "template_even_odd" in details:
        on = bool(details["template_even_odd"])
        return head + EVEN_ODD.format(document="This document does not have" if on else "This document has",
                                      template="does" if on else "does not")
    if code == "title-page":
        return head + ("This document's first page and the template's differ on a header and footer of their own "
                       "(Word's Different First Page), and matching that is turned off.")
    if code == "template-revisions":
        return head + ("The template's headers or footers carry tracked changes. Accept or reject them in the "
                       "template first.")
    if code == "not-found":
        specific = _not_found_message(exception, details)
        if specific:
            return head + specific
    if code == "name-collision":
        return head + ("Two documents would have the same output filename. Check their section numbers and titles "
                       "before trying again.")
    if exception.get("kind") in {"internal-error", "environment"}:
        return head + "The change could not be completed for this file. The diagnostic record has the technical details."
    plain = _plain_reason(reason, operation)
    if code == "ambiguous" and plain:
        return head + f"More than one place could be meant, so nothing is guessed: {plain[0].lower()}{plain[1:]}"
    return head + (plain or "This file could not take the requested change; it was left untouched.")
