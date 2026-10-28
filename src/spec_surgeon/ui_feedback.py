"""Plain-language presentation of existing results. Raw exceptions and verification records stay in diagnostics."""
import re


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


def refusal_message(exception):
    """Explain common refusals without regexes or operation identifiers. This never changes the engine's record."""
    code = exception.get("code", "")
    reason = str(exception.get("reason") or "")
    operation = exception.get("op") or (exception.get("details") or {}).get("op")
    prefix = re.search(r"op #\d+ \(([^)]+)\):", reason)
    if operation is None and prefix:
        operation = prefix.group(1)
    if code == "no-header":
        return "This file has no header to hold a watermark. Add a header in Word or use a header template first."
    if operation == "name_output" and code in {"not-found", "unfilled-slot", "ambiguous"}:
        return "The section number or title needed for the filename could not be read clearly. Check this file's SECTION heading and title."
    details = exception.get("details") or {}
    if code == "unfilled-slot" and (operation == "fill_variables" or "slot" in details):
        slot = details.get("placeholder") or details.get("slot")
        named = f" for \"{slot}\"" if slot else ""
        return f"No value was given{named}. Add it to the values or the attached table, or ask to leave it as it is."
    common = {
        "not-found": "This file does not contain the text or location needed for this change.",
        "unfilled-slot": "A value needed by the template could not be read from this file. Check its section number and title.",
        "ambiguous": "More than one possible match was found, so the change could not be applied unambiguously.",
        "name-collision": "Two documents would have the same output filename. Check their section numbers and titles before trying again.",
    }
    if code in common:
        return common[code]
    if exception.get("kind") in {"internal-error", "environment"}:
        return "The change could not be completed for this file. The diagnostic record has the technical details."
    reason = re.sub(r"^stage \d+ \([^)]+\):\s*", "", reason)
    reason = re.sub(r"op #\d+ \([^)]+\):\s*", "", reason)
    return reason or "This file could not take the requested change; it was left untouched."
