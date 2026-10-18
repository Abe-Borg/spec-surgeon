"""Portable office standards and explicitly reviewed, source-bound applications.

A profile is data, never executable code. It stores original reference documents
and desired roles/policies; each application resolves a fresh source inventory,
builds ordinary approved-effect contracts and executes the existing chain engine.
The classifier proposes targets and is never called by the independent verifier.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

SCHEMA_VERSION = 1
MANIFEST = "manifest.json"
MAX_BUNDLE_BYTES = 256 * 1024 * 1024
MAX_BUNDLE_MEMBERS = 64
POLICIES = {"page_setup", "header_footer_template", "table_format", "table_style",
            "proofing_language", "numbering"}
TEMPLATED_POLICIES = {"header_footer_template", "table_style", "numbering"}
DIRECT_FORMATTING = {"keep", "clear", "replace_style_owned"}


class ProfileError(ValueError):
    """An invalid, stale, unsupported or unapproved office profile/application."""


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _seal(review):
    review["digest"] = _digest({key: value for key, value in review.items() if key != "digest"})
    return review


def _json_read(path):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ProfileError(f"Duplicate JSON key: {key}")
            result[key] = value
        return result
    return json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=pairs,
                      parse_constant=lambda value: (_ for _ in ()).throw(ProfileError("Non-finite JSON value")))


def _relative(value):
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise ProfileError("Bundle members must use plain relative POSIX paths")
    p = PurePosixPath(value)
    if p.is_absolute() or any(part in {".", ".."} for part in value.split("/")) or str(p) != value:
        raise ProfileError(f"Unsafe bundle member: {value!r}")
    return p


def _within(path, root):
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _bundle_file(root, relative):
    path = root / str(_relative(relative))
    if not _within(path, root) or any(p.is_symlink() for p in [path, *path.parents] if _within(p, root)):
        raise ProfileError("Profile assets must be regular files inside their bundle, without symlinks")
    if not path.is_file():
        raise ProfileError(f"Missing profile asset: {relative}")
    return path


def _write_json(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def _publish_directory(temp, destination):
    if destination.exists() or destination.is_symlink():
        raise ProfileError(f"Destination already exists: {destination}; choose a new location")
    os.rename(temp, destination)


@dataclass(frozen=True)
class OfficeProfile:
    root: Path
    manifest: dict

    @property
    def name(self):
        return self.manifest["name"]

    @property
    def digest(self):
        return _digest(self.manifest)

    @property
    def template(self):
        return self.asset_path(self.manifest["template_asset"])

    def asset_path(self, asset):
        return _bundle_file(self.root, self.manifest["assets"][asset]["path"])

    def to_dict(self):
        return {"path": str(self.root), "digest": self.digest, **copy.deepcopy(self.manifest)}


def _validate_manifest(manifest):
    from .structure import ROLES
    allowed = {"schema_version", "name", "description", "template_asset", "assets", "role_style_map",
               "policies", "direct_formatting", "tracked"}
    if (not isinstance(manifest, dict) or set(manifest) != allowed
            or type(manifest["schema_version"]) is not int or manifest["schema_version"] != SCHEMA_VERSION):
        raise ProfileError("Unsupported office profile manifest schema")
    if not isinstance(manifest["name"], str) or not manifest["name"].strip() or not isinstance(manifest["description"], str):
        raise ProfileError("A profile needs a nonempty name and text description")
    assets = manifest["assets"]
    if not isinstance(assets, dict) or not assets or len(assets) >= MAX_BUNDLE_MEMBERS:
        raise ProfileError("A profile needs a bounded collection of document assets")
    paths = set()
    for key, asset in assets.items():
        if not isinstance(key, str) or not re.fullmatch(r"[a-zA-Z0-9_-]+", key):
            raise ProfileError("Asset names must contain letters, numbers, underscores or hyphens")
        if not isinstance(asset, dict) or set(asset) != {"path", "sha256", "size", "source_name"}:
            raise ProfileError("Invalid profile asset record")
        relative = _relative(asset["path"])
        if relative.parts[0] != "assets" or relative.suffix.lower() not in {".docx", ".dotx"}:
            raise ProfileError("Only original .docx/.dotx reference documents may be bundled under assets/")
        if str(relative).casefold() in paths:
            raise ProfileError("Duplicate or case-colliding profile asset paths")
        paths.add(str(relative).casefold())
        if (not isinstance(asset["sha256"], str) or not re.fullmatch("[0-9a-f]{64}", asset["sha256"])
                or type(asset["size"]) is not int or not 0 < asset["size"] <= MAX_BUNDLE_BYTES
                or not isinstance(asset["source_name"], str)):
            raise ProfileError("Invalid asset size, SHA256 or source name")
    if manifest["template_asset"] not in assets:
        raise ProfileError("The template asset is missing")
    mapping = manifest["role_style_map"]
    if not isinstance(mapping, dict) or any(role not in ROLES or not isinstance(style, str) or not style.strip()
                                           for role, style in mapping.items()):
        raise ProfileError("Role mappings must name supported structure roles and literal destination styles")
    if (not isinstance(manifest["direct_formatting"], str) or manifest["direct_formatting"] not in DIRECT_FORMATTING
            or type(manifest["tracked"]) is not bool):
        raise ProfileError("Invalid direct-formatting policy or tracked flag")
    if not isinstance(manifest["policies"], list):
        raise ProfileError("Policies must be a list of declarative approved-effect objects")
    for policy in manifest["policies"]:
        if (not isinstance(policy, dict) or not isinstance(policy.get("type"), str)
                or policy["type"] not in POLICIES):
            raise ProfileError(f"Supported profile policies: {sorted(POLICIES)}")
        if set(policy) & {"id", "expected_count", "template", "template_sha256"}:
            raise ProfileError("Policy IDs, source counts and template pins are established by each fresh review")
        if "template_asset" in policy and (policy["type"] not in TEMPLATED_POLICIES
                or not isinstance(policy["template_asset"], str) or policy["template_asset"] not in assets):
            raise ProfileError("Policy template_asset must name a bundled reference document")
        if "params" in policy and not isinstance(policy["params"], dict):
            raise ProfileError("Policy params must be an object")
        if "template" in policy.get("params", {}):
            raise ProfileError("Policies cannot contain external template paths")
    if not mapping and not manifest["policies"]:
        raise ProfileError("A profile needs at least one role mapping or explicit policy")
    if sum(asset["size"] for asset in assets.values()) > MAX_BUNDLE_BYTES:
        raise ProfileError("Profile assets exceed the bundle size limit")


def load_profile(path):
    """Validate all bytes, declared assets and settings, including on every reuse."""
    root = Path(path).resolve()
    if not root.is_dir():
        raise ProfileError("An office profile is a directory containing manifest.json and original assets")
    manifest = _json_read(_bundle_file(root, MANIFEST))
    _validate_manifest(manifest)
    expected = {MANIFEST, *(asset["path"] for asset in manifest["assets"].values())}
    actual = {str(p.relative_to(root).as_posix()) for p in root.rglob("*") if p.is_file() or p.is_symlink()}
    if actual != expected:
        raise ProfileError("Profile contains missing or undeclared files; executable additions are never loaded")
    from .package import DocxPackage
    for asset in manifest["assets"].values():
        data = _bundle_file(root, asset["path"]).read_bytes()
        if len(data) != asset["size"] or _sha(data) != asset["sha256"]:
            raise ProfileError(f"Profile asset changed: {asset['path']}")
        pkg = DocxPackage(data, name=asset["path"])
        pkg.close()
    profile = OfficeProfile(root, manifest)
    _validate_profile_settings(profile)
    return profile


def _validate_profile_settings(profile):
    """Reject source-independent method gaps before publishing a reusable profile.

The complete grammar is checked using disjoint representative paragraph ranges.
Real targets/counts and source-specific feasibility are still established anew by
prepare_review. This never grants authority to the representative selections.
"""
    from .goal_contracts import validate_contract
    from .ops.base import JobException
    from .ops.selectors import StyleSheet
    from .ops.style_import import is_default, resolve_style, style_links, style_type
    from .package import DocxPackage, W

    mapping = profile.manifest["role_style_map"]
    policies = profile.manifest["policies"]
    kinds = [policy["type"] for policy in policies]
    if len(kinds) != len(set(kinds)):
        raise ProfileError("Use at most one policy of each type per office profile; combine its settings in one "
                           "policy or create separate profiles for distinct applications")
    if "numbering" in kinds and (mapping or set(kinds) - {"numbering", "page_setup"}):
        raise ProfileError("A numbering policy needs a separate office profile with no role mappings or other "
                           "formatting policies; page setup may accompany it. Independent numbering verification "
                           "compares the complete original numbering graph")
    effects = []
    template = DocxPackage.from_path(profile.template)
    try:
        sheet = StyleSheet(template)
        for index, (role, reference) in enumerate(mapping.items()):
            sid = resolve_style(sheet.styles, reference, "office profile template")
            if sid is None or style_type(sheet.styles[sid]) != "paragraph":
                raise ProfileError(f"Role {role!r} needs a paragraph style defined in the bundled template: {reference!r}")
            if is_default(sheet.styles[sid]):
                raise ProfileError(f"Role {role!r} selects the default style {reference!r}, which cannot be replaced "
                                   "by a template import; choose a named nondefault paragraph style")
            pending, visited = [sid], set()
            while pending:
                dependency = pending.pop()
                if dependency in visited or dependency not in sheet.styles:
                    continue
                visited.add(dependency)
                definition = sheet.styles[dependency]
                num = definition.find(W + "pPr/" + W + "numPr/" + W + "numId")
                if num is not None and num.get(W + "val") != "0":
                    raise ProfileError(f"Role {role!r} style {reference!r} depends on numbered style {dependency!r}. "
                                       "Repeated template imports cannot yet prove one original-to-final numbering "
                                       "graph. Use nonnumbered role styles and a separate numbering-only profile")
                pending.extend(style_links(definition))
            effects.append({"id": f"role-{index}", "type": "paragraph_style",
                "select": {"scope": ["document"], "paragraphs": {"index_range": [index, index + 1]}},
                "params": {"style": reference, "existing": "replace",
                           "direct_formatting": profile.manifest["direct_formatting"],
                           "tracked": profile.manifest["tracked"]}, "expected_count": 1,
                "template": str(profile.template),
                "template_sha256": profile.manifest["assets"][profile.manifest["template_asset"]]["sha256"]})
        effects.extend(_policy_effect(profile, policy, i) for i, policy in enumerate(policies))
        try:
            validate_contract({"kind": "document_effects", "effects": effects})
        except ValueError as exc:
            raise ProfileError("These office profile settings require separate reviewed applications: " + str(exc)) from exc
        for policy in policies:
            if policy["type"] not in {"table_style", "numbering"}:
                continue
            asset = policy.get("template_asset", profile.manifest["template_asset"])
            policy_template = DocxPackage.from_path(profile.asset_path(asset))
            try:
                policy_styles = StyleSheet(policy_template)
                if policy["type"] == "table_style":
                    reference = policy["params"]["style"]
                    sid = resolve_style(policy_styles.styles, reference, "office profile table template")
                    if sid is None or style_type(policy_styles.styles[sid]) != "table":
                        raise ProfileError(f"Table policy needs a table style defined in its bundled template: {reference!r}")
                    if policy["params"].get("existing") == "replace" and is_default(policy_styles.styles[sid]):
                        raise ProfileError("A default table style cannot be replaced by a template import")
                else:
                    from .ops.lists import ListDefs, list_is_clean, resolve_scheme
                    definitions = ListDefs.load(policy_template)
                    scheme = resolve_scheme(policy["params"]["levels"], policy_styles, definitions,
                                            "office profile numbering template", "bad-params")
                    reason = list_is_clean(definitions, scheme.num_id, [])
                    if reason:
                        raise ProfileError("Unsupported office profile numbering template: " + reason)
            finally:
                policy_template.close()
    except JobException as exc:
        raise ProfileError("Invalid office profile template settings: " + str(exc)) from exc
    finally:
        template.close()


def create_profile(destination, template, name, role_map, *, policies=(), direct_formatting="keep",
                   tracked=False, description="", assets=None):
    """Create a new bundle atomically; never replace an existing profile."""
    destination = Path(destination).absolute()
    if destination.exists() or destination.is_symlink():
        raise ProfileError(f"Destination already exists: {destination}")
    references = {"template": Path(template).resolve(), **{key: Path(value).resolve() for key, value in (assets or {}).items()}}
    if references["template"] != Path(template).resolve():
        raise ProfileError("The reserved template asset cannot be replaced")
    from .batch import check_inputs_outside
    check_inputs_outside(list(references.values()), destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix=".office-profile-", dir=destination.parent))
    try:
        (temp / "assets").mkdir()
        records = {}
        for key, source in references.items():
            if not isinstance(key, str) or not re.fullmatch(r"[a-zA-Z0-9_-]+", key):
                raise ProfileError("Invalid asset name")
            if source.suffix.lower() not in {".docx", ".dotx"}:
                raise ProfileError("Reference assets must be .docx/.dotx files")
            data = source.read_bytes()
            relative = f"assets/{key}{source.suffix.lower()}"
            records[key] = {"path": relative, "sha256": _sha(data), "size": len(data), "source_name": source.name}
            (temp / relative).write_bytes(data)
        manifest = {"schema_version": SCHEMA_VERSION, "name": name, "description": description,
                    "template_asset": "template", "assets": records, "role_style_map": copy.deepcopy(role_map),
                    "policies": copy.deepcopy(list(policies)), "direct_formatting": direct_formatting, "tracked": tracked}
        _write_json(temp / MANIFEST, manifest)
        load_profile(temp)
        _publish_directory(temp, destination)
    finally:
        if temp.exists():
            shutil.rmtree(temp)
    return load_profile(destination)


def export_profile(profile, destination):
    """Publish a portable ZIP atomically, without replacing any existing file."""
    profile = load_profile(profile.root if isinstance(profile, OfficeProfile) else profile)
    destination = Path(destination).absolute()
    if destination.exists() or destination.is_symlink() or _within(destination, profile.root):
        raise ProfileError("Export needs a new path outside the profile bundle")
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, filename = tempfile.mkstemp(prefix=".office-export-", dir=destination.parent)
    os.close(fd)
    temp = Path(filename)
    try:
        with zipfile.ZipFile(temp, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(MANIFEST, json.dumps(profile.manifest, ensure_ascii=False, indent=2))
            for asset in profile.manifest["assets"].values():
                data = _bundle_file(profile.root, asset["path"]).read_bytes()
                if len(data) != asset["size"] or _sha(data) != asset["sha256"]:
                    raise ProfileError("A profile asset changed while exporting")
                archive.writestr(asset["path"], data)
        if load_profile(profile.root).digest != profile.digest:
            raise ProfileError("The profile changed while exporting")
        # A hard link publishes without overwriting even if another process races.
        os.link(temp, destination)
    finally:
        temp.unlink(missing_ok=True)
    return destination


def import_profile(archive, destination):
    """Validate a bounded, relative-path ZIP in staging before publishing it."""
    archive, destination = Path(archive).resolve(), Path(destination).absolute()
    if destination.exists() or destination.is_symlink() or _within(archive, destination):
        raise ProfileError("Import needs a new directory outside its source archive")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix=".office-import-", dir=destination.parent))
    try:
        with zipfile.ZipFile(archive) as source:
            members = source.infolist()
            if len(members) > MAX_BUNDLE_MEMBERS or sum(m.file_size for m in members) > MAX_BUNDLE_BYTES:
                raise ProfileError("Profile archive exceeds member or expanded-size limits")
            names = set()
            for member in members:
                relative = str(_relative(member.filename))
                if relative.casefold() in names or member.is_dir() or stat.S_ISLNK(member.external_attr >> 16):
                    raise ProfileError("Duplicate, directory or symlink archive member")
                names.add(relative.casefold())
                target = temp / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.open(member) as stream:
                    data = stream.read(min(MAX_BUNDLE_BYTES, member.file_size) + 1)
                if len(data) != member.file_size:
                    raise ProfileError("Archive member expanded beyond its declared size")
                target.write_bytes(data)
        load_profile(temp)
        _publish_directory(temp, destination)
    except (zipfile.BadZipFile, RuntimeError) as exc:
        raise ProfileError("The profile archive cannot be read: " + str(exc)) from exc
    finally:
        if temp.exists():
            shutil.rmtree(temp)
    return load_profile(destination)


def _policy_effect(profile, policy, index):
    effect = copy.deepcopy(policy)
    kind = effect["type"]
    asset = effect.pop("template_asset", profile.manifest["template_asset"])
    effect["id"] = f"policy-{index + 1}"
    if kind in TEMPLATED_POLICIES:
        effect["template"] = str(profile.asset_path(asset))
        effect["template_sha256"] = profile.manifest["assets"][asset]["sha256"]
    if kind not in {"page_setup", "header_footer_template"}:
        effect["expected_count"] = "all"
    return effect


def _identity(path):
    before = path.stat()
    data = path.read_bytes()
    after = path.stat()
    if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns or len(data) != after.st_size:
        raise ProfileError(f"Source changed while being read: {path}")
    return data, {"size": after.st_size, "mtime_ns": after.st_mtime_ns, "sha256": _sha(data)}


def prepare_review(profile, files, *, choices=None):
    """Propose complete paragraph dispositions and exact per-file effects.

    ``choices`` maps absolute source paths to {paragraph_index: role | 'exclude'}.
    Unresolved paragraphs require a decision. Excluded regions cannot be promoted
    into supported targets. No decision or digest is itself permission to run.
    """
    from .structure import ROLES, analyze_structure
    from .package import DocxPackage
    from .goal_contracts import compile_goal_candidate, resolve_goal
    profile = load_profile(profile.root if isinstance(profile, OfficeProfile) else profile)
    paths = [Path(path).resolve() for path in files]
    if not paths or len(set(paths)) != len(paths):
        raise ProfileError("A review requires distinct original documents")
    if any(_within(path, profile.root) for path in paths):
        raise ProfileError("Profile reference assets cannot also be target documents")
    choices = {str(Path(source).resolve()): value for source, value in (choices or {}).items()}
    if set(choices) - {str(path) for path in paths}:
        raise ProfileError("Manual choices name documents outside this review")
    review = {"schema_version": 1, "review_id": uuid.uuid4().hex, "profile": str(profile.root),
              "profile_digest": profile.digest, "profile_name": profile.name, "status": "PROPOSED",
              "documents": [], "mode": "copies", "limitations": [
                  "Structure classification proposes targets; every original paragraph disposition is included for review.",
                  "Unsupported effect combinations pause for separate reviewed applications; they never bypass independent verification."]}
    for path in paths:
        data, identity = _identity(path)
        pkg = DocxPackage(data, name=str(path))
        try:
            analysis = analyze_structure(pkg)
        finally:
            pkg.close()
        raw_choices = choices.get(str(path), {})
        if not isinstance(raw_choices, dict):
            raise ProfileError("Each document's manual choices must be a paragraph-to-role object")
        selected = {}
        for index, role in raw_choices.items():
            if isinstance(index, bool) or not re.fullmatch(r"0|[1-9][0-9]*", str(index)) or (role not in ROLES and role != "exclude"):
                raise ProfileError("Choices require zero-based paragraph indices and a supported role or 'exclude'")
            selected[int(index)] = role
        if set(selected) - {row["paragraph"] for row in analysis["paragraphs"]}:
            raise ProfileError("A manual choice names a paragraph outside the original analysis")
        row = {"source": str(path), "identity": identity, "analysis": analysis, "targets": [], "unresolved": [],
               "choices": {str(k): v for k, v in selected.items()}, "contract": None, "binding": None, "chain": None,
               "status": "PROPOSED", "reason": ""}
        effects = []
        for paragraph in analysis["paragraphs"]:
            index = paragraph["paragraph"]
            choice = selected.get(index)
            if paragraph["disposition"] == "excluded":
                if choice is not None and choice != "exclude":
                    raise ProfileError(f"Paragraph {index + 1} is an unsupported region and cannot be selected")
                continue
            if choice == "exclude":
                continue
            if paragraph["disposition"] == "unresolved" and choice is None and profile.manifest["role_style_map"]:
                row["unresolved"].append(copy.deepcopy(paragraph))
                continue
            role = choice or paragraph.get("role")
            style = profile.manifest["role_style_map"].get(role)
            if style is None:
                continue
            target = {**copy.deepcopy(paragraph), "reviewed_role": role, "style": style,
                      "decision": "manual" if choice is not None else "proposed"}
            row["targets"].append(target)
            effects.append({"id": f"paragraph-{index}", "type": "paragraph_style",
                "select": {"scope": ["document"], "paragraphs": {"index_range": [index, index + 1]}},
                "params": {"style": style, "existing": "replace", "direct_formatting": profile.manifest["direct_formatting"],
                           "tracked": profile.manifest["tracked"]}, "expected_count": 1,
                "template": str(profile.template),
                "template_sha256": profile.manifest["assets"][profile.manifest["template_asset"]]["sha256"]})
        effects.extend(_policy_effect(profile, policy, i) for i, policy in enumerate(profile.manifest["policies"]))
        contract = {"kind": "document_effects", "request": f"Apply reviewed office profile {profile.name}", "effects": effects}
        row["contract"] = contract
        if row["unresolved"]:
            row.update(status="NEEDS_INPUT", reason="Choose a role or explicitly exclude every unresolved paragraph before reviewing the targets.")
        elif not effects:
            row.update(status="NEEDS_INPUT", reason="No approved roles or policies apply to this original document.")
        else:
            try:
                resolution = resolve_goal(path, contract)
                if resolution["binding"] is None:
                    raise ProfileError(resolution["reason"])
                row["binding"] = resolution["binding"]
                compiled = compile_goal_candidate(path, contract, row["binding"])
                row["chain"] = compiled.get("chain") or {"name": profile.name, "stages": [{"name": profile.name, "spec": compiled["spec"]}]}
            except (ValueError, RuntimeError) as exc:
                row.update(status="NEEDS_INPUT", reason=str(exc))
        _, current = _identity(path)
        if current != identity:
            raise ProfileError(f"Source changed during review preparation: {path}")
        if row["status"] != "PROPOSED":
            review["status"] = "NEEDS_INPUT"
        review["documents"].append(row)
    if load_profile(profile.root).digest != profile.digest:
        raise ProfileError("Office profile changed during review preparation")
    return _seal(review)


def validate_review(review, *, approval=None, files=None):
    """Reject modified review, profile, source, selection or target binding."""
    from .goals import validate_binding
    if (not isinstance(review, dict) or review.get("schema_version") != 1
            or review.get("digest") != _digest({key: value for key, value in review.items() if key != "digest"})):
        raise ProfileError("The office profile review changed; prepare it again")
    if review.get("status") != "PROPOSED" or review.get("mode") != "copies" or not review.get("documents"):
        raise ProfileError("The office profile review still needs input")
    if approval is not None and approval != review["digest"]:
        raise ProfileError("Approve the exact office profile outcomes and every document's targets")
    profile = load_profile(review["profile"])
    if profile.digest != review["profile_digest"]:
        raise ProfileError("The office profile changed; prepare and approve a fresh review")
    sources = [row["source"] for row in review["documents"]]
    if len(sources) != len(set(sources)):
        raise ProfileError("The review contains duplicate sources")
    if files is not None and sorted(str(Path(path).resolve()) for path in files) != sorted(sources):
        raise ProfileError("The original document scope changed since review")
    for row in review["documents"]:
        if row["status"] != "PROPOSED" or row["unresolved"] or not row["chain"]:
            raise ProfileError("Every original document needs a complete approved target selection")
        _, identity = _identity(Path(row["source"]))
        if identity != row["identity"]:
            raise ProfileError(f"An original document changed since review: {row['source']}")
        validate_binding(Path(row["source"]), row["contract"], row["binding"])
    return profile


def _output_check(review, folder):
    from .batch import check_inputs_outside
    profile = load_profile(review["profile"])
    folder = Path(folder).resolve()
    if _within(folder, profile.root):
        raise ProfileError("Review and run output must be outside the office profile bundle")
    check_inputs_outside([*(Path(row["source"]) for row in review["documents"]),
                          *(profile.asset_path(key) for key in profile.manifest["assets"])], folder)


def preview_review(review, out_dir, *, renderer="auto", do_render=True):
    """Preview every file and every chain stage; no representative-file omission."""
    from .preview import make_chain_preview, write_preview_files
    from .goals import verify_goal
    validate_review(review)
    _output_check(review, out_dir)
    result = {}
    for index, row in enumerate(review["documents"], 1):
        pv = make_chain_preview(row["chain"], [row["source"]],
            Path(out_dir) / f"file-{index:04d}", renderer=renderer, do_render=do_render)
        pv._office_render_config = (renderer, do_render)
        pv.warnings.append("Optional model visual review was not run for this office profile preview; "
                           "inspect the rendered pages yourself.")
        for sample in pv.samples:
            if sample.passed and sample.output is not None:
                verdict = verify_goal(Path(row["source"]).read_bytes(), sample.output.read_bytes(),
                                      row["contract"], row["binding"])
                passed = bool(verdict.get("passed"))
                sample.checks["OFFICE_OUTCOMES"] = "PASS" if passed else "FAIL"
                sample.outcomes.append({"index": len(sample.outcomes) + 1, "kind": "approved_goal",
                    "status": "PASS" if passed else "FAIL", "summary": "approved original-to-final effects independently verified"
                    if passed else "; ".join(verdict.get("errors", [])), "details": verdict})
                sample.passed = passed
        write_preview_files(pv)
        result[row["source"]] = pv
    validate_review(review)
    return result


def execute_review(review, out_dir, *, approval, previews, preview_approvals, ack_review=False,
                   workers=0, renderer="auto", do_render=True, progress=None):
    """Execute once, with fresh outcome and preview approval, through run_chain.

    Preview objects are current-session evidence. They are consumed even if a run
    fails, so a persisted review or previous acknowledgement cannot authorize a
    second run. A fresh preview and explicit approval are needed for any retry.
    """
    from .chain import chain_key, run_chain
    from .preview import changed_since_preview
    if not approval:
        raise ProfileError("Explicit approval of the complete office profile review is required")
    validate_review(review, approval=approval)
    _output_check(review, out_dir)
    if Path(out_dir).exists() or Path(out_dir).is_symlink():
        raise ProfileError("Choose a new run directory; each office profile run keeps its own results")
    sources = {row["source"] for row in review["documents"]}
    if set(previews) != sources or set(preview_approvals) != sources:
        raise ProfileError("Every reviewed source requires a current, explicitly approved preview")
    for row in review["documents"]:
        pv = previews[row["source"]]
        if getattr(pv, "_office_consumed", False) or preview_approvals[row["source"]] != pv.token:
            raise ProfileError("Preview approval is missing or was already consumed; preview again")
        if getattr(pv, "_office_render_config", None) != (renderer, do_render):
            raise ProfileError("Renderer settings changed since the office profile preview")
        if pv.chain != row["chain"] or [str(f.source.resolve()) for f in pv.batch_plan.files] != [row["source"]]:
            raise ProfileError("Preview does not match the reviewed chain and original source")
        if (not pv.samples or pv.batch_plan.exceptions or any(not sample.passed or sample.exception for sample in pv.samples)
                or changed_since_preview(chain_key(row["chain"]), [Path(row["source"])], pv.token, pv.token_inputs)):
            raise ProfileError("A preview failed or changed; prepare and inspect a fresh preview")
        if pv.review is not None and pv.review.blocking() and not ack_review:
            raise ProfileError("Inspect and explicitly acknowledge the current blocking visual findings")
        if _within(Path(pv.out_dir), Path(out_dir)) or _within(Path(out_dir), Path(pv.out_dir)):
            raise ProfileError("Run and preview output directories must be disjoint")
    for pv in previews.values():
        pv._office_consumed = True
    results = []
    for index, row in enumerate(review["documents"], 1):
        validate_review(review, approval=approval)
        results.append(run_chain([row["source"]], row["chain"], Path(out_dir) / f"file-{index:04d}",
            workers=workers, renderer=renderer, do_render=do_render, in_place=False, progress=progress,
            goal_contract=row["contract"], goal_bindings={row["source"]: row["binding"]},
            approved_inputs=previews[row["source"]].token_inputs))
    save_review(review, Path(out_dir) / "office-review.json")
    return results


def save_review(review, path):
    """Persist proposal/evidence only, never an execution approval."""
    path = Path(path).absolute()
    profile = load_profile(review["profile"])
    if _within(path, profile.root) or path.is_symlink():
        raise ProfileError("A review file must be outside the profile and cannot be a symlink")
    if any(path.resolve() == Path(row["source"]).resolve() or
           (path.exists() and os.path.samefile(path, row["source"])) for row in review["documents"]):
        raise ProfileError("A review file cannot replace an original document")
    if path.suffix.lower() != ".json":
        raise ProfileError("An office review must be saved as a .json file, never over a document or other asset")
    if path.exists():
        try:
            previous = load_review(path)
            if previous.get("schema_version") != 1 or not {"review_id", "profile_digest", "documents"} <= set(previous):
                raise ValueError("not an office review")
        except (OSError, ValueError, TypeError) as exc:
            raise ProfileError("An existing destination can be replaced only when it is a valid office review JSON") from exc
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".office-review-", dir=path.parent)
    os.close(fd)
    try:
        _write_json(temp, review)
        os.replace(temp, path)
    finally:
        Path(temp).unlink(missing_ok=True)


def load_review(path):
    review = _json_read(path)
    if not isinstance(review, dict) or review.get("digest") != _digest({k: v for k, v in review.items() if k != "digest"}):
        raise ProfileError("The stored review changed; prepare a fresh review")
    return review


def review_text(review):
    """Full review text, including every source paragraph's proposed disposition."""
    lines = [f"OFFICE PROFILE — {review['profile_name']}", "Status: " + review["status"],
             "Review every original paragraph's disposition and requested effect before approving.",
             "Paragraph numbers below start at 1; choice-file keys use zero-based paragraph indices.",
             "Delivery: verified copies. References and originals remain inputs.", "Review digest: " + review["digest"]]
    for row in review["documents"]:
        lines.extend(["", "Document: " + row["source"], "Status: " + row["status"] + (" — " + row["reason"] if row["reason"] else "")])
        targets = {target["paragraph"]: target for target in row["targets"]}
        for paragraph in row["analysis"]["paragraphs"]:
            index = paragraph["paragraph"]
            target = targets.get(index)
            decision = (f"{target['reviewed_role']} → {target['style']} ({target['decision']})" if target else
                        "explicitly excluded" if row["choices"].get(str(index)) == "exclude" else
                        paragraph["disposition"] + ("; no mapped role" if paragraph["disposition"] == "classified" else ""))
            lines.append(f"  {paragraph['part']} paragraph {index + 1}: {decision}: {paragraph['text']!r}")
            if paragraph.get("reason"):
                lines.append("    " + paragraph["reason"])
        lines.extend(["Requested effects:", json.dumps(row["contract"], ensure_ascii=False, indent=2)])
    lines.extend("Limit: " + value for value in review["limitations"])
    return "\n".join(lines)
