"""Read-only, project-scoped discovery for a mixed document collection.

Project membership is evidence, not a guess based on a structural fingerprint. A nearby
PROJECT.json establishes a boundary only inside an explicitly selected input root; otherwise
each folder remains a separate provisional group. Source bytes are never modified.
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import zipfile
from pathlib import Path
from typing import Any, Dict, Sequence

from . import guard
from .fingerprint import fingerprint
from .package import DocxPackage, W


RESULT_MARKERS = {"campaign.json", "recovery.json", "set_aside.json", "preview.json", "BATCH_REPORT.md",
                  "CHAIN_REPORT.md"}
IGNORED_DIRS = {".git", ".venv", "__pycache__", "node_modules"}
DIVISIONS = {"00": "Procurement and contracting", "01": "General requirements", "02": "Existing conditions",
             "03": "Concrete", "04": "Masonry", "05": "Metals", "06": "Wood, plastics and composites",
             "07": "Thermal and moisture protection", "08": "Openings", "09": "Finishes", "10": "Specialties",
             "11": "Equipment", "12": "Furnishings", "13": "Special construction", "14": "Conveying equipment",
             "21": "Fire suppression", "22": "Plumbing", "23": "HVAC", "25": "Integrated automation", "26": "Electrical",
             "27": "Communications", "28": "Electronic safety and security", "31": "Earthwork",
             "32": "Exterior improvements", "33": "Utilities", "34": "Transportation", "35": "Waterway and marine construction",
             "40": "Process interconnections", "41": "Material processing and handling equipment",
             "42": "Process heating, cooling and drying equipment", "43": "Process gas and liquid handling",
             "44": "Pollution and waste control equipment", "45": "Industry-specific manufacturing equipment",
             "46": "Water and wastewater equipment", "48": "Electrical power generation"}
SECTION = re.compile(r"^\s*SECTION\s+(\d{2})[ -]?(\d{2})[ -]?(\d{2})(?!\d)", re.I)
FILE_SECTION = re.compile(r"^(\d{2})[ -]?(\d{2})[ -]?(\d{2})(?!\d)")


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def stable_id(kind: str, value: str) -> str:
    return kind + "-" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _inside(path: Path, folder: Path) -> bool:
    return path == folder or folder in path.parents


def _result_folder(path: Path) -> bool:
    return any((path / name).is_file() for name in RESULT_MARKERS)


def _metadata(path: Path, root: Path):
    folder = path.parent
    while _inside(folder, root):
        metadata = folder / "PROJECT.json"
        if metadata.is_file():
            try:
                value = json.loads(metadata.read_text(encoding="utf-8"))
                if not isinstance(value, dict):
                    raise ValueError("PROJECT.json must be a JSON object")
                return metadata, value, ""
            except (OSError, ValueError) as exc:
                return metadata, {}, f"Cannot read project metadata: {exc}"
        if folder == root:
            break
        folder = folder.parent
    return None, {}, "Project is provisional: no PROJECT.json within the selected input root."


def _classification(pkg: DocxPackage, path: Path) -> Dict[str, Any]:
    evidence, numbers = [], set()
    for p in pkg.xml(pkg.main_part()).iter(W + "p"):
        text = "".join(t.text or "" for t in p.iter(W + "t"))
        hit = SECTION.match(text)
        if hit:
            value = " ".join(hit.groups())
            numbers.add(value)
            evidence.append({"kind": "section_heading", "value": value, "text": text[:200]})
    hit = FILE_SECTION.match(path.stem)
    if hit:
        value = " ".join(hit.groups())
        numbers.add(value)
        evidence.append({"kind": "filename_section", "value": value, "text": path.name})
    section = next(iter(numbers)) if len(numbers) == 1 else None
    unresolved = ["Conflicting section numbers in the document and file name."] if len(numbers) > 1 else []
    discipline = DIVISIONS.get(section[:2]) if section else None
    if discipline:
        evidence.append({"kind": "csi_division", "value": discipline, "division": section[:2],
                         "inference": True})
    return {"section": section, "section_status": "identified" if section else "conflicting" if numbers else "not_found",
            "discipline": discipline, "evidence": evidence, "unresolved": unresolved}


def inspect_source(path: Path, *, doc_id=None, project_id="", input_root=None) -> Dict[str, Any]:
    """Reacquire one source; reusable for explicit refresh without reassigning its project."""
    path = Path(path).resolve()
    record = {"id": doc_id or stable_id("document", str(path)), "source": str(path),
              "input_root": str(input_root or path.parent), "project_id": project_id,
              "source_sha256": None, "source_identity": None, "pattern_id": None,
              "section": None, "section_status": "unreadable", "discipline": None,
              "evidence": [], "quality_flags": [], "unresolved": [], "status": "PENDING"}
    pkg = None
    try:
        identity = guard.acquire(path)
        record["source_sha256"], record["source_identity"] = digest(path), identity.to_dict()
        pkg = DocxPackage.from_path(path)
        fp = fingerprint(pkg)
        record["pattern_id"] = fp["pattern_id"]
        record.update(_classification(pkg, path))
        root = pkg.xml(pkg.main_part())
        flags = []
        if next(root.iter(W + "tbl"), None) is not None:
            flags.append("tables")
        if next(root.iter(W + "txbxContent"), None) is not None:
            flags.append("textboxes")
        if next(root.iter(W + "ins"), None) is not None or next(root.iter(W + "del"), None) is not None:
            flags.append("pending_revisions")
        if not any(r.type.endswith("/styles") and r.resolved and pkg.has(r.resolved)
                   for r in pkg.rels(pkg.main_part()).values()):
            flags.append("missing_styles_part")
        hygiene = fp.get("body_hygiene") or {}
        flags += [name for name in ("has_nbsp", "has_multi_space") if hygiene.get(name)]
        record["quality_flags"] = flags
        record["evidence"].append({"kind": "quality_observations", "flags": flags,
                                   "note": "Diagnostic signals only; they do not authorize edits or determine correctness."})
        changed = guard.drifted(identity)
        if changed:
            raise ValueError(changed)
    except Exception as exc:
        from lxml import etree
        record["status"] = "UNREADABLE"
        record["quality_flags"].append("invalid_or_unreadable_package")
        record["quality_flags"].append("invalid_zip" if isinstance(exc, zipfile.BadZipFile) else
                                      "invalid_xml" if isinstance(exc, etree.XMLSyntaxError) else
                                      "unreadable_source" if isinstance(exc, OSError) else "malformed_package")
        record["unresolved"].append(f"{type(exc).__name__}: {exc}")
    finally:
        if pkg is not None:
            pkg.close()
    return record


def inventory_inputs(inputs: Sequence[str], *, recursive: bool = True, exclude: Sequence[Path] = ()) -> Dict[str, Any]:
    """Discover documents and independent project groups without creating any files.

    Duplicate and overlapping roots are deduplicated by resolved source path; the deepest
    explicit root wins for metadata lookup. Results trees and Word owner files are excluded.
    """
    excluded = [Path(p).resolve() for p in exclude]
    entries = []
    for raw in inputs:
        expanded = sorted(glob.glob(str(raw), recursive=recursive)) if glob.has_magic(str(raw)) else [str(raw)]
        if not expanded:
            raise FileNotFoundError(raw)
        for value in expanded:
            p = Path(value).resolve()
            if not p.exists():
                raise FileNotFoundError(p)
            if p.is_file() and p.suffix.lower() != ".docx":
                raise ValueError(f"An input file must be .docx: {p}")
            entries.append(p)
    roots = sorted(set(p if p.is_dir() else p.parent for p in entries), key=str)
    discovered = {}

    def include(path, root):
        path = path.resolve()
        if (path.suffix.lower() != ".docx" or path.name.startswith("~$")
                or not _inside(path, root) or any(_inside(path, p) for p in excluded)):
            return
        # Explicit file inputs also must not quietly import a prior result tree.
        if any(_result_folder(p) for p in (path.parent, *path.parents) if _inside(p, root)):
            return
        previous = discovered.get(path)
        if previous is None or len(root.parts) > len(previous.parts):
            discovered[path] = root

    for entry in entries:
        root = entry if entry.is_dir() else entry.parent
        if entry.is_file():
            include(entry, root)
            continue
        for folder, dirs, names in os.walk(entry, followlinks=False):
            folder = Path(folder)
            if any(_inside(folder.resolve(), p) for p in excluded) or _result_folder(folder):
                dirs[:] = []
                continue
            dirs[:] = sorted(d for d in dirs if d not in IGNORED_DIRS and not (folder / d).is_symlink()) if recursive else []
            for name in sorted(names):
                include(folder / name, root)

    projects, documents = {}, {}
    for path, root in sorted(discovered.items(), key=lambda item: str(item[0])):
        metadata, context, warning = _metadata(path, root)
        boundary = metadata if metadata else path.parent
        project_id = stable_id("project", str(boundary))
        label = context.get("name") or context.get("project_name") or (boundary.parent.name if metadata else path.parent.name)
        project = projects.setdefault(project_id, {
            "id": project_id, "label": str(label),
            "context": context, "context_source": str(metadata) if metadata else None,
            "context_sha256": digest(metadata) if metadata and not warning else None,
            "provisional": bool(warning), "evidence": [{"kind": "project_metadata" if metadata else "folder_boundary",
                                                          "path": str(boundary), "input_root": str(root)}],
            "unresolved": [warning] if warning else [], "document_ids": [],
        })
        doc_id = stable_id("document", str(path))
        record = inspect_source(path, doc_id=doc_id, project_id=project_id, input_root=root)
        documents[doc_id] = record
        project["document_ids"].append(doc_id)
    return {"roots": [str(p) for p in roots], "projects": projects, "documents": documents}
