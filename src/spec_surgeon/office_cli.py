"""Keyless office-profile and structure commands using reviewed engine execution."""
from __future__ import annotations

import hashlib
import json
import sys
import zipfile
from pathlib import Path


def _json(value):
    return json.loads(Path(value[1:]).read_text(encoding="utf-8") if value.startswith("@") else value)


def _code_digest():
    """An approval from a different installed editing/verification build is stale."""
    root = Path(__file__).parent
    records = [(path.relative_to(root).as_posix(), hashlib.sha256(path.read_bytes()).hexdigest())
               for path in sorted(root.rglob("*.py"))]
    if getattr(sys, "frozen", False):
        # PyInstaller embeds most Python modules in the executable's PYZ archive.
        # The loose ops/*.py files alone do not identify an installed app build.
        records.append(("frozen-executable", hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest()))
    return hashlib.sha256(json.dumps(records, separators=(",", ":")).encode()).hexdigest()


def _formatting_revision_ids(paths):
    from lxml import etree
    from .package import W, parse_xml
    tags = {W + "pPrChange", W + "rPrChange"}
    protected = set()
    for path in paths:
        with zipfile.ZipFile(path) as archive:
            for name in archive.namelist():
                if not name.endswith(".xml"):
                    continue
                try:
                    root = parse_xml(archive.read(name))
                except etree.XMLSyntaxError:
                    continue  # Inert non-Word members are still hashed byte-for-byte.
                protected.update((node.tag, node.get(W + "id")) for node in root.iter() if node.tag in tags)
    return protected


def _document_digest(path, protected):
    """Hash package member bytes, normalizing only new formatting-marker dates.

    ZIP timestamps are not document content. Existing source/reference revision
    dates remain literal. New pPrChange/rPrChange timestamps are the one expected
    difference when a tracked preview is regenerated for execution; their IDs,
    author, contents and every other package byte stay in the digest.
    """
    from lxml import etree
    from .package import W, parse_xml
    tags = {W + "pPrChange", W + "rPrChange"}
    dates = {W + "date", "{http://schemas.microsoft.com/office/word/2023/wordml/word16du}dateUtc"}
    records = []
    with zipfile.ZipFile(path) as archive:
        for member in archive.infolist():
            data = archive.read(member)
            if member.filename.endswith(".xml"):
                try:
                    root = parse_xml(data)
                except etree.XMLSyntaxError:
                    root = None
                changed = False
                if root is not None:
                    for node in root.iter():
                        if node.tag not in tags or (node.tag, node.get(W + "id")) in protected:
                            continue
                        for attribute in dates & set(node.attrib):
                            node.set(attribute, "GENERATED-FORMATTING-DATE")
                            changed = True
                if changed:
                    data = etree.tostring(root, encoding="UTF-8")
            records.append((member.filename, hashlib.sha256(data).hexdigest()))
    return hashlib.sha256(json.dumps(records, separators=(",", ":")).encode()).hexdigest()


def _render_digest(path):
    """Every rendered page's pixels; PDF creation timestamps do not affect approval."""
    if path is None:
        return None
    import fitz
    records = []
    with fitz.open(path) as document:
        for page in document:
            pixels = page.get_pixmap(matrix=fitz.Matrix(1, 1), colorspace=fitz.csRGB, alpha=False)
            records.append((pixels.width, pixels.height, hashlib.sha256(pixels.samples).hexdigest()))
    return records


def preview_digest(review, previews, renderer, do_render):
    """Bind current outcomes, document bytes, every rendered page and app build.

    The CLI regenerates a preview before running. Its contents must match what
    the user inspected, not merely the same source files and editing recipe.
    """
    records = {}
    for source, preview in sorted(previews.items()):
        inputs = [Path(source), *(Path(path) for path in preview.token_inputs.get("templates", {}))]
        protected = _formatting_revision_ids(inputs)
        samples = []
        for sample in preview.samples:
            samples.append({"source": str(sample.source), "passed": sample.passed,
                "output": _document_digest(sample.output, protected) if sample.output else None,
                "checks": sample.checks, "outcomes": sample.outcomes, "exception": sample.exception,
                "source_pages": _render_digest(sample.source_pdf), "output_pages": _render_digest(sample.output_pdf),
                "images": [hashlib.sha256(Path(image).read_bytes()).hexdigest() for image in sample.images]})
        records[str(source)] = {"token": preview.token, "renderer": preview.renderer, "samples": samples,
                                "warnings": preview.warnings,
                                "visual_review": preview.review.to_dict() if preview.review is not None else None}
    record = {"schema_version": 2, "review": review["digest"], "renderer": renderer,
              "render": do_render, "application": _code_digest(), "previews": records}
    return hashlib.sha256(json.dumps(record, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def structure_text(report):
    rows = [f"Specification structure: {report.get('source', report.get('source_sha256', ''))}",
            "Read-only analysis. Classifications are evidence, not approval to edit.",
            "Coverage: " + json.dumps(report.get("coverage", {}), ensure_ascii=False)]
    for item in report["paragraphs"]:
        rows.append(f"\n{item['paragraph'] + 1}. {item['disposition']} — {item.get('role') or 'unresolved'}")
        rows.append(str(item.get("location") or item.get("reason") or ""))
        rows.append(item["text"])
    rows.extend(str(item) for item in report.get("limitations", []))
    return "\n".join(rows)


def cmd_structure(args):
    from .batch import expand_inputs
    from .package import DocxPackage
    from .structure import analyze_structure
    reports, errors = [], []
    try:
        files = expand_inputs(args.files)
        if not files:
            raise ValueError("No DOCX files found.")
        for path in files:
            pkg = None
            try:
                pkg = DocxPackage.from_path(path)
                reports.append({**analyze_structure(pkg), "source": str(path)})
            except Exception as exc:
                errors.append({"source": str(path), "error": str(exc)})
            finally:
                if pkg is not None:
                    pkg.close()
        if args.json:
            print(json.dumps({"schema_version": 1, "report_only": True,
                              "files": reports, "errors": errors}, ensure_ascii=False, indent=2))
        else:
            print("\n\n".join(structure_text(report) for report in reports))
            for error in errors:
                print(f"{error['source']}: {error['error']}", file=sys.stderr)
        return 1 if errors else 0
    except (OSError, ValueError) as exc:
        print(f"Structure analysis refused: {exc}", file=sys.stderr)
        return 2


def cmd_profiles(args):
    from . import office_profiles as profiles
    from .batch import expand_inputs

    try:
        if args.action == "create":
            profiles.create_profile(Path(args.out), Path(args.template), args.name,
                                    _json(args.roles), policies=_json(args.policies),
                                    direct_formatting=args.direct_formatting, tracked=args.tracked)
            print(f"Office profile created: {Path(args.out).resolve()}")
            return 0
        if args.action == "import":
            profiles.import_profile(Path(args.archive), Path(args.out))
            print(f"Office profile imported: {Path(args.out).resolve()}")
            return 0
        if args.action == "export":
            profiles.export_profile(profiles.load_profile(Path(args.profile)), Path(args.out))
            print(f"Office profile exported: {Path(args.out).resolve()}")
            return 0
        if args.action == "show":
            profile = profiles.load_profile(Path(args.profile))
            print(json.dumps(profile.manifest, ensure_ascii=False, indent=2))
            return 0
        if args.action == "review":
            files = expand_inputs(args.files)
            if not files:
                raise ValueError("No DOCX files found.")
            review = profiles.prepare_review(profiles.load_profile(Path(args.profile)), files,
                                             choices=_json(args.choices))
            profiles.save_review(review, Path(args.out))
            print(profiles.review_text(review))
            print(f"\nSaved review: {Path(args.out).resolve()}")
            print(f"Review approval digest: {review['digest']}")
            print("Inspect the complete review, resolve any outstanding choices, then prepare a preview.")
            return 0
        review = profiles.load_review(Path(args.review))
        if args.action == "run" and args.approval != review["digest"]:
            raise ValueError("The exact reviewed outcome digest is required before execution.")
        root = Path(args.out).resolve()
        previews = profiles.preview_review(review, root / "preview", renderer=args.renderer,
                                            do_render=not args.no_render)
        digest = preview_digest(review, previews, args.renderer, not args.no_render)
        for source, preview in previews.items():
            print(f"\n{source}\n{preview.to_text()}")
        print(f"\nPreview approval digest: {digest}")
        if args.action == "preview":
            print("Preview only. Inspect its records and pages before approving execution.")
            return 0
        if args.preview_approval != digest:
            raise ValueError("The preview approval does not match these sources, profile and rendering settings. "
                             "Inspect the current preview before approving it.")
        results = profiles.execute_review(
            review, root / "run", approval=args.approval, previews=previews,
            preview_approvals={str(source): preview.token for source, preview in previews.items()},
            ack_review=args.ack_review, workers=args.workers,
            renderer=args.renderer, do_render=not args.no_render)
        failed = False
        for result in results:
            counts = result.counts()
            print("Results: " + ", ".join(f"{key} {value}" for key, value in counts.items()))
            failed |= any(value for key, value in counts.items() if key not in {"PASS", "SKIPPED"})
        print(f"Verified copies and reports: {root / 'run'}")
        return 1 if failed else 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"Office profile refused: {exc}", file=sys.stderr)
        return 2


def add_parsers(sub):
    p = sub.add_parser("structure", help="read-only specification hierarchy and unresolved paragraph evidence")
    p.add_argument("files", nargs="+")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_structure)
    p = sub.add_parser("profiles", help="portable office standards with reviewed per-document targets")
    actions = p.add_subparsers(dest="action", required=True)
    create = actions.add_parser("create", help="bundle a reference document and explicit role-to-style mappings")
    create.add_argument("template")
    create.add_argument("--name", required=True)
    create.add_argument("--roles", default="{}", help='JSON or @file: {"article": "Office Article"}')
    create.add_argument("--policies", default="[]", help="JSON or @file of supported explicit outcome policies")
    create.add_argument("--direct-formatting", choices=["keep", "clear", "replace_style_owned"], default="keep")
    create.add_argument("--tracked", action="store_true", help="track paragraph style changes")
    create.add_argument("--out", required=True, help="new profile directory")
    show = actions.add_parser("show", help="validate and inspect an office profile")
    show.add_argument("profile")
    export = actions.add_parser("export", help="export a validated profile as a portable ZIP")
    export.add_argument("profile")
    export.add_argument("--out", required=True)
    import_ = actions.add_parser("import", help="validate and import a portable profile ZIP")
    import_.add_argument("archive")
    import_.add_argument("--out", required=True, help="new profile directory")
    review = actions.add_parser("review", help="prepare exact per-document targets without editing documents")
    review.add_argument("profile")
    review.add_argument("files", nargs="+")
    review.add_argument("--choices", default="{}", help="JSON or @file: source path -> paragraph index -> role or exclude")
    review.add_argument("--out", required=True, help="new review JSON file")
    for action in ("preview", "run"):
        parser = actions.add_parser(action, help="preview reviewed changes" if action == "preview" else "deliver approved verified copies")
        parser.add_argument("review", help="saved office review JSON")
        parser.add_argument("--out", required=True)
        parser.add_argument("--renderer", default="auto", choices=["auto", "word", "libreoffice", "none"])
        parser.add_argument("--no-render", action="store_true")
        if action == "run":
            parser.add_argument("--approval", required=True, help="exact digest printed by profiles review")
            parser.add_argument("--preview-approval", required=True, help="exact digest printed by profiles preview")
            parser.add_argument("--ack-review", action="store_true", help="acknowledge inspected visual findings")
            parser.add_argument("--workers", type=int, default=0)
    p.set_defaults(fn=cmd_profiles)
