"""Command line entry points for LabelTool v7 collaboration files."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .collaboration import (
    compact_worker_log,
    create_project_manifest,
    merge_return_bundles,
    write_assignments,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="LabelTool v7 collaboration utilities")
    commands = parser.add_subparsers(dest="command", required=True)

    create = commands.add_parser("create-project", help="hash images and create project_manifest.json")
    create.add_argument("image_folder")
    create.add_argument("manifest")

    assign = commands.add_parser("assign", help="split a project into contiguous worker assignments")
    assign.add_argument("manifest")
    assign.add_argument("output_folder")
    assign.add_argument("annotators", nargs="+")

    compact = commands.add_parser("compact", help="create a compact worker return ZIP")
    compact.add_argument("output_folder")
    compact.add_argument("annotator_id")
    compact.add_argument("destination_zip")

    merge = commands.add_parser("merge", help="merge ZIP/JSONL return files and report conflicts")
    merge.add_argument("destination_json")
    merge.add_argument("bundles", nargs="+")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "create-project":
        result = create_project_manifest(args.image_folder, args.manifest)
    elif args.command == "assign":
        with Path(args.manifest).open("r", encoding="utf-8") as handle:
            manifest = json.load(handle)
        result = write_assignments(manifest, args.annotators, args.output_folder)
    elif args.command == "compact":
        result = compact_worker_log(args.output_folder, args.annotator_id, args.destination_zip)
    else:
        result = merge_return_bundles(args.bundles, args.destination_json)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
