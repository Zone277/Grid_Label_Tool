"""Split a v6 JSONL log created with a parent input folder into child outputs.

The repair is intentionally conservative:
- source images are read and hashed but never modified;
- existing annotation metadata is copied to a timestamped backup;
- target JSON/JSONL files are written atomically;
- only the latest saved event for each destination relative path is retained.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import time
from pathlib import Path

from .collaboration import sha256_file, stable_image_id


LABEL_DESCRIPTIONS = {
    "0": "背景信息",
    "1": "单层布料",
    "2": "多层布料-无褶皱",
    "3": "多层布料-有褶皱",
}


def atomic_json_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".repair-tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    os.replace(temporary, path)


def atomic_jsonl_write(path: Path, events: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".repair-tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for event in events:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def load_events(path: Path) -> list[dict]:
    events = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSON") from exc
            if not isinstance(event, dict):
                raise ValueError(f"{path}:{line_number}: event is not an object")
            events.append(event)
    return events


def split_relative_path(relative_path: str) -> tuple[str, str]:
    normalized = str(relative_path).replace("\\", "/").strip("/")
    if "/" not in normalized:
        raise ValueError(f"path has no child-folder prefix: {relative_path!r}")
    folder, child_relative = normalized.split("/", 1)
    if not folder or not child_relative:
        raise ValueError(f"invalid mixed relative path: {relative_path!r}")
    return folder, child_relative


def validate_event(event: dict, image_path: Path) -> None:
    required = (
        "relative_path",
        "sha256",
        "base_width",
        "base_height",
        "rows",
        "cols",
        "labels",
    )
    missing = [key for key in required if key not in event]
    if missing:
        raise ValueError(f"{event.get('relative_path')}: missing {missing}")
    labels = event["labels"]
    rows = int(event["rows"])
    cols = int(event["cols"])
    if len(labels) != rows or any(len(row) != cols for row in labels):
        raise ValueError(
            f"{event['relative_path']}: label matrix is not {rows}x{cols}"
        )
    if not image_path.is_file():
        raise FileNotFoundError(f"source image does not exist: {image_path}")
    actual_hash = sha256_file(image_path)
    if actual_hash != event["sha256"]:
        raise ValueError(
            f"{event['relative_path']}: SHA256 mismatch "
            f"(event={event['sha256']}, actual={actual_hash})"
        )


def backup_metadata(root: Path, target_folders: list[str], backup_root: Path) -> list[str]:
    copied = []
    for folder in target_folders:
        source_root = root / folder
        candidates = [source_root / "annotations.json"]
        workers = source_root / "workers"
        if workers.exists():
            candidates.extend(workers.rglob("annotations.jsonl"))
        for source in candidates:
            if not source.is_file():
                continue
            relative = source.relative_to(root)
            destination = backup_root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            copied.append(relative.as_posix())
    return copied


def build_split_events(
    root: Path, source_events: list[dict], annotator_id: str | None
) -> tuple[dict[str, list[dict]], dict]:
    latest_by_destination: dict[tuple[str, str], tuple[int, dict]] = {}
    for index, source_event in enumerate(source_events):
        folder, child_relative = split_relative_path(source_event.get("relative_path", ""))
        destination_key = (folder, child_relative)
        latest_by_destination[destination_key] = (index, source_event)

    split: dict[str, list[dict]] = {}
    for (folder, child_relative), (source_index, source_event) in latest_by_destination.items():
        image_path = root / folder / Path(child_relative)
        validate_event(source_event, image_path)
        repaired = dict(source_event)
        repaired["relative_path"] = child_relative
        repaired["image_id"] = stable_image_id(child_relative, repaired["sha256"])
        repaired["repair_source_relative_path"] = source_event["relative_path"]
        repaired["repair_source_image_id"] = source_event.get("image_id")
        repaired["repair_source_event_index"] = source_index
        if annotator_id:
            repaired["annotator_id"] = annotator_id
        split.setdefault(folder, []).append(repaired)

    for events in split.values():
        events.sort(key=lambda item: int(item["repair_source_event_index"]))

    total_source_images = {}
    for folder in split:
        image_count = sum(
            1
            for path in (root / folder).rglob("*")
            if path.is_file()
            and path.suffix.lower()
            in {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}
            and "exports" not in {part.lower() for part in path.relative_to(root / folder).parts}
        )
        total_source_images[folder] = image_count

    summary = {
        "source_event_count": len(source_events),
        "duplicate_save_count": len(source_events) - len(latest_by_destination),
        "unique_image_count": len(latest_by_destination),
        "folders": {
            folder: {
                "annotated_images": len(events),
                "source_images": total_source_images[folder],
                "remaining_images": total_source_images[folder] - len(events),
            }
            for folder, events in sorted(split.items())
        },
    }
    return split, summary


def write_split_outputs(
    root: Path,
    split: dict[str, list[dict]],
    annotator_id: str,
) -> dict[str, dict]:
    written = {}
    for folder, events in sorted(split.items()):
        target_root = root / folder
        log_path = target_root / "workers" / annotator_id / "annotations.jsonl"
        atomic_jsonl_write(log_path, events)

        first = events[0]
        annotations = {
            "schema_version": 6,
            "label_descriptions": dict(LABEL_DESCRIPTIONS),
            "base_width": first["base_width"],
            "base_height": first["base_height"],
            "images": {
                event["relative_path"]: event
                for event in events
            },
        }
        annotations_path = target_root / "annotations.json"
        atomic_json_write(annotations_path, annotations)
        written[folder] = {
            "log": str(log_path),
            "annotations": str(annotations_path),
            "image_count": len(events),
        }
    return written


def verify_outputs(
    root: Path,
    split: dict[str, list[dict]],
    annotator_id: str,
) -> dict[str, dict]:
    verified = {}
    for folder, expected_events in sorted(split.items()):
        target_root = root / folder
        log_path = target_root / "workers" / annotator_id / "annotations.jsonl"
        actual_events = load_events(log_path)
        if len(actual_events) != len(expected_events):
            raise ValueError(
                f"{folder}: log has {len(actual_events)} events; "
                f"expected {len(expected_events)}"
            )
        with (target_root / "annotations.json").open("r", encoding="utf-8") as handle:
            annotations = json.load(handle)
        images = annotations.get("images", {})
        if len(images) != len(expected_events):
            raise ValueError(
                f"{folder}: annotations.json has {len(images)} images; "
                f"expected {len(expected_events)}"
            )

        for event in actual_events:
            relative = event["relative_path"]
            if relative.startswith(folder + "/"):
                raise ValueError(f"{folder}: prefix was not removed from {relative}")
            expected_id = stable_image_id(relative, event["sha256"])
            if event["image_id"] != expected_id:
                raise ValueError(f"{folder}/{relative}: image_id was not recomputed")
            validate_event(event, target_root / Path(relative))
            if relative not in images:
                raise ValueError(f"{folder}/{relative}: missing from annotations.json")
        verified[folder] = {
            "event_count": len(actual_events),
            "json_image_count": len(images),
            "hashes_valid": True,
            "relative_paths_valid": True,
            "image_ids_valid": True,
        }
    return verified


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Safely split a mixed LabelTool v6 output into child folders."
    )
    parser.add_argument("--root", required=True, help="Parent input folder")
    parser.add_argument(
        "--mixed-log",
        required=True,
        help="JSONL file containing child-prefixed relative paths",
    )
    parser.add_argument("--annotator", required=True)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write repaired metadata; without this flag only analyze",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    root = Path(args.root).resolve()
    mixed_log = Path(args.mixed_log).resolve()
    if not root.is_dir():
        raise NotADirectoryError(root)
    if not mixed_log.is_file():
        raise FileNotFoundError(mixed_log)
    if root not in mixed_log.parents:
        raise ValueError("mixed log must be located inside the specified root")

    source_events = load_events(mixed_log)
    split, summary = build_split_events(root, source_events, args.annotator)
    result = {
        "root": str(root),
        "mixed_log": str(mixed_log),
        "apply": bool(args.apply),
        "summary": summary,
    }
    if not args.apply:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    backup_root = root / f"_labeltool_repair_backup_{timestamp}"
    suffix = 1
    while backup_root.exists():
        backup_root = root / f"_labeltool_repair_backup_{timestamp}_{suffix}"
        suffix += 1
    backup_root.mkdir(parents=True)
    target_folders = sorted(split)
    result["backup_root"] = str(backup_root)
    result["backup_files"] = backup_metadata(root, target_folders, backup_root)
    result["written"] = write_split_outputs(root, split, args.annotator)
    result["verified"] = verify_outputs(root, split, args.annotator)
    report_path = backup_root / "repair_report.json"
    atomic_json_write(report_path, result)
    result["report"] = str(report_path)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
