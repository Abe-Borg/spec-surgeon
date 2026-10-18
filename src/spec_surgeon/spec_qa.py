"""Read-only specification QA (items 45–49). Never creates or repacks a DOCX.

Reports retain every occurrence with its source location. Missing information and
unreadable files remain explicit; a partial scan cannot report issue readiness.
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path
from zipfile import BadZipFile

from lxml import etree

from .ooxml import paragraphs, revision_kind
from .ops.markup import Look
from .ops.selectors import StyleSheet
from .package import DocxPackage, W
from .structure import analyze_structure, canonical_reference

AUDITS = ("placeholders", "cross_references", "standards", "readiness", "project_identity",
          "duplicate_provisions", "internal_references")
NUMBER = r"\d{2}[ \u00a0–—-]*\d{2}[ \u00a0–—-]*\d{2}(?:\.\d{2})?"
PLACEHOLDER = re.compile(r"\[[^\[\]\r\n]*\]|<\s*Insert\b[^>]*>|\bX{4,}\b|\bTBD\b|\{\{[^{}]+\}\}", re.I)
FAMILY = re.compile(r"\b(?:NFPA|UL|FM|ASTM|ASME)\b", re.I)
DESIGNATION = re.compile(r"\s*(?:(?:Approval\s+)?Standard\s*|Code\s*|No\.\s*)?"
                         r"(?P<id>(?:[A-Z]{1,4}\s*)?\d+(?:\.\d+)*[A-Z]?(?:/[A-Z]*\d+[A-Z]?)?)(?!\w)", re.I)
MONTH = (r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|"
         r"Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?")
DATE = re.compile(r"\b(?:\d{4}[-/.]\d{1,2}[-/.]\d{1,2}|\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}|"
                  + MONTH + r"\s+(?:\d{1,2},?\s+)?\d{4}|\d{1,2}\s+"+MONTH+r"\s+\d{4})\b", re.I)


def _number(text):
    compact = re.sub(r"[ \u00a0–—-]+", "", text)
    return f"{compact[:2]} {compact[2:4]} {compact[4:]}"


def _normalized(text):
    return " ".join(re.sub(r"[^\w]+", " ", text.casefold()).split())


def _text(p):
    out = []
    for node in p.iter():
        if node.tag not in (W+"t", W+"tab", W+"br", W+"cr"):
            continue
        ancestors = list(node.iterancestors())
        if next((a for a in ancestors if a.tag == W+"p"), None) is not p:
            continue
        if any(a.tag in (W+"del", W+"moveFrom") for a in ancestors):
            continue
        out.append(node.text or "" if node.tag == W+"t" else "\t" if node.tag == W+"tab" else "\n")
    return "".join(out)


def _sections(pkg, rows):
    definitions = []
    main_rows = [r for r in rows if r["part"] == pkg.main_part() and not r["nested"] and r["text"].strip()]
    for i, row in enumerate(main_rows):
        # An uppercase SECTION heading is distinct from prose such as "Section 21 05 00".
        m = re.fullmatch(r"\s*SECTION\s+("+NUMBER+r")(?:\s*[:–—-]\s*|\s+)?(.*?)\s*", row["text"])
        if m is None and i == 0:
            m = re.fullmatch(r"\s*("+NUMBER+r")(?:\s*[:–—-]\s*|\s+)?(.*?)\s*", row["text"])
        if m is None:
            continue
        title, title_paragraph = m[2].strip(), None
        if not title and i+1 < len(main_rows):
            following = main_rows[i+1]
            if not re.match(r"(?:PART\s+\d|SECTION\s|\d+\.)", following["text"].strip()):
                title, title_paragraph = following["text"].strip(), following["paragraph"]
        definitions.append({"number": _number(m[1]), "title": title or None, "file": row["file"],
                            "part": row["part"], "paragraph": row["paragraph"], "title_paragraph": title_paragraph,
                            "identity_source": "heading"})
    if not definitions:
        m = re.search(r"(?<!\d)("+NUMBER+r")(?!\d)", Path(pkg.name).stem)
        if m:
            title = Path(pkg.name).stem[m.end():].strip(" –—-_")
            definitions.append({"number": _number(m[1]), "title": title or None, "file": str(Path(pkg.name).resolve()),
                                "part": pkg.main_part(), "paragraph": -1, "title_paragraph": None,
                                "identity_source": "filename_only"})
    return definitions


def _location(row, start=0, text=None):
    return {k: row.get(k) for k in ("file", "section", "part", "paragraph")} | {
        "start": start, "text": row["text"] if text is None else text, "context": row["text"],
        **({"location": row["location"]} if row.get("location") else {})}


def _structure_location(row, file):
    return {"file": str(file), "section": row["section_id"], "part": row["part"],
            "paragraph": row["paragraph"], "text": row["text"], "location": row["location"]}


def _unassessed_structure(structure, file):
    return [{**_structure_location(r, file), "reason": r["reason"] if r["disposition"] != "classified"
             else "logical hierarchy contains repeated or ambiguous labels"}
            for r in structure["paragraphs"] if r["text"].strip() and
            (r["disposition"] != "classified" or not r["hierarchy_reliable"])]


def _duplicate_provisions(structure, file):
    """Advisory sibling comparisons; wording similarity never authorizes deletion."""
    groups = defaultdict(list)
    for row in structure["paragraphs"]:
        if row["depth"] is not None and row["depth"] >= 3 and row["hierarchy_reliable"]:
            groups[row["parent"]].append(row)
    findings, unassessed = [], _unassessed_structure(structure, file)
    comparisons = 0
    for siblings in groups.values():
        previous = []
        for row in siblings:
            content = _normalized(row["content"])
            numbers = re.findall(r"[0-9]+(?:[./-][0-9]+)*", row["content"])
            for earlier, normalized, earlier_numbers in previous:
                if comparisons >= 50000:
                    unassessed.append({**_structure_location(row, file),
                                       "reason": "document comparison limit reached (50000 sibling pairs)"})
                    break
                comparisons += 1
                if numbers != earlier_numbers or not content:
                    continue
                exact = content == normalized
                if not exact and min(len(content.split()), len(normalized.split())) < 6:
                    continue
                matcher = SequenceMatcher(None, normalized, content, autojunk=False)
                if not exact and (matcher.real_quick_ratio() < .92 or matcher.quick_ratio() < .92):
                    continue
                similarity = 1.0 if exact else matcher.ratio()
                if similarity >= .92:
                    findings.append({**_structure_location(row, file), "status": "duplicate" if exact else "near_duplicate",
                                     "similarity": round(similarity, 4), "parent": row["parent"],
                                     "other": _structure_location(earlier, file),
                                     "reason": "similar sibling provisions with identical numeric tokens; review both requirements"})
                    break
            previous.append((row, content, numbers))
    return {"status": "partial" if unassessed else "evaluated", "findings": findings,
            "unassessed": unassessed, "coverage": structure["coverage"], "comparisons": comparisons,
            "limitations": ["Only provisions with an unambiguous immediate parent are compared.",
                            "Numeric tokens must match; labels are excluded from wording comparison.",
                            "Similarity is advisory and never authorizes removing a requirement."]}


_INTERNAL_REFERENCE = re.compile(
    r"\b(Article|Paragraph)\s+([0-9]+(?:\.[0-9]+)?(?:\.[A-Za-z0-9]+)*|[A-Z](?:\.[A-Za-z0-9]+)*)(?!\w)", re.I)


def _external_reference(text, match):
    # Limit the qualifier to the same semicolon/newline-delimited clause. This
    # avoids treating a separate citation elsewhere in the paragraph as authority.
    before = re.split(r"[;\n]|[.!?](?=\s|$)", text[:match.start()])[-1]
    after = re.split(r"[;\n]", text[match.end():])[0]
    # Sentence boundaries after ordinary prose stop a following standard citation.
    after = re.split(r"[.!?](?=\s|$)", after)[0]
    external = r"(?:NFPA|UL|FM|ASTM|ASME|IBC|IFC|ISO|ANSI|AWWA|CSA|IEC|EN|API|Section\s+" + NUMBER + r")\b"
    if re.search(external, before, re.I):
        return True
    return bool(re.match(r"\s+(?:of|in|under|per)\s+(?:the\s+)?" + external, after, re.I))


def _internal_references(structure, file):
    rows = structure["paragraphs"]
    addresses = defaultdict(list)
    uncertain_sections = set()
    for row in rows:
        if row["reference_id"]:
            addresses[(row["section_paragraph"], row["reference_id"])].append(row)
        if row["text"].strip() and (row["disposition"] != "classified" or not row["hierarchy_reliable"]):
            uncertain_sections.add(row["section_paragraph"])
    findings = []
    for row in rows:
        for match in _INTERNAL_REFERENCE.finditer(row["text"]):
            label = canonical_reference(match[2])
            if row["role"] == "article" and not row["text"][:match.start()].strip() and label == row["reference_id"]:
                continue
            if re.match(r"^[A-Z](?:\.|$)", label) and row["article_label"]:
                label = row["article_label"] + "." + label
            targets = addresses.get((row["section_paragraph"], label), [])
            if _external_reference(row["text"], match):
                status, reason = "external_reference", "citation is qualified by an external standard or another section"
                targets = []
            elif row["disposition"] == "excluded" or row["section_paragraph"] is None:
                status, reason = "unassessed", "source location has no supported section hierarchy"
            elif not re.fullmatch(r"[0-9]+\.[0-9]+(?:\.[A-Za-z0-9]+)*", label):
                status, reason = "unassessed", "reference syntax or relative article context is unsupported"
            elif len(targets) > 1:
                status, reason = "ambiguous_target", "more than one paragraph has this logical address"
            elif targets and targets[0]["hierarchy_reliable"]:
                status, reason = "matched", "one unambiguous target exists in this section"
            elif targets or row["section_paragraph"] in uncertain_sections:
                status, reason = "unassessed", "incomplete or ambiguous hierarchy cannot prove this target absent"
            else:
                status, reason = "missing_target", "no target exists in the fully classified section hierarchy"
            findings.append({**_structure_location(row, file), "start": match.start(), "text": match[0],
                             "context": row["text"], "reference_id": label, "status": status, "reason": reason,
                             "targets": [_structure_location(target, file) for target in targets]})
    unassessed = _unassessed_structure(structure, file)
    return {"status": "partial" if unassessed or any(f["status"] == "unassessed" for f in findings) else "evaluated",
            "findings": findings, "unassessed": unassessed, "coverage": structure["coverage"],
            "limitations": ["Explicit singular Article/Paragraph references use section-local addresses.",
                            "Known external standards and other-section qualifiers are not resolved here.",
                            "Missing targets are reported only when the section hierarchy is fully assessed.",
                            "Plural lists, ranges, named headings and bare numeric citations are outside coverage."]}


def _standards(row):
    text, out = row["text"], []
    families = list(FAMILY.finditer(text))
    for i, family in enumerate(families):
        tail_end = families[i+1].start() if i+1 < len(families) else len(text)
        tail = text[family.end():tail_end].split(";")[0].split("\n")[0]
        datasheet = re.match(r"\s*(?:Global\s+)?(?:(?:Property\s+Loss\s+Prevention\s+)?Data\s+Sheets?|DS)\s*"
                             r"(?P<id>\d+(?:-\d+)+)", tail, re.I) if family.group().upper() == "FM" else None
        designation = datasheet or DESIGNATION.match(tail)
        identifier = re.sub(r"\s+", "", designation["id"]).upper() if designation else None
        editions, end = [], len(family.group())
        if designation:
            remainder = tail[designation.end():]
            # Stop at the citation's sentence, after parsing decimal designations
            # and "No.". A month abbreviation can still introduce an edition date.
            for boundary in re.finditer(r"[.!?](?=\s|$)", remainder):
                if (boundary[0] == "." and re.search(r"\b"+MONTH+r"$", remainder[:boundary.start()], re.I)
                        and re.match(r"\s+(?:\d{1,2},?\s+)?\d{4}\b", remainder[boundary.end():])):
                    continue
                remainder = remainder[:boundary.start()]
                break
            attached = re.match(r"[-–](\d{2,4}[a-z]?)\b", remainder, re.I)
            if attached:
                editions.append(attached[1])
            editions += [m[0] for m in re.finditer(r"\b(?:19|20)\d{2}[a-z]?\b", remainder, re.I)]
            editions = list(dict.fromkeys(editions))
            end = family.end()+designation.end()+len(remainder)-family.start()
        record = _location(row, family.start(), text[family.start():family.start()+end].strip())
        record.update({"family": family.group().upper(), "designation": identifier, "editions": editions,
                       "edition": editions[0] if len(editions) == 1 else None,
                       "status": "designation_missing" if not identifier else "edition_missing" if not editions else "recorded"})
        if datasheet:
            record["reference_type"] = "data_sheet"
        out.append(record)
    return out


def _identity_rows(row, canonical):
    text, out = row["text"], []
    labels = {
        "project_name": r"(?:project\s+name|job\s+name|project)(?!\s*(?:number|no\b|#))\s*:\s*([^\t\n]+)",
        "project_number": r"(?:project|job)\s*(?:number|no\.?|#)\s*[:#]?\s*([^\t\n]+)",
        "date": r"(?:issue\s+date|project\s+date|date)\s*:\s*([^\t\n]+)",
    }
    for field, pattern in labels.items():
        for m in re.finditer(pattern, text, re.I):
            out.append({**_location(row, m.start(1), m[1].strip()), "field": field, "basis": "label"})
    if row["kind"] in ("headers", "footers") or row["paragraph"] < 12:
        for m in DATE.finditer(text):
            if not any(r["field"] == "date" and m.group() in r["text"] for r in out):
                out.append({**_location(row, m.start(), m.group()), "field": "date", "basis": "date_candidate"})
        for field in ("project_name", "project_number"):
            expected = canonical.get(field)
            if not expected:
                continue
            for candidate in re.split(r"[\t\n]", text):
                candidate = candidate.strip()
                score = SequenceMatcher(None, _normalized(candidate), _normalized(expected)).ratio()
                if candidate and score >= 0.72 and not any(r["field"] == field and r["text"] == candidate for r in out):
                    out.append({**_location(row, text.index(candidate), candidate), "field": field,
                                "basis": "similarity_candidate"})
    return out


def _read_file(path, canonical, structural_audits=()):
    data = path.read_bytes()
    pkg = DocxPackage(data, name=str(path))
    kinds = pkg.parts_by_kind()
    part_kinds = {p: k for k, parts in kinds.items() for p in parts}
    # Include orphaned Word text parts: they may still hold comments or review debris.
    roots = {p: pkg.xml(p) for p in pkg.text_parts()}
    for part in pkg.member_names:
        if part.startswith("word/") and part.endswith(".xml") and part not in roots:
            root = pkg.xml(part)
            if any(True for _ in root.iter(W+"p", W+"comment")):
                roots[part] = root
                part_kinds[part] = "other_word_part"
    rows, row_by_paragraph = [], {}
    for part, root in roots.items():
        for bi, p in enumerate(paragraphs(root)):
            row = {"file": str(path), "part": part, "paragraph": bi, "text": _text(p),
                   "kind": part_kinds[part], "nested": any(a.tag == W+"p" for a in p.iterancestors())}
            rows.append(row)
            row_by_paragraph[p] = row
    sections = _sections(pkg, rows)
    for row in rows:
        preceding = [s for s in sections if s["paragraph"] <= row["paragraph"]] if row["kind"] == "document" else []
        row["section"] = (preceding[-1]["number"] if preceding else sections[0]["number"] if len(sections) == 1 else None)
    placeholders = [{**_location(row, m.start(), m.group()), "marker": m.group()} for row in rows
                    for m in PLACEHOLDER.finditer(row["text"])]
    standards = [ref for row in rows for ref in _standards(row)]
    definitions = {(s["part"], s["paragraph"]) for s in sections}
    references = []
    for row in rows:
        for m in re.finditer(r"\bSection\s+("+NUMBER+r")(?!\w|\.\d)", row["text"], re.I):
            if (row["part"], row["paragraph"]) in definitions:
                continue
            tail = row["text"][m.end():]
            title = re.match(r'\s*(?:[,;:]?\s*[“\"]([^”\"]+)[”\"]|[–—-]\s*([^.;\n]+))', tail)
            references.append({**_location(row, m.start(), m.group()), "number": _number(m[1]),
                               "stated_title": (title[1] or title[2]).strip() if title else None})
    markup, unknown = [], []
    look = Look(StyleSheet(pkg))
    for part, root in roots.items():
        for el in root.iter():
            revision = revision_kind(el)
            if revision:
                paragraph = next((a for a in el.iterancestors() if a.tag == W+"p"), None)
                row = row_by_paragraph.get(paragraph)
                location = _location(row) if row is not None else {"part": part}
                markup.append({**location, "type": "tracked_changes", "revision": revision,
                               "author": el.get(W+"author"), "id": el.get(W+"id")})
            if el.tag == W+"comment":
                markup.append({"type": "comments", "part": part, "id": el.get(W+"id"), "author": el.get(W+"author")})
        for bi, p in enumerate(paragraphs(root)):
            for r in p.iter(W+"r"):
                if next((a for a in r.iterancestors() if a.tag == W+"p"), None) is not p:
                    continue
                rpr = r.find(W+"rPr")
                hidden = look.hidden(rpr, p)
                if hidden is None:
                    unknown.append(f"ambiguous inherited hidden formatting: {part} paragraph {bi+1}")
                if hidden:
                    markup.append({"type": "hidden_text", "part": part, "paragraph": bi, "text": _text(p)})
                if look.toggle(rpr, p, "specVanish"):
                    markup.append({"type": "hidden_text", "part": part, "paragraph": bi, "text": _text(p), "kind": "specVanish"})
                # Effective highlight (direct, character, paragraph, table, defaults).
                levels = look.pr._levels_run(p, rpr) + [("direct", rpr)]
                h = next((pr.find(W+"highlight") for _, pr in reversed(levels)
                          if pr is not None and pr.find(W+"highlight") is not None), None)
                if h is not None and h.get(W+"val") != "none":
                    markup.append({"type": "highlights", "part": part, "paragraph": bi, "color": h.get(W+"val")})
            ppr = p.find(W+"pPr")
            mark = ppr.find(W+"rPr") if ppr is not None else None
            if look.hidden(mark, p):
                markup.append({"type": "hidden_text", "part": part, "paragraph": bi, "text": "[paragraph mark]"})
            h = mark.find(W+"highlight") if mark is not None else None
            if h is not None and h.get(W+"val") != "none":
                markup.append({"type": "highlights", "part": part, "paragraph": bi, "color": h.get(W+"val")})
    comment_ids = {el.get(W+"id") for root in roots.values() for el in root.iter(W+"comment")}
    for part, root in roots.items():
        for el in root.iter(W+"commentRangeStart", W+"commentRangeEnd", W+"commentReference"):
            if el.get(W+"id") not in comment_ids:
                markup.append({"type": "comments", "part": part, "id": el.get(W+"id"), "kind": "orphan_anchor"})
    # Markup without a content paragraph, including style snapshots and orphan comment anchors.
    for part in pkg.member_names:
        if part.endswith(".xml") and part not in roots:
            for el in pkg.xml(part).iter():
                if revision_kind(el):
                    markup.append({"type": "tracked_changes", "part": part, "revision": revision_kind(el)})
    if any(n.startswith("word/embeddings/") or n.endswith("vbaProject.bin") for n in pkg.member_names):
        unknown.append("embedded objects or macros require separate inspection")
    if not sections:
        unknown.append("no section identity could be established")
    identity = [item for row in rows for item in _identity_rows(row, canonical)]
    # Custom document properties are frequently the source of project identity fields.
    for part in pkg.member_names:
        if not part.startswith("docProps/") or not part.endswith(".xml"):
            continue
        for prop in pkg.xml(part):
            key = re.sub(r"[^a-z]", "", (prop.get("name") or "").lower())
            field = {"projectname": "project_name", "projectnumber": "project_number", "projectno": "project_number",
                     "issuedate": "date", "projectdate": "date"}.get(key)
            if field:
                identity.append({"file": str(path), "section": None, "part": part, "paragraph": None,
                                 "start": 0, "text": "".join(prop.itertext()), "field": field, "basis": "property"})
    counts = dict(Counter(m["type"] for m in markup))
    counts["placeholders"] = len(placeholders)
    readiness = {"status": "not_ready" if any(counts.values()) else "incomplete" if unknown else "no_findings",
                 "counts": counts, "findings": markup + [{**p, "type": "placeholders"} for p in placeholders],
                 "limitations": list(dict.fromkeys(unknown))}
    result = {"file": str(path), "source_sha256": hashlib.sha256(data).hexdigest(), "sections": sections,
            "placeholders": placeholders, "cross_references": references, "standards": standards,
            "readiness": readiness, "project_identity": identity}
    if structural_audits:
        structure = analyze_structure(pkg)
        result["structure"] = {k: structure[k] for k in ("schema_version", "analyzer_version", "coverage", "limitations")}
        if "duplicate_provisions" in structural_audits:
            result["duplicate_provisions"] = _duplicate_provisions(structure, path)
        if "internal_references" in structural_audits:
            result["internal_references"] = _internal_references(structure, path)
    return result


def audit_batch(files, audits=None, identity=None, project_context=None):
    selected = list(audits or AUDITS)
    if set(selected)-set(AUDITS):
        raise ValueError(f"unknown audits: {sorted(set(selected)-set(AUDITS))}")
    canonical = {} if identity is None else identity
    if not isinstance(canonical, dict) or any(k not in ("project_name", "project_number", "date") or
                                              not isinstance(v, str) or not v.strip() for k, v in canonical.items()):
        raise ValueError("identity is an object with optional non-empty project_name, project_number and date strings")
    if project_context is not None:
        from .project_evidence import validate_context
        # Validate caller input before scanning; invalid registers are not partial
        # document failures and must not silently produce an empty comparison.
        project_context = validate_context(project_context)
    report = {"schema_version": 2, "report_only": True, "audits": selected, "files": [], "errors": [],
              "text_view": ("Current text, including pending insertions, excluding pending deletions. All tracked changes are audited separately. "
                            "Structural evidence retains source wording in excluded revision paragraphs; those paragraphs are never assessed."),
              "limitations": ["Audits inspect DOCX content; no standard editions or document text are changed.",
                              "Latest and AHJ-adopted standard editions are not inferred from a publication year.",
                              "Project identity uses labels and properties; unlabelled near-matches need canonical identity values.",
                              "No findings means none detected by these checks; it is not an issue certification."]}
    for path in dict.fromkeys(Path(f).resolve() for f in files):
        try:
            file = _read_file(path, canonical, set(selected) & {"duplicate_provisions", "internal_references"})
            if project_context is not None and "standards" in selected:
                from .project_evidence import audit_standards_register
                file["standards_register"] = []
                for ref in file["standards"]:
                    observation = {"identifier": f"{ref['family']} {ref['designation']}" if ref["designation"] else ref["family"],
                                   "edition": ref["edition"], "location": ref}
                    scopes = [{"kind": "document", "value": str(path)}]
                    if ref["section"] and any(s["number"] == ref["section"] and s["identity_source"] == "heading"
                                              for s in file["sections"]):
                        scopes.append({"kind": "section", "value": ref["section"]})
                    file["standards_register"] += audit_standards_register([observation], project_context, scopes=scopes)
            report["files"].append(file)
        except (OSError, ValueError, KeyError, BadZipFile, etree.Error) as e:
            report["errors"].append({"file": str(path), "error": f"{type(e).__name__}: {e}"})
    index = defaultdict(list)
    for file in report["files"]:
        for section in file["sections"]:
            index[section["number"]].append(section)
    incoming = set()
    for file in report["files"]:
        for ref in file["cross_references"]:
            targets = index.get(ref["number"], [])
            if targets and ((ref["section"] is not None and ref["number"] != ref["section"])
                            or any(target["file"] != ref["file"] for target in targets)):
                incoming.add(ref["number"])
            ref["target_files"] = [s["file"] for s in targets]
            ref["target_titles"] = [s["title"] for s in targets]
            if not targets:
                status = "missing_section"
            elif len(targets) > 1:
                status = "ambiguous_target"
            elif not targets[0]["title"]:
                status = "target_title_missing"
            elif ref["stated_title"] and _normalized(ref["stated_title"]) != _normalized(targets[0]["title"]):
                status = "title_mismatch"
            elif targets[0]["identity_source"] == "filename_only":
                status = "target_heading_unconfirmed"
            else:
                status = "matched" if ref["stated_title"] else "exists_title_not_stated"
            ref["status"] = status
    report["unreferenced_sections"] = [s for number, sections in index.items() if number not in incoming for s in sections]
    report["duplicate_sections"] = {number: sections for number, sections in index.items() if len(sections) > 1}
    variants = {field: {} for field in ("project_name", "project_number", "date")}
    for file in report["files"]:
        for record in file["project_identity"]:
            field, value = record["field"], record["text"]
            variants[field].setdefault(value, []).append(record)
    report["identity_variants"] = {field: {"expected": canonical.get(field), "variants": values,
                                           "has_variants": len(values) > 1,
                                           "different_from_expected": [v for v in values if canonical.get(field) and v != canonical[field]],
                                           "missing_in_files": [f["file"] for f in report["files"] if not any(
                                               r["field"] == field for r in f["project_identity"])]}
                                   for field, values in variants.items()}
    report["complete"] = not report["errors"]
    for file in report["files"]:
        if report["errors"]:
            file["readiness"]["limitations"].append("batch contains unreadable files")
        for audit in AUDITS:
            if audit not in selected:
                file.pop(audit, None)
    if "cross_references" not in selected:
        for key in ("unreferenced_sections", "duplicate_sections"):
            report.pop(key)
    if "project_identity" not in selected:
        report.pop("identity_variants")
    return report


def _literal(value):
    """A Markdown code span that preserves literal placeholder/HTML/backtick text."""
    text = str(value).replace("\r", "\\r").replace("\n", "\\n")
    delimiter = "`" * (max((len(m[0]) for m in re.finditer(r"`+", text)), default=0) + 1)
    return delimiter + " " + text + " " + delimiter


def audit_markdown(report):
    lines = ["# Specification QA audit", "", "Report only — source files unchanged.", "", report["text_view"], ""]
    for err in report["errors"]:
        lines += [f"UNREADABLE: {_literal(err['file'])}: {_literal(err['error'])}", ""]
    for file in report["files"]:
        lines += [f"## {_literal(file['file'])}", "", f"Source SHA-256: {file['source_sha256']}", ""]
        for audit in report["audits"]:
            lines += [f"### {audit.replace('_', ' ').title()}", ""]
            items = file[audit]
            if audit == "readiness":
                lines += [f"{items['status']}: {items['counts']}", ""]
                lines += [f"- {_literal(x)}" for x in items["limitations"]]
                items = items["findings"]
            elif audit in ("duplicate_provisions", "internal_references"):
                lines += [f"Assessment: {items['status']}; paragraph coverage: {items['coverage']}", ""]
                lines += [f"- {_literal(x)}" for x in items["limitations"]]
                for item in items["unassessed"]:
                    lines.append(f"- Unassessed: {_literal(item['location'])}: {_literal(item['reason'])}")
                lines.append("")
                items = items["findings"]
            for item in items:
                location = item.get("location") or (f"{item.get('part', '?')} paragraph {item['paragraph']+1}" if isinstance(item.get("paragraph"), int) else item.get("part", "?"))
                if audit == "readiness":
                    detail = item["type"] + " " + "; ".join(str(item[k]) for k in ("revision", "color", "text", "id", "author")
                                                              if item.get(k) is not None)
                elif audit == "cross_references":
                    detail = f"{item['text']} — {item['status']}; titles: {item['target_titles']}"
                elif audit == "standards":
                    detail = f"{item['text']} — edition(s): {item['editions'] or 'NOT STATED'} ({item['status']})"
                elif audit == "duplicate_provisions":
                    detail = f"{item['status']} ({item['similarity']}): {item['text']}; compare {item['other']['location']}"
                elif audit == "internal_references":
                    detail = f"{item['text']} — {item['status']}: {item['reason']}"
                else:
                    detail = item["text"]
                lines.append(f"- Section {item.get('section') or '?'}; {_literal(location)}: {_literal(detail)}")
            if not items:
                lines.append("No occurrences detected.")
            lines.append("")
        if "standards_register" in file:
            lines += ["### Approved standards register", ""]
            for item in file["standards_register"]:
                lines.append(f"- {_literal(item['identifier'])}: {item['status']} — {_literal(item['reason'])}")
            lines.append("")
    if "unreferenced_sections" in report:
        lines += ["## Sections with no incoming reference", ""]
        lines += [f"- {s['number']} — {_literal(s['title'] or 'TITLE MISSING')} ({_literal(s['file'])})" for s in report["unreferenced_sections"]]
        lines += ["", "## Duplicate section numbers", ""]
        lines += [f"- {number}: {_literal([s['file'] for s in files])}" for number, files in report["duplicate_sections"].items()]
    if "identity_variants" in report:
        lines += ["", "## Project identity variants", ""]
        for field, values in report["identity_variants"].items():
            lines += [f"{field}: expected {_literal(repr(values['expected']))}; variants detected: {values['has_variants']}", ""]
            lines += [f"- {_literal(repr(value))}: {len(occurrences)} occurrence(s) in {_literal(sorted({r['file'] for r in occurrences}))}"
                      for value, occurrences in values["variants"].items()]
            lines += [f"- No value identified: {_literal(path)}" for path in values["missing_in_files"]]
    lines += ["", "## Scope and limitations", ""] + [f"- {x}" for x in report["limitations"]]
    return "\n".join(lines) + "\n"
