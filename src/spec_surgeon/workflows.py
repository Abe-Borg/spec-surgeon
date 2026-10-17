"""Built-in, keyless workflows composed from the ordinary verified operation library.

The catalog is Python package data in the broad sense: it ships with every wheel and
needs no checkout, personal library, or model. Building a workflow only produces a
validated chain. The existing chain preview, approval digest, verification and
delivery guard remain responsible for running it.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from .chain import chain_from_specs


class WorkflowError(ValueError):
    """A workflow name or one of its explicit inputs is unusable."""


_TEMPLATE = {"name": "template", "label": "Example document", "type": "string", "format": "document",
             "description": "The .docx or .dotx containing the styles to import. This file is excluded from the batch."}
_EXISTING = {"name": "existing", "label": "Styles already in the document", "type": "string",
             "choices": ["keep", "replace"], "default": "keep",
             "description": "keep uses the document's existing definitions; replace takes the example's definitions."}
_SCOPE = {"name": "scope", "label": "Document regions", "type": "array", "default": ["document"],
          "description": 'JSON list: "document", "headers", "footers", "footnotes", "endnotes", "comments".'}
_DIRECT = {"name": "direct_formatting", "label": "Direct formatting", "type": "string",
           "choices": ["keep", "clear"], "default": "keep",
           "description": "keep preserves direct formatting; clear removes the operation's supported overrides so the style shows."}

_WORKFLOWS = (
    {"id": "architect-styles", "name": "Standardize architect styles",
     "description": "Map named styles to your example document's styles. Optionally remove unused styles in a separate verified stage.",
     "inputs": [_TEMPLATE,
                {"name": "style_map", "label": "Style mapping", "type": "array",
                 "description": "List each source style and its destination, using the IDs or names shown by Profile.",
                 "example": [{"from": "Architect Heading", "to": "Office Heading"}]},
                _EXISTING, _SCOPE,
                {"name": "allow_missing", "label": "Skip unused or absent source styles", "type": "boolean", "default": False,
                 "description": "Off: set aside a document if a requested source style is absent or unused."},
                {"name": "unused_styles", "label": "Unused styles after mapping", "type": "string",
                 "choices": ["keep", "custom", "all"], "default": "keep",
                 "description": "keep removes none; custom removes only unused custom styles; all also considers Word's built-in styles."},
                {"name": "keep_styles", "label": "Styles to retain", "type": "array", "default": [],
                 "description": "Additional style IDs or names to keep when removing unused styles."}]},
    {"id": "match-numbering", "name": "Match an example numbering scheme",
     "description": "Convert selected automatic numbering to an explicit sequence of example styles. Preview reports displayed numbers and references needing review.",
     "inputs": [_TEMPLATE,
                {"name": "levels", "label": "Example styles, level 0 first", "type": "array",
                 "description": "The example's numbered paragraph style IDs or names, in outline order.",
                 "example": ["PRT", "ART", "PR1", "PR2", "PR3"]},
                {"name": "source", "label": "Numbering to convert", "type": ["string", "object"],
                 "description": 'JSON: "main" for the list numbering the most paragraphs, or {"styles": [["Part"], ["Article"]]} for explicit source levels.',
                 "example": "main"},
                {"name": "level_map", "label": "Source to example level mapping", "type": "object", "default": {},
                 "description": 'JSON object when levels differ, e.g. {"0": 0, "1": 2}; empty keeps the source levels.'},
                _EXISTING, _DIRECT]},
    {"id": "format-tables", "name": "Standardize schedule tables",
     "description": "Apply a table style, then set the selected tables' explicit layout properties in a second verified stage.",
     "inputs": [{**_TEMPLATE, "default": "", "description": "Optional .docx/.dotx containing the table style; leave blank to use a style already in each source."},
                {"name": "style", "label": "Table style", "type": "string",
                 "description": "The table style's exact ID or name, as shown by Profile.", "example": "Table Grid"},
                {"name": "tables", "label": "Tables to format", "type": ["string", "object"],
                 "description": 'JSON: "all", or a selector such as {"first_row_regex": "^Tag"} or {"index_range": [0, 1]}. Indices start at 0; nested tables count.',
                 "example": {"first_row_regex": "^Tag"}},
                {"name": "properties", "label": "Table layout", "type": "object",
                 "description": "Only the properties you enter change. Width is a percent or twips; margins are twips (1440 = 1 inch).",
                 "example": {"width": {"pct": 100}, "repeat_header_rows": 1, "cant_split": True}},
                _EXISTING, _DIRECT, _SCOPE]},
    {"id": "proofing-language", "name": "Set the proofing language",
     "description": "Set the language Word uses to check spelling across document defaults, styles and text. Document wording stays the same.",
     "inputs": [{"name": "language", "label": "Proofing language", "type": "string",
                 "description": "Language tag, such as en-US or en-GB. This does not translate or change text.", "example": "en-US"},
                {"name": "replace_only", "label": "Only replace these languages", "type": "array", "default": [],
                 "description": 'JSON list, e.g. ["en-GB"]. Empty changes the Latin-text language everywhere.'}]},
)


def workflow_catalog() -> list[dict[str, Any]]:
    """Return isolated metadata for discovery, CLI help and the desktop form."""
    return copy.deepcopy(list(_WORKFLOWS))


def get_workflow(name: str) -> dict[str, Any]:
    for workflow in _WORKFLOWS:
        if workflow["id"] == name:
            return copy.deepcopy(workflow)
    raise WorkflowError(f"unknown workflow {name!r}; choose one of: " + ", ".join(w["id"] for w in _WORKFLOWS))


def load_workflow_inputs(source: str) -> tuple[dict[str, Any], Path]:
    """Read JSON or @file; relative example paths are relative to that file."""
    base = Path.cwd()
    try:
        if source.startswith("@"):
            path = Path(source[1:]).expanduser().resolve()
            base = path.parent
            source = path.read_text(encoding="utf-8")
        values = json.loads(source)
    except (OSError, ValueError) as e:
        raise WorkflowError(f"cannot read workflow inputs: {e}") from e
    if not isinstance(values, dict):
        raise WorkflowError("workflow inputs must be a JSON object")
    return values, base


def _inputs(workflow: dict, values: dict, base_dir: Path | None) -> dict:
    if not isinstance(values, dict):
        raise WorkflowError("workflow inputs must be a JSON object")
    fields = {f["name"]: f for f in workflow["inputs"]}
    unknown = set(values) - set(fields)
    if unknown:
        raise WorkflowError("unknown workflow inputs: " + ", ".join(sorted(unknown)))
    result = copy.deepcopy(values)
    types = {"string": str, "boolean": bool, "array": list, "object": dict}
    for name, field in fields.items():
        if name not in result:
            if "default" not in field:
                raise WorkflowError(f"{name} is required: {field['description']}")
            result[name] = copy.deepcopy(field["default"])
        value = result[name]
        allowed = field["type"] if isinstance(field["type"], list) else [field["type"]]
        if not any(isinstance(value, types[k]) for k in allowed):
            raise WorkflowError(f"{name} must be {' or '.join(allowed)}")
        if "choices" in field and value not in field["choices"]:
            raise WorkflowError(f"{name} must be one of: {', '.join(field['choices'])}")
        if isinstance(value, str) and not value.strip() and field.get("default") != "":
            raise WorkflowError(f"{name} must not be blank")
        if field.get("format") == "document" and value:
            path = Path(value).expanduser()
            if not path.is_absolute():
                path = (base_dir or Path.cwd()) / path
            path = path.resolve()
            if path.suffix.lower() not in (".docx", ".dotx") or not path.is_file():
                raise WorkflowError(f"{name} must name an existing .docx or .dotx file: {path}")
            result[name] = str(path)
    for name in ("scope", "levels", "keep_styles", "replace_only"):
        if name in result:
            value = result[name]
            if any(not isinstance(v, str) or not v.strip() for v in value):
                raise WorkflowError(f"{name} must be a list of nonblank strings")
            if name in ("scope", "levels") and not value:
                raise WorkflowError(f"{name} must not be empty")
    if "style_map" in result:
        pairs = result["style_map"]
        if not pairs or any(not isinstance(p, dict) or set(p) != {"from", "to"}
                            or any(not isinstance(p[k], str) or not p[k].strip() for k in ("from", "to"))
                            for p in pairs):
            raise WorkflowError('style_map must be a nonempty list of {"from": "source style", "to": "destination style"}')
    return result


def _stage(name: str, operation: str, params: dict, select: dict | None = None) -> tuple[str, dict]:
    # Make the actual choices and defaults visible in the existing preview and
    # saved recipes, without exposing an EditSpec to desktop users.
    op = {"op": operation, "params": params}
    if select:
        op["select"] = select
    labels = {"map": "Style mappings", "template": "Example document", "existing": "Existing style definitions",
              "allow_missing": "Skip absent or unused source styles", "scope": "Regions", "which": "Unused styles to remove",
              "keep": "Additional styles to retain", "levels": "Example styles in level order", "from": "Source numbering",
              "level_map": "Source-to-example levels", "direct_formatting": "Direct formatting", "style": "Table style",
              "tables": "Selected tables", "width": "Table width", "repeat_header_rows": "Repeated header rows",
              "cant_split": "Keep rows on one page", "cell_margins": "Cell margins in twips", "borders": "Table borders",
              "clear_cell_overrides": "Remove cell border/margin overrides", "language": "Proofing language",
              "replace_only": "Only replace languages"}

    def display(key, value):
        if key == "map":
            return ", ".join(f"{p['from']} → {p['to']}" for p in value)
        if isinstance(value, bool):
            return "yes" if value else "no"
        if isinstance(value, list):
            return ", ".join(str(v) for v in value) or "none"
        if key == "width" and isinstance(value, dict):
            return str(value["pct"]) + "%" if "pct" in value else str(value.get("twips")) + " twips"
        if isinstance(value, dict):
            return json.dumps(value, ensure_ascii=False) if value else "unchanged"
        return str(value)

    choices = {**params, **(select or {})}
    summary = "; ".join(labels.get(key, key) + ": " + display(key, value) for key, value in choices.items())
    spec = {"request": name, "summary": summary,
            "mode": "formatting", "applies_to": {"or": "all"}, "ops": [op]}
    return name, spec


def build_workflow(name: str, inputs: dict[str, Any], base_dir: Path | None = None) -> dict[str, Any]:
    """Compile explicit workflow inputs into an ordinary validated chain; no writes or model calls."""
    workflow = get_workflow(name)
    v = _inputs(workflow, inputs, base_dir)
    stages = []
    if name == "architect-styles":
        stages.append(_stage("Map architect styles", "map_styles",
                             {"map": v["style_map"], "template": v["template"], "existing": v["existing"],
                              "allow_missing": v["allow_missing"]}, {"scope": v["scope"]}))
        if v["unused_styles"] != "keep":
            stages.append(_stage("Remove unused " + v["unused_styles"] + " styles", "remove_unused_styles",
                                 {"which": v["unused_styles"], "keep": v["keep_styles"]}))
        elif v["keep_styles"]:
            raise WorkflowError("keep_styles applies only when unused_styles is custom or all")
    elif name == "match-numbering":
        source = v["source"]
        if source != "main" and (not isinstance(source, dict) or set(source) != {"styles"}
                                  or not isinstance(source["styles"], list) or not source["styles"]
                                  or any(not isinstance(level, list) or not level
                                         or any(not isinstance(s, str) or not s.strip() for s in level)
                                         for level in source["styles"])):
            raise WorkflowError('source must be "main" or {"styles": [["source level 0 style"], ...]}')
        if any(not isinstance(k, str) or not k.isdigit() or not 0 <= int(k) <= 8
               or type(level) is not int or not 0 <= level < len(v["levels"]) for k, level in v["level_map"].items()):
            raise WorkflowError("level_map must map source levels 0–8 to the index of a supplied example style")
        stages.append(_stage("Match the numbering scheme", "convert_numbering",
                             {"template": v["template"], "levels": v["levels"], "from": source,
                              "level_map": v["level_map"], "existing": v["existing"],
                              "direct_formatting": v["direct_formatting"], "allow_missing": False}))
    elif name == "format-tables":
        if not v["properties"] or not set(v["properties"]) & {"borders", "cell_margins", "width", "repeat_header_rows", "cant_split"}:
            raise WorkflowError("properties must request borders, cell_margins, width, repeat_header_rows, or cant_split")
        if v["tables"] != "all" and (not isinstance(v["tables"], dict) or not v["tables"]):
            raise WorkflowError('tables must be "all" or a nonempty table selector')
        select = {"scope": v["scope"], "tables": v["tables"]}
        params = {"style": v["style"], "existing": v["existing"], "direct_formatting": v["direct_formatting"]}
        if v["template"]:
            params["template"] = v["template"]
        elif v["existing"] == "replace":
            raise WorkflowError("replacing a table style definition requires a template")
        stages.append(_stage("Apply the table style", "apply_table_style", params, select))
        # A style-based selector would change after stage one. Freeze it by
        # requiring a stable selection rather than silently formatting fewer tables.
        if isinstance(v["tables"], dict) and "style_in" in v["tables"]:
            raise WorkflowError("this two-stage workflow needs a stable table selector: use index_range or text instead of style_in")
        stages.append(_stage("Set the table layout", "set_table_props", v["properties"], select))
    else:  # proofing-language
        params = {"language": v["language"]}
        if v["replace_only"]:
            params["replace_only"] = v["replace_only"]
        stages.append(_stage("Set the proofing language", "set_language", params))
    return chain_from_specs(stages, name=workflow["name"], description=workflow["description"])
