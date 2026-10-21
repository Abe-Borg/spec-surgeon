"""Op: remove_images — the pictures a match chooses go from the parts in scope; the paragraph that held each stays
(built-in batch 6, docs/BUILTIN_OPS_BATCH6_DESIGN.md §5). The verifier derives all of it again (verify/pictures.py,
kind ``removed_images``).

Definition (both sides; the pictures, the match and the refusals every picture op shares are ops/pictures.py's):

* **the pictures**: those the match chooses in the scope (the document body by default; headers, footers). A matched
  picture inside a pending tracked change is refused (``existing-revisions``), and so is one a field shows
  (``field-result``: Word would bring it back when it updates the field); a picture's ``w:drawing`` must be a run's
  child (``structure``). Watermarks are remove_watermark's: never matched (noted).
* **Tracked** (the default), as Word records a deleted picture: a run whose content (its children but ``w:rPr``) is all
  matched drawings goes inside a new ``w:del`` whole; any other run holding one is split where its content changes
  between matched drawings and the rest — each stretch, in order, a run of its own with the run's attributes and
  ``w:rPr`` — and each stretch of matched drawings is wrapped in a ``w:del`` of its own. A run to split that carries a
  pending formatting change (``w:rPrChange``: its revision id would be copied) is refused (``tracked-format``). The
  picture stays referenced until the deletion is accepted: no relationship and no member changes.
* **Untracked** (``tracked: false``): each matched ``w:drawing`` leaves its run, and a run left with nothing but its
  ``w:rPr`` goes. A relationship of the part that only removed drawings name (an attribute in the relationships
  namespace inside them, none outside) goes; a member a removed relationship targeted that no relationship of the
  package targets any more goes too, with its Override (one with relationships of its own refuses: ``structure``).
  Each paragraph's drawing count drops (a structure-only declaration).

No picture matched in any part in scope → ``not-found`` unless ``allow_missing``.
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

from lxml import etree

from ..ooxml import RevisionStamp, block_full_text, block_struct_counts, clone_run_shell, file_uses_date_utc, \
    max_revision_id, paragraphs
from ..package import CT_PART, DocxPackage
from . import prose, register
from .base import BlockDiff, JobException, Op, PlanFragment
from .images import check_unchanged
from .insert_paragraph import default_author
from .pictures import (R, W, field_drawings, given_scope, in_revision, match_rule, match_words, matched_pictures,
                       media_md5s, r_values, scope_of, scope_words)
from .selectors import scope_parts

PARAMS = ("match", "scope", "tracked", "author", "allow_missing", "date_convention")


def content_of(run) -> List[Any]:
    """A run's content: its children but ``w:rPr``, in order."""
    return [c for c in run if c.tag != W + "rPr"]


def stretches(run, matched: set) -> List[Tuple[bool, List[Any]]]:
    """The run's content cut where it changes between matched drawings (``matched``: their ids) and the rest:
    (matched?, children) in order."""
    out: List[Tuple[bool, List[Any]]] = []
    for c in content_of(run):
        hit = id(c) in matched
        if out and out[-1][0] == hit:
            out[-1][1].append(c)
        else:
            out.append((hit, [c]))
    return out


def remove_tracked(runs: List[Any], matched: set, stamp, ids: List[int]) -> None:
    """Each run's matched drawings into a ``w:del`` (module docstring); ``ids`` hands out the revision ids."""
    for run in runs:
        parts = stretches(run, matched)
        if len(parts) == 1:
            wrap = etree.Element(W + "del")
            stamp.apply(wrap, ids.pop(0))
            run.addprevious(wrap)
            wrap.append(run)
            continue
        for hit, children in parts:
            piece = clone_run_shell(run)
            piece.extend(children)
            if hit:
                wrap = etree.Element(W + "del")
                stamp.apply(wrap, ids.pop(0))
                wrap.append(piece)
                piece = wrap
            run.addprevious(piece)
        run.getparent().remove(run)


def remove_untracked(drawings: List[Any]) -> None:
    for d in drawings:
        run = d.getparent()
        run.remove(d)
        if not content_of(run):
            run.getparent().remove(run)


@register
class RemoveImages(Op):
    name = "remove_images"
    mode = "content"
    description = ("Remove the pictures a match chooses — by their file (match {'image': old.png}), md5, name/alt text "
                   "(name_regex) or 'all' — from the document body (default), headers and/or footers; the paragraph "
                   "that held each stays. Tracked (default, the picture shown deleted) or untracked (its file leaves "
                   "the document when nothing else shows it). Watermarks are remove_watermark's; VML pictures, pictures "
                   "in shapes or text boxes and pictures a field shows are refused.")
    params_schema = {
        "type": "object",
        "properties": {
            "match": {"description": "{image: the picture file} | {md5} | {name_regex} | 'all'"},
            "scope": {"type": "array", "items": {"type": "string", "enum": ["document", "headers", "footers"]},
                      "default": ["document"]},
            "tracked": {"type": "boolean", "default": True},
            "author": {"type": "string"},
            "allow_missing": {"type": "boolean", "default": False,
                              "description": "a file without a matching picture passes unchanged instead of being "
                                             "set aside"},
            "date_convention": {"type": "string", "enum": ["word-local-z", "utc"], "default": "word-local-z"},
        },
        "required": ["match"],
    }
    select_schema = {"type": "object", "properties": {"scope": {"type": "array"}}}
    verification_profile = {
        "V1": "untracked: the parts' rels and the media no relationship targets any more (removed), [Content_Types].xml "
              "when an Override goes; tracked: the parts only",
        "V3": "untracked: a structure-only declaration per paragraph (its drawings fewer)",
        "V4": "tracked: +1 del per stretch of removed pictures",
        "V10": "removed_images re-derives the matches (its own md5 of every media member, its own picture reading and "
               "refusals) and checks each paragraph item by item: exactly those drawings gone (or inside a new "
               "w:del), the runs split as the rule says; the relationships, members and content types whole",
    }

    def describe(self, select: Optional[Dict[str, Any]], params: Dict[str, Any]) -> str:
        select, params = dict(select or {}), dict(params or {})
        scope = select.get("scope") or params.get("scope")
        head = f"Remove {match_words(params.get('match'))} {scope_words(scope)}, leaving the paragraphs that held them"
        if params.get("tracked", True) is False:
            head += ", without tracked changes; a picture's file leaves the document when nothing else shows it"
        else:
            head += ", shown as a tracked change" + (f" by {prose.quote(params['author'])}" if params.get("author")
                                                      else "")
        second = "Files with no such picture are left unchanged." if params.get("allow_missing") is True else ""
        return prose.sentence(head + ".", second)

    def plan(self, pkg: DocxPackage, select: Dict[str, Any], params: Dict[str, Any]) -> PlanFragment:
        select, params = dict(select or {}), dict(params or {})
        if set(select) - {"scope"}:
            raise JobException(f"remove_images' selector takes only 'scope' (got {sorted(select)})", "bad-params")
        unknown = set(params) - set(PARAMS)
        if unknown:
            raise JobException(f"remove_images has no parameter(s) {sorted(unknown)}", "bad-params")
        match = match_rule(params.get("match"), "remove_images")
        tracked = params.get("tracked", True)
        allow_missing = params.get("allow_missing", False)
        if not isinstance(tracked, bool) or not isinstance(allow_missing, bool):
            raise JobException("'tracked' and 'allow_missing' must be true or false", "bad-params")
        convention = params.get("date_convention", "word-local-z")
        if convention not in ("word-local-z", "utc"):
            raise JobException("date_convention must be 'word-local-z' or 'utc'", "bad-params")
        author = params.get("author") or default_author()
        scope = scope_of(select, params, "remove_images")
        parts = scope_parts(pkg, scope)
        md5_of = media_md5s(pkg)
        frag = self.fragment({"scope": scope} if given_scope(select, params) else {},
                             {**params, "tracked": tracked, "author": author},
                             mode="content" if tracked else "untracked")
        frag.untracked_diffs_expected = not tracked
        frag.geometry_reference = "none"
        frag.geometry_reason = "a removed picture no longer takes its space on the page"
        chosen: Dict[str, List[int]] = {}
        held: Dict[str, Any] = {}                    # part → (root, found, drawings): kept alive while planning
        skipped = 0
        for part in parts:
            frag.touches.append((part, "scope", "verify"))
            if match["how"] == "md5":                # which media a picture shows is read through the part's rels
                frag.touches.append((pkg.rels_path_for(part), "*", "verify"))
            root = pkg.xml(part)
            found, n_skipped, drawings = matched_pictures(pkg, part, root, match, md5_of)
            skipped += n_skipped
            in_field = field_drawings(root)
            for f in found:
                if f.drawing.getparent() is None or f.drawing.getparent().tag != W + "r":
                    raise JobException(f"{part}: picture {f.ordinal} is not in a run of its own paragraph", "structure",
                                       {"part": part, "picture": f.ordinal})
                if in_revision(f.drawing, f.paragraph):
                    raise JobException(f"{part}: picture {f.ordinal} is part of a pending tracked change — accept or "
                                       f"reject it first", "existing-revisions", {"part": part, "picture": f.ordinal})
                if id(f.drawing) in in_field:
                    raise JobException(f"{part}: picture {f.ordinal} is shown by a field — Word would bring it back "
                                       f"when it updates the field; remove the field in Word", "field-result",
                                       {"part": part, "picture": f.ordinal})
            if found:
                chosen[part] = [f.ordinal for f in found]
                held[part] = (root, found, drawings)
        if skipped:
            frag.notes.append(f"remove_images: {skipped} watermark picture(s) left alone — a watermark is "
                              f"remove_watermark's")
        assertion: Dict[str, Any] = {
            "kind": "removed_images", "match": params.get("match"), "match_md5": match["md5"] if match.get("file")
            else None, "scope": given_scope(select, params), "tracked": tracked, "author": author,
            "allow_missing": allow_missing, "expected": chosen, "watermarks": skipped}
        if not chosen:
            if not allow_missing:
                raise JobException(f"no picture in the {', '.join(scope)} matches — nothing to remove", "not-found",
                                   {"scope": scope})
            frag.notes.append("remove_images: no matching picture in this file (allowed); nothing removed")
            assertion.update(rels_removed={}, members_removed=[])
            frag.property_assertions.append(assertion)
            return frag
        dels = 0
        gone_rids: Dict[str, List[str]] = {}
        td_parts: Dict[str, Dict[str, Any]] = {}
        for part, (root, found, _) in held.items():
            frag.intended_parts.append(part)
            pars = paragraphs(root)
            by_block: Dict[int, List[Any]] = {}
            for f in found:
                by_block.setdefault(f.block, []).append(f.drawing)
            for bi, ds in sorted(by_block.items()):
                frag.touches.append((part, str(bi), "text"))
                p = pars[bi]
                if tracked:
                    ids = {id(d) for d in ds}
                    runs = list(dict.fromkeys(d.getparent() for d in ds))
                    for r in runs:
                        if len(stretches(r, ids)) > 1 and r.find(f"{W}rPr/{W}rPrChange") is not None:
                            raise JobException(f"{part} paragraph {bi}: a picture shares its run with text that carries "
                                               f"a pending formatting change — accept or reject it first",
                                               "tracked-format", {"part": part, "paragraph": bi})
                    dels += sum(1 for r in runs for hit, _ in stretches(r, ids) if hit)
                else:
                    text = block_full_text(p)
                    frag.diffs.append(BlockDiff(part=part, block_index=bi, before=text, after=text,
                                                struct_after=block_struct_counts(p, ds),
                                                note=f"{len(ds)} picture(s) removed (structure only)"))
            td_parts[part] = {"ordinals": chosen[part]}
            if not tracked:
                rels = pkg.rels(part)
                inside = Counter(v for f in found for e in f.drawing.iter() if isinstance(e.tag, str)
                                 for k, v in e.attrib.items() if k.startswith(R))
                everywhere = Counter(v for e in root.iter() if isinstance(e.tag, str)
                                     for k, v in e.attrib.items() if k.startswith(R))
                rids = sorted(v for v in inside if v in rels and everywhere[v] == inside[v])
                if rids:
                    gone_rids[part] = rids
                    td_parts[part]["rids"] = rids
        members: List[str] = []
        if gone_rids:
            pairs = {(part, rid) for part, rids in gone_rids.items() for rid in rids}
            targets = {pkg.rels(part)[rid].resolved for part, rid in pairs
                       if pkg.rels(part)[rid].target_mode == "Internal" and pkg.rels(part)[rid].resolved}
            still = set()
            for owner, r in pkg.relationships():
                if r.resolved in targets and (owner, r.rid) not in pairs:
                    still.add(r.resolved)
                    frag.touches.append((pkg.rels_path_for(owner) if owner else "_rels/.rels", "*", "verify"))
            members = [m for m in pkg.member_names if m in targets and m not in still]
            for m in members:
                if pkg.has(pkg.rels_path_for(m)):
                    raise JobException(f"{m}, which a removed picture shows, has relationships of its own — remove "
                                       f"that picture in Word", "structure", {"member": m})
            for part in gone_rids:
                frag.intended_parts.append(pkg.rels_path_for(part))
                frag.touches.append((pkg.rels_path_for(part), "*", "rels"))
            _, overrides = pkg.content_types()
            if any(m in overrides for m in members):
                frag.intended_parts.append(CT_PART)
                frag.touches.append((CT_PART, "*", "ct"))
            frag.members_removed = members
            frag.touches += [(m, "*", "remove") for m in members]
        n = sum(len(v) for v in chosen.values())
        if tracked:
            frag.rev_delta["del"] = frag.rev_delta.get("del", 0) + dels
            frag.rev_id_base = max_revision_id(pkg) + 1
            frag.rev_ids_needed = dels
        else:
            frag.flags.append(f"UNTRACKED removal of {n} picture(s) at the user's request.")
        assertion.update(rels_removed=gone_rids, members_removed=members)
        frag.property_assertions.append(assertion)
        frag.notes.append(f"remove_images: {n} picture(s) out of {len(chosen)} part(s) "
                          f"({'tracked' if tracked else 'untracked'}): "
                          + "; ".join(f"{p} picture(s) {v}" for p, v in chosen.items())
                          + (f"; relationships removed {gone_rids}" if gone_rids else "")
                          + (f"; files removed {members}" if members else ""))
        frag.transform_data = {"tracked": tracked, "author": author, "use_date_utc": file_uses_date_utc(pkg),
                               "date_convention": convention, "first_rev_id": frag.rev_id_base, "parts": td_parts,
                               "members": members, "match": match}
        return frag

    def apply(self, pkg: DocxPackage, frag: PlanFragment) -> None:
        td = frag.transform_data
        if not td:
            return
        if td["match"].get("file"):
            check_unchanged(td["match"]["file"], td["match"]["md5"], "remove_images.match.image")
        stamp = RevisionStamp.now(td["author"], td["use_date_utc"], td["date_convention"])
        ids = list(range(td["first_rev_id"], td["first_rev_id"] + frag.rev_ids_needed))
        for part, d in td["parts"].items():
            root = pkg.xml(part)
            drawings = list(root.iter(W + "drawing"))
            mine = [drawings[k] for k in d["ordinals"]]
            if td["tracked"]:
                remove_tracked(list(dict.fromkeys(x.getparent() for x in mine)), {id(x) for x in mine}, stamp, ids)
            else:
                remove_untracked(mine)
            pkg.write_xml(part, root)
            if d.get("rids"):
                pkg.edit_rels(part, remove=d["rids"])
        for m in td["members"]:
            pkg.remove_member(m)
        if not td["tracked"] and pkg.unreferenced_media():
            raise JobException(f"internal: media no longer shown but still in the file {pkg.unreferenced_media()}",
                               "internal")
