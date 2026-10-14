"""Header/footer helpers shared by remove_header_footer and apply_header_footer_template (transform side).

Removing a header/footer *reference* can leave its part unreferenced; the part then goes with its .rels, its
content-type override, its relationship in document.xml.rels and any media nothing else uses. The prediction
(``plan_removal``) walks the SOURCE reference graph; ``apply_removal`` performs it with the package helpers and
cross-checks ``unreferenced_media()`` against the prediction.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from ..package import CT_PART, MEDIA_REL_TYPES, R, W, DocxPackage, REL_TYPE_BASE
from ..ooxml import block_full_text, count_revisions, paragraphs
from .base import BlockDiff, JobException

KINDS = ("header", "footer")
TYPES = ("default", "first", "even")
HYPERLINK_REL = REL_TYPE_BASE + "hyperlink"
HEADER_CT = "application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml"
FOOTER_CT = "application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"


def refs(sp) -> List[Tuple[str, str, str, object]]:
    """(kind, type, rId, element) for every header/footer reference of one sectPr."""
    out = []
    for el in sp:
        if el.tag in (W + "headerReference", W + "footerReference"):
            kind = "header" if el.tag == W + "headerReference" else "footer"
            out.append((kind, el.get(W + "type", "default"), el.get(R + "id"), el))
    return out


def check_part_relationships(pkg: DocxPackage, part: str) -> None:
    """Header/footer parts are handled when their relationships are images (internal) or hyperlinks only."""
    for r in pkg.rels(part).values():
        if r.target_mode == "External" and r.type == HYPERLINK_REL:
            continue
        if r.type not in MEDIA_REL_TYPES:
            raise JobException(f"{part} carries a {r.type.rsplit('/', 1)[-1]} relationship ({r.target}); "
                               f"only images and hyperlinks are handled", "structure",
                               {"part": part, "relationship": r.rid, "type": r.type})


@dataclass
class RemovalPlan:
    gone_rids: List[str] = field(default_factory=list)
    parts_gone: List[str] = field(default_factory=list)
    members: List[str] = field(default_factory=list)      # parts + .rels + media, in package order
    media: List[str] = field(default_factory=list)
    diffs: List[BlockDiff] = field(default_factory=list)
    rev_gone: Dict[str, int] = field(default_factory=dict)
    flags: List[str] = field(default_factory=list)
    ct_changes: bool = False


def plan_removal(pkg: DocxPackage, main: str, root, removed_refs: Sequence[Tuple[int, str, str, str]],
                 kept_rids: Sequence[str]) -> RemovalPlan:
    """What disappears when ``removed_refs`` (section, kind, type, rId) are deleted and ``kept_rids`` stay."""
    rels = pkg.rels(main)
    part_of = {rid: r.resolved for rid, r in rels.items()}
    other_rid_uses = {v for el in root.iter() if isinstance(el.tag, str)
                      for k, v in el.attrib.items() if k.startswith(R)
                      and el.tag not in (W + "headerReference", W + "footerReference")}
    rp = RemovalPlan()
    rp.gone_rids = sorted({rid for _, _, _, rid in removed_refs} - set(kept_rids) - other_rid_uses)
    still_targeted = {part_of.get(rid) for rid in rels if rid not in rp.gone_rids}
    for rid in rp.gone_rids:
        part = part_of.get(rid)
        if part and pkg.has(part) and part not in still_targeted and part not in rp.parts_gone:
            rp.parts_gone.append(part)
    removed_set = set(rp.parts_gone)
    members: List[str] = []
    for part in rp.parts_gone:
        members.append(part)
        relp = pkg.rels_path_for(part)
        if pkg.has(relp):
            members.append(relp)
            check_part_relationships(pkg, part)
        proot = pkg.xml(part)
        for bi, p in enumerate(paragraphs(proot)):
            rp.diffs.append(BlockDiff(part=part, block_index=bi, before=block_full_text(p), after=None,
                                      note="part removed"))
        revs = count_revisions(proot)
        for k, v in revs.items():
            rp.rev_gone[k] = rp.rev_gone.get(k, 0) + v
        if revs:
            authors = sorted({el.get(W + "author") for el in proot.iter(W + "ins", W + "del", W + "rPrChange",
                                                                          W + "pPrChange") if el.get(W + "author")})
            rp.flags.append(f"{part} carried pending tracked change(s) {revs} by {authors}; removing the part "
                            f"discards them.")
    remaining_targets = {r.resolved for owner, r in pkg.relationships(source=True)
                         if owner not in removed_set and r.resolved
                         and not (owner == main and r.rid in rp.gone_rids)}
    for part in rp.parts_gone:
        for r in pkg.rels(part).values():
            if r.type in MEDIA_REL_TYPES and r.resolved and pkg.has(r.resolved) \
                    and r.resolved not in remaining_targets and r.resolved not in rp.media:
                rp.media.append(r.resolved)
    members += rp.media
    order = {n: i for i, n in enumerate(pkg.member_names)}
    rp.members = sorted(set(members), key=lambda n: order[n])
    _, overrides = pkg.content_types()
    rp.ct_changes = any(m in overrides for m in rp.members)
    return rp


def apply_removal(pkg: DocxPackage, main: str, rids: Sequence[str], members: Sequence[str],
                  media: Sequence[str]) -> None:
    """Drop the relationships and members a RemovalPlan predicted (references already removed by the caller),
    cross-checking the media prediction against the package's own orphan detection."""
    if rids:
        pkg.edit_rels(main, remove=list(rids))
    media_set = set(media)
    for m in members:
        if m not in media_set:
            pkg.remove_member(m)
    orphaned = pkg.unreferenced_media()
    if sorted(orphaned) != sorted(media_set):
        raise JobException(f"internal: media orphaned by this removal {sorted(orphaned)} differs from the plan's "
                           f"prediction {sorted(media_set)}", "internal")
    for m in media:
        pkg.remove_member(m)


def effective_refs(pkg: DocxPackage, sections=None) -> List[Dict[Tuple[str, str], Optional[str]]]:
    """Per section, what part Word shows for every (kind, type), inheritance included (resolved via rels)."""
    main = pkg.main_part()
    rels = pkg.rels(main)
    secs = DocxPackage.sect_prs(pkg.xml(main)) if sections is None else sections
    out, prev = [], {}
    for sp in secs:
        cur = dict(prev)
        for kind, typ, rid, _ in refs(sp):
            cur[(kind, typ)] = rels[rid].resolved if rid in rels else None
        out.append(cur)
        prev = cur
    return out
