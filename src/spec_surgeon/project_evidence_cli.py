"""Explicit local maintenance of structured facts in a campaign's project context."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .campaign import load_campaign
from .project_evidence import validate_context


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workspace", type=Path)
    parser.add_argument("project", help="Project ID from workspace inspect")
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("inspect")
    add = sub.add_parser("add", help="Add a fact from a reviewed JSON object")
    add.add_argument("fact", type=Path)
    supersede = sub.add_parser("supersede", help="Replace an active fact, preserving history")
    supersede.add_argument("old_id")
    supersede.add_argument("fact", type=Path)
    standards = sub.add_parser("standards", help="Set the complete user-supplied standards register")
    standards.add_argument("register", type=Path, help="JSON array of approved standards entries")
    args = parser.parse_args(argv)
    try:
        campaign = load_campaign(args.workspace)
        if args.project not in campaign.projects:
            raise ValueError("Unknown project ID")
        if args.action in {"add", "supersede"}:
            fact = json.loads(args.fact.read_text(encoding="utf-8"))
            if args.action == "add":
                campaign.set_project_fact(args.project, fact)
            else:
                campaign.supersede_project_fact(args.project, args.old_id, fact)
        elif args.action == "standards":
            entries = json.loads(args.register.read_text(encoding="utf-8"))
            campaign.set_standards_register(args.project, entries)
        context = validate_context(campaign.projects[args.project]["context"])
        print(json.dumps(context.get("project_evidence", {"version": 1, "facts": [], "standards": []}),
                         indent=2, ensure_ascii=False, allow_nan=False))
    except (ValueError, OSError, KeyError) as exc:
        parser.exit(2, f"Project evidence error: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
