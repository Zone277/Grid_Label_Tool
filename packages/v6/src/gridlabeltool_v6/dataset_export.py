"""Dataset-root discovery helpers for LabelTool v6 multi-scale export."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

from .collaboration import IMAGE_EXTENSIONS, latest_events_from_jsonl


SKIP_IMAGE_DIRS = {"exports", "workers", "__pycache__", "build", "dist"}


def is_source_image_path(root: str | Path, path: str | Path) -> bool:
    root_path = Path(root)
    image_path = Path(path)
    if not image_path.is_file() or image_path.suffix.lower() not in IMAGE_EXTENSIONS:
        return False
    try:
        parts = image_path.relative_to(root_path).parts
    except ValueError:
        parts = image_path.parts
    return not any(part.lower() in SKIP_IMAGE_DIRS for part in parts[:-1])


def has_annotation_data(folder: str | Path) -> bool:
    root = Path(folder)
    if (root / "annotations.json").exists():
        return True
    workers_root = root / "workers"
    return workers_root.exists() and any(workers_root.glob("*/annotations.jsonl"))


def load_annotation_store(folder: str | Path) -> tuple[dict, list[str]]:
    root = Path(folder)
    store: dict = {"schema_version": 6, "images": {}}
    warnings: list[str] = []
    annotations_path = root / "annotations.json"
    if annotations_path.exists():
        try:
            with annotations_path.open("r", encoding="utf-8") as handle:
                loaded = json.load(handle)
            if isinstance(loaded, dict) and isinstance(loaded.get("images"), dict):
                normalized = {}
                for key, value in loaded["images"].items():
                    if isinstance(value, dict):
                        normalized[str(value.get("relative_path") or key)] = value
                store = {**loaded, "images": normalized}
        except (OSError, json.JSONDecodeError) as exc:
            warnings.append(f"{annotations_path}: {exc}")

    workers_root = root / "workers"
    if workers_root.exists():
        for log_path in sorted(workers_root.glob("*/annotations.jsonl")):
            try:
                latest = latest_events_from_jsonl(log_path)
            except ValueError as exc:
                warnings.append(str(exc))
                continue
            for event in latest.values():
                relative = event.get("relative_path")
                if relative:
                    store.setdefault("images", {})[str(relative)] = event
    return store, warnings


def records_from_store(
    store: Mapping,
    image_root: str | Path,
    dataset: str | None = None,
    infer_dataset_from_relative: bool = False,
) -> tuple[list[dict], list[str]]:
    root = Path(image_root)
    records: list[dict] = []
    missing: list[str] = []
    for key, payload in store.get("images", {}).items():
        if not isinstance(payload, Mapping):
            continue
        relative = str(payload.get("relative_path") or key)
        image_path = root / relative
        labels = payload.get("labels")
        record_dataset = dataset or str(payload.get("dataset") or "").strip()
        source_relative = str(payload.get("source_relative_path") or relative)
        if not record_dataset and infer_dataset_from_relative:
            parts = relative.replace("\\", "/").split("/")
            if len(parts) > 1 and has_annotation_data(root / parts[0]):
                record_dataset = parts[0]
                source_relative = "/".join(parts[1:])
        if not image_path.exists():
            missing.append(f"{root.name}/{relative}")
            continue
        if not labels:
            continue
        record = {
            "image_id": payload.get("image_id") or image_path.stem,
            "image_path": str(image_path),
            "sha256": payload.get("sha256"),
            "labels": labels,
            "source_relative_path": source_relative,
        }
        if record_dataset:
            record["dataset"] = record_dataset
        records.append(record)
    return records, missing


def prefixed_store_for_image_root(
    store: Mapping,
    prefix: str,
) -> dict:
    """Convert a child material store into image-root relative keys."""
    result = {"schema_version": store.get("schema_version", 6), "images": {}}
    clean_prefix = prefix.replace("\\", "/").strip("/")
    for key, payload in store.get("images", {}).items():
        if not isinstance(payload, Mapping):
            continue
        child_relative = str(payload.get("relative_path") or key).replace("\\", "/")
        root_relative = f"{clean_prefix}/{child_relative}".strip("/")
        copied = dict(payload)
        copied["relative_path"] = root_relative
        copied["dataset"] = clean_prefix
        copied["source_relative_path"] = child_relative
        result["images"][root_relative] = copied
    for name in ("base_width", "base_height", "label_descriptions"):
        if name in store:
            result[name] = store[name]
    return result


def collect_history_store(
    image_folder: str | Path | None,
    output_folder: str | Path,
) -> dict:
    """Load annotations for UI history replay, including material child folders."""
    image_root = Path(image_folder) if image_folder else None
    output_root = Path(output_folder)
    result: dict = {"schema_version": 6, "images": {}}
    warnings: list[str] = []
    sources: list[dict] = []

    def merge_store(store: Mapping, source: dict, overwrite: bool = True) -> None:
        added = 0
        for key, payload in store.get("images", {}).items():
            if overwrite or key not in result["images"]:
                result["images"][str(key)] = payload
                added += 1
        for name in ("base_width", "base_height", "label_descriptions"):
            if name in store and name not in result:
                result[name] = store[name]
        sources.append({**source, "image_count": added})

    if has_annotation_data(output_root):
        store, source_warnings = load_annotation_store(output_root)
        warnings.extend(source_warnings)
        merge_store(store, {"kind": "output_folder", "folder": str(output_root)})

    if image_root and output_root.exists():
        for child in sorted(path for path in output_root.iterdir() if path.is_dir()):
            if child.name.lower() in SKIP_IMAGE_DIRS or not has_annotation_data(child):
                continue
            image_child = image_root / child.name
            if not image_child.exists():
                continue
            store, source_warnings = load_annotation_store(child)
            warnings.extend(source_warnings)
            prefixed = prefixed_store_for_image_root(store, child.name)
            merge_store(
                prefixed,
                {
                    "kind": "material_folder",
                    "folder": str(child),
                    "dataset": child.name,
                },
                overwrite=False,
            )

    result["_history_warnings"] = warnings
    result["_history_sources"] = sources
    return result


def collect_export_records(
    image_folder: str | Path,
    output_folder: str | Path | None = None,
    current_store: Mapping | None = None,
) -> dict:
    """Collect records from the active project and annotated material children."""
    image_root = Path(image_folder)
    output_root = Path(output_folder) if output_folder else None
    records: list[dict] = []
    missing: list[str] = []
    warnings: list[str] = []
    sources: list[dict] = []
    seen: set[tuple[str, str]] = set()

    def add_records(source_records: list[dict], source_missing: list[str], source: dict) -> None:
        added = 0
        for record in source_records:
            key = (
                str(record.get("dataset") or ""),
                str(Path(record["image_path"]).resolve()).lower(),
            )
            if key in seen:
                continue
            seen.add(key)
            records.append(record)
            added += 1
        missing.extend(source_missing)
        sources.append({**source, "image_count": added, "missing_count": len(source_missing)})

    if current_store and current_store.get("images"):
        source_records, source_missing = records_from_store(
            current_store,
            image_root,
            infer_dataset_from_relative=True,
        )
        add_records(
            source_records,
            source_missing,
            {
                "kind": "active_output",
                "folder": str(output_root) if output_root else "",
                "dataset": "",
            },
        )

    if has_annotation_data(image_root):
        store, source_warnings = load_annotation_store(image_root)
        warnings.extend(source_warnings)
        source_records, source_missing = records_from_store(store, image_root)
        add_records(
            source_records,
            source_missing,
            {"kind": "input_folder", "folder": str(image_root), "dataset": ""},
        )

    if image_root.exists():
        for child in sorted(path for path in image_root.iterdir() if path.is_dir()):
            if child.name.lower() in SKIP_IMAGE_DIRS:
                continue
            if not has_annotation_data(child):
                continue
            store, source_warnings = load_annotation_store(child)
            warnings.extend(source_warnings)
            source_records, source_missing = records_from_store(
                store, child, dataset=child.name
            )
            add_records(
                source_records,
                source_missing,
                {
                    "kind": "material_folder",
                    "folder": str(child),
                    "dataset": child.name,
                },
            )

    return {
        "records": records,
        "sources": sources,
        "missing": missing,
        "warnings": warnings,
    }
