"""Offline multi-annotator project utilities for LabelTool v6."""

from __future__ import annotations

import hashlib
import json
import os
import time
import zipfile
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}


def sha256_file(path: str | os.PathLike, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def stable_image_id(relative_path: str, sha256: str) -> str:
    normalized = relative_path.replace("\\", "/").lower()
    short = hashlib.sha256(f"{normalized}\0{sha256}".encode("utf-8")).hexdigest()[:16]
    return f"img_{short}"


def list_images(folder: str | os.PathLike) -> list[Path]:
    root = Path(folder)
    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
    )


def create_project_manifest(
    image_folder: str | os.PathLike,
    manifest_path: str | os.PathLike,
    progress: Callable[[int, int, str], None] | None = None,
) -> dict:
    root = Path(image_folder).resolve()
    images = list_images(root)
    records = []
    for index, path in enumerate(images, start=1):
        relative = path.relative_to(root).as_posix()
        digest = sha256_file(path)
        records.append(
            {
                "image_id": stable_image_id(relative, digest),
                "relative_path": relative,
                "sha256": digest,
                "size_bytes": path.stat().st_size,
            }
        )
        if progress:
            progress(index, len(images), relative)
    manifest = {
        "schema_version": 1,
        "project_type": "labeltool-v6",
        "image_root": str(root),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "image_count": len(records),
        "images": records,
    }
    target = Path(manifest_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    _atomic_json_write(target, manifest)
    return manifest


def split_assignments(manifest: Mapping, annotator_ids: Sequence[str]) -> dict:
    workers = [str(item).strip() for item in annotator_ids if str(item).strip()]
    if not workers or len(set(workers)) != len(workers):
        raise ValueError("annotator IDs must be non-empty and unique")
    images = list(manifest.get("images", []))
    base, extra = divmod(len(images), len(workers))
    assignments = {}
    cursor = 0
    for index, worker in enumerate(workers):
        count = base + (1 if index < extra else 0)
        assigned = images[cursor : cursor + count]
        cursor += count
        assignments[worker] = {
            "annotator_id": worker,
            "image_count": len(assigned),
            "image_ids": [record["image_id"] for record in assigned],
            "images": assigned,
        }
    return {
        "schema_version": 1,
        "project_image_count": len(images),
        "assignments": assignments,
    }


def write_assignments(
    manifest: Mapping, annotator_ids: Sequence[str], output_folder: str | os.PathLike
) -> dict:
    payload = split_assignments(manifest, annotator_ids)
    root = Path(output_folder)
    root.mkdir(parents=True, exist_ok=True)
    _atomic_json_write(root / "assignments.json", payload)
    for worker, assignment in payload["assignments"].items():
        _atomic_json_write(root / f"assignment_{_safe_name(worker)}.json", assignment)
    return payload


def append_annotation_event(
    output_folder: str | os.PathLike,
    annotator_id: str,
    image_payload: Mapping,
) -> Path:
    worker = str(annotator_id).strip() or "anonymous"
    path = Path(output_folder) / "workers" / _safe_name(worker) / "annotations.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    event = {
        "schema_version": 1,
        "annotator_id": worker,
        "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        **dict(image_payload),
    }
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return path


def compact_worker_log(
    output_folder: str | os.PathLike,
    annotator_id: str,
    destination_zip: str | os.PathLike,
) -> dict:
    worker = str(annotator_id).strip() or "anonymous"
    log_path = Path(output_folder) / "workers" / _safe_name(worker) / "annotations.jsonl"
    latest = latest_events_from_jsonl(log_path)
    destination = Path(destination_zip)
    destination.parent.mkdir(parents=True, exist_ok=True)
    compact_payload = {
        "schema_version": 1,
        "annotator_id": worker,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "image_count": len(latest),
        "annotations": list(latest.values()),
    }
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "annotations.json",
            json.dumps(compact_payload, ensure_ascii=False, indent=2),
        )
        manifest = Path(output_folder) / "project_manifest.json"
        if manifest.exists():
            archive.write(manifest, arcname="project_manifest.json")
    return compact_payload


def latest_events_from_jsonl(path: str | os.PathLike) -> dict[str, dict]:
    latest: dict[str, dict] = {}
    source = Path(path)
    if not source.exists():
        return latest
    with source.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{source}:{line_number}: invalid JSON") from exc
            image_id = event.get("image_id")
            if not image_id:
                raise ValueError(f"{source}:{line_number}: missing image_id")
            latest[str(image_id)] = event
    return latest


def merge_return_bundles(
    bundles: Iterable[str | os.PathLike],
    destination_json: str | os.PathLike,
) -> dict:
    merged: dict[str, dict] = {}
    conflicts = []
    source_records = []
    for bundle in bundles:
        bundle_path = Path(bundle)
        if bundle_path.suffix.lower() == ".zip":
            with zipfile.ZipFile(bundle_path, "r") as archive:
                payload = json.loads(archive.read("annotations.json").decode("utf-8"))
            events = payload.get("annotations", [])
        elif bundle_path.suffix.lower() == ".jsonl":
            events = list(latest_events_from_jsonl(bundle_path).values())
        else:
            with bundle_path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
            events = payload.get("annotations", payload.get("images", []))
            if isinstance(events, Mapping):
                events = list(events.values())

        source_records.append({"path": str(bundle_path.resolve()), "event_count": len(events)})
        for event in events:
            image_id = str(event.get("image_id") or "")
            if not image_id:
                raise ValueError(f"{bundle_path}: annotation without image_id")
            previous = merged.get(image_id)
            if previous is not None:
                same_hash = previous.get("sha256") == event.get("sha256")
                same_labels = previous.get("labels") == event.get("labels")
                if not (same_hash and same_labels):
                    conflicts.append(
                        {
                            "image_id": image_id,
                            "first_annotator": previous.get("annotator_id"),
                            "second_annotator": event.get("annotator_id"),
                            "same_sha256": same_hash,
                            "same_labels": same_labels,
                        }
                    )
                    continue
            merged[image_id] = dict(event)

    result = {
        "schema_version": 1,
        "merged_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "source_bundles": source_records,
        "image_count": len(merged),
        "conflict_count": len(conflicts),
        "conflicts": conflicts,
        "images": merged,
    }
    _atomic_json_write(Path(destination_json), result)
    return result


def _safe_name(value: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in value)
    return safe.strip("_") or "anonymous"


def _atomic_json_write(path: Path, payload: Mapping) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    os.replace(temp, path)

