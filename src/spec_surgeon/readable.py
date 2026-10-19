"""Bounded human views of evidence; machine records and verification decisions stay intact."""
from __future__ import annotations

import re
from collections import OrderedDict
from pathlib import Path
from typing import Iterable, List

RECORD_BYTES = 100_000
REPORT_BYTES = 500_000
EXAMPLES = 3


def short(text: object, limit: int = 600) -> str:
    value = str(text)
    return value if len(value) <= limit else value[:limit - 1] + "…"


def bounded(text: str, limit: int, reference: str) -> str:
    """Fit UTF-8 bytes strictly below a cap, keeping the beginning and a durable evidence reference."""
    data = text.encode("utf-8")
    if len(data) < limit:
        return text
    note = (f"\n\n[Truncated: this human view was {len(data):,} bytes. Remaining details are omitted here; "
            f"full machine evidence: {reference}.]\n")
    budget = max(0, limit - len(note.encode("utf-8")) - 1)
    return data[:budget].decode("utf-8", errors="ignore") + note


def write_report(path: Path, text: str, reference: str) -> None:
    # Count and write the same bytes; Windows text-mode CRLF expansion can exceed the cap.
    Path(path).write_bytes(bounded(text, REPORT_BYTES, reference).encode("utf-8"))


# Only location numbers are normalised. Font names, slot names, expected values and failures stay distinct.
_LOCATION = re.compile(r"\b(block|paragraph|run|page|row|cell|position|index)\s+#?\d+\b", re.IGNORECASE)


def finding_identity(text: str) -> str:
    return _LOCATION.sub(lambda m: m.group(1) + " *", text)


def grouped_findings(lines: Iterable[str], limit: int = 12) -> List[str]:
    """One finding per identity, its occurrence count, and at most three original locations."""
    groups: OrderedDict[str, list] = OrderedDict()
    for raw in lines:
        line = str(raw)
        identity = finding_identity(line)
        group = groups.setdefault(identity, [0, []])
        group[0] += 1
        if len(group[1]) < EXAMPLES:
            group[1].append(short(line))
    result = []
    for identity, (count, examples) in list(groups.items())[:limit]:
        if count == 1:
            result.append(examples[0])
        else:
            result.append(f"{count:,} occurrences: {short(identity, 400)}; examples: " + " · ".join(examples))
    if len(groups) > limit:
        result.append(f"… {len(groups) - limit:,} more finding identities; see the full machine evidence.")
    return result
