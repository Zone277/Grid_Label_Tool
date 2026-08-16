"""Path-first metadata audit and repair helpers for LabelTool v7."""

from __future__ import annotations

import json
import os
import shutil
import time
from collections import defaultdict
from pathlib import Path
from typing import Mapping

from PIL import Image

from .collaboration import path_based_image_id, sha256_file
from .multiscale_core import make_padding_plan


SKIP_ANNOTATION_DIRS = {
    "exports",
    "workers",
    "metadata_audit",
    "__pycache__",
    "build",
    "dist",
}


def resolve_export_output_folder(
    image_folder: str | os.PathLike | None,
    output_folder: str | os.PathLike | None,
) -> Path | None:
    """Redirect same input/output export runs to a sibling folder."""
    if not output_folder:
        return None
    output = Path(output_folder)
    if not image_folder:
        return output
    image = Path(image_folder)
    try:
        same_folder = image.resolve() == output.resolve()
    except OSError:
        same_folder = image.absolute() == output.absolute()
    if not same_folder:
        return output
    return output.parent / f"{output.name}_v7_exports"


def path_image_id_from_record(record: Mapping) -> str:
    root_relative = canonical_root_relative_from_record(record)
    return path_based_image_id(root_relative)


def canonical_root_relative_from_record(record: Mapping) -> str:
    dataset = str(record.get("dataset") or "").replace("\\", "/").strip("/")
    source_relative = str(record.get("source_relative_path") or "").replace(
        "\\", "/"
    ).strip("/")
    relative = str(record.get("relative_path") or "").replace("\\", "/").strip("/")
    if dataset and source_relative:
        return f"{dataset}/{source_relative}".strip("/")
    if relative:
        return relative
    image_path = record.get("image_path") or record.get("source_image") or ""
    return Path(str(image_path)).name or "image"


def canonical_record_key(record: Mapping) -> tuple[str, str]:
    root_relative = canonical_root_relative_from_record(record)
    parts = root_relative.split("/", 1)
    dataset = str(record.get("dataset") or "").replace("\\", "/").strip("/")
    source_relative = str(record.get("source_relative_path") or "").replace(
        "\\", "/"
    ).strip("/")
    if not dataset and len(parts) == 2:
        dataset, source_relative = parts
    if not source_relative:
        source_relative = root_relative if not dataset else parts[-1]
    return dataset.lower(), source_relative.lower()


def audit_annotation_tree(
    image_root: str | os.PathLike,
    annotation_root: str | os.PathLike,
    base_width: int | None = None,
    base_height: int | None = None,
    compute_sha: bool = False,
) -> dict:
    image_root_path = Path(image_root)
    annotation_root_path = Path(annotation_root)
    contexts, load_warnings = _collect_contexts(
        image_root_path, annotation_root_path
    )
    identity_map, collision_groups = _path_identity_map(contexts)
    issues: list[dict] = []
    file_summaries: dict[str, dict] = {}
    duplicate_image_ids = _duplicate_image_ids(contexts)

    for warning in load_warnings:
        issues.append({"severity": "blocking", "type": "read_error", **warning})

    for context in contexts:
        annotation_file = context["annotation_file"]
        file_key = str(annotation_file)
        summary = file_summaries.setdefault(
            file_key,
            {
                "annotation_file": file_key,
                "record_count": 0,
                "repairable_count": 0,
                "blocking_count": 0,
            },
        )
        summary["record_count"] += 1
        record = context["record"]
        expected_id = identity_map[context["root_relative_path"]]
        current_id = str(record.get("image_id") or "")
        record_issues = _audit_one_record(
            context,
            expected_id,
            base_width,
            base_height,
            compute_sha,
        )
        for issue in record_issues:
            issues.append(issue)
            if issue["severity"] == "blocking":
                summary["blocking_count"] += 1
            elif issue["severity"] == "repairable":
                summary["repairable_count"] += 1
        if current_id != expected_id:
            summary["repairable_count"] += 1

    for image_id, paths in duplicate_image_ids.items():
        if len(paths) > 1:
            issues.append(
                {
                    "severity": "repairable",
                    "type": "duplicate_image_id",
                    "image_id": image_id,
                    "root_relative_paths": paths,
                }
            )

    for base_id, paths in collision_groups.items():
        if len(paths) > 1:
            issues.append(
                {
                    "severity": "warning",
                    "type": "path_id_sanitization_collision",
                    "base_image_id": base_id,
                    "root_relative_paths": paths,
                    "resolution": "numeric __dupN suffix without hashes",
                }
            )

    repairable_count = sum(1 for item in issues if item["severity"] == "repairable")
    blocking_count = sum(1 for item in issues if item["severity"] == "blocking")
    return {
        "schema_version": 1,
        "tool_version": 7,
        "created_at": _iso_timestamp(),
        "image_root": str(image_root_path),
        "annotation_root": str(annotation_root_path),
        "annotation_file_count": len({str(item["annotation_file"]) for item in contexts}),
        "record_count": len(contexts),
        "repairable_count": repairable_count,
        "blocking_count": blocking_count,
        "warning_count": sum(1 for item in issues if item["severity"] == "warning"),
        "can_repair": repairable_count > 0,
        "can_export": blocking_count == 0,
        "compute_sha": bool(compute_sha),
        "path_identity_map": [
            {
                "root_relative_path": root_relative,
                "image_id": image_id,
            }
            for root_relative, image_id in sorted(identity_map.items())
        ],
        "files": list(file_summaries.values()),
        "issues": issues,
    }


def write_metadata_audit_report(
    audit: Mapping,
    annotation_root: str | os.PathLike,
    timestamp: str | None = None,
) -> Path:
    audit_dir = Path(annotation_root) / "metadata_audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    stamp = timestamp or _file_timestamp()
    path = audit_dir / f"metadata_audit_{stamp}.json"
    _atomic_json(path, dict(audit))
    return path


def repair_annotation_tree(
    image_root: str | os.PathLike,
    annotation_root: str | os.PathLike,
    base_width: int | None = None,
    base_height: int | None = None,
    compute_sha: bool = False,
    audit: Mapping | None = None,
) -> dict:
    image_root_path = Path(image_root)
    annotation_root_path = Path(annotation_root)
    stamp = _file_timestamp()
    before_audit = dict(
        audit
        or audit_annotation_tree(
            image_root_path,
            annotation_root_path,
            base_width,
            base_height,
            compute_sha=compute_sha,
        )
    )
    contexts, load_warnings = _collect_contexts(
        image_root_path, annotation_root_path
    )
    if load_warnings:
        raise ValueError("; ".join(item["message"] for item in load_warnings))
    identity_map, _collision_groups = _path_identity_map(contexts)
    grouped: dict[Path, list[dict]] = defaultdict(list)
    for context in contexts:
        grouped[context["annotation_file"]].append(context)

    backup_paths: list[str] = []
    rewritten_files: list[str] = []
    mapping_rows: list[dict] = []
    audit_dir = annotation_root_path / "metadata_audit"
    audit_dir.mkdir(parents=True, exist_ok=True)

    for annotation_file, file_contexts in sorted(grouped.items()):
        payload = _read_json(annotation_file)
        images = payload.get("images")
        if not isinstance(images, dict):
            continue
        new_images: dict[str, dict] = {}
        changed = False
        for context in file_contexts:
            record = dict(context["record"])
            original_record = dict(record)
            expected_id = identity_map[context["root_relative_path"]]
            reasons: list[str] = []

            previous_image_id = str(record.get("image_id") or "")
            if previous_image_id != expected_id:
                if previous_image_id and "previous_image_id" not in record:
                    record["previous_image_id"] = previous_image_id
                record["image_id"] = expected_id
                reasons.append("image_id_path_identity_migration")

            if record.get("relative_path") != context["record_relative_path"]:
                record["relative_path"] = context["record_relative_path"]
                reasons.append("relative_path_normalization")

            if context["dataset"]:
                if record.get("dataset") != context["dataset"]:
                    record["dataset"] = context["dataset"]
                    reasons.append("dataset_normalization")
                if record.get("source_relative_path") != context["source_relative_path"]:
                    record["source_relative_path"] = context["source_relative_path"]
                    reasons.append("source_relative_path_normalization")

            plan = _current_padding_plan(
                context,
                base_width,
                base_height,
            )
            if plan is not None:
                metadata_updates = {
                    "original_width": plan.original_width,
                    "original_height": plan.original_height,
                    "padded_width": plan.padded_width,
                    "padded_height": plan.padded_height,
                    "pad_right": plan.pad_right,
                    "pad_bottom": plan.pad_bottom,
                    "padding_mode": "reflect",
                    "padding_counted_as_valid_area": False,
                    "base_width": plan.base_width,
                    "base_height": plan.base_height,
                    "rows": plan.rows,
                    "cols": plan.cols,
                }
                for key, value in metadata_updates.items():
                    if record.get(key) != value:
                        record[key] = value
                        reasons.append("grid_metadata_refresh")

            if compute_sha and context["image_path"].exists():
                actual_sha = sha256_file(context["image_path"])
                previous_sha = str(record.get("sha256") or "")
                if previous_sha != actual_sha:
                    if previous_sha and "previous_sha256" not in record:
                        record["previous_sha256"] = previous_sha
                    record["sha256"] = actual_sha
                    reasons.append("sha256_metadata_refresh")

            if reasons:
                record["metadata_repaired"] = True
                record["metadata_repaired_at"] = _iso_timestamp()
                record["metadata_repair_reason"] = ",".join(sorted(set(reasons)))
                changed = True
                mapping_rows.append(
                    {
                        "annotation_file": str(annotation_file),
                        "root_relative_path": context["root_relative_path"],
                        "dataset": context["dataset"],
                        "source_relative_path": context["source_relative_path"],
                        "previous_image_id": previous_image_id,
                        "new_image_id": expected_id,
                    }
                )

            if record != original_record:
                changed = True
            new_images[context["record_relative_path"]] = record

        if changed:
            backup = _backup_annotation_file(
                annotation_file, annotation_root_path, audit_dir, stamp
            )
            backup_paths.append(str(backup))
            payload["images"] = new_images
            payload["schema_version"] = max(int(payload.get("schema_version", 0) or 0), 6)
            payload["tool_version"] = 7
            payload["metadata_repaired_at"] = _iso_timestamp()
            _atomic_json(annotation_file, payload)
            rewritten_files.append(str(annotation_file))

    mapping_path = audit_dir / f"image_id_mapping_{stamp}.json"
    _atomic_json(
        mapping_path,
        {
            "schema_version": 1,
            "tool_version": 7,
            "created_at": _iso_timestamp(),
            "rows": mapping_rows,
        },
    )
    after_audit = audit_annotation_tree(
        image_root_path,
        annotation_root_path,
        base_width,
        base_height,
        compute_sha=compute_sha,
    )
    repair_report = {
        "schema_version": 1,
        "tool_version": 7,
        "created_at": _iso_timestamp(),
        "image_root": str(image_root_path),
        "annotation_root": str(annotation_root_path),
        "before": {
            "record_count": before_audit.get("record_count", 0),
            "repairable_count": before_audit.get("repairable_count", 0),
            "blocking_count": before_audit.get("blocking_count", 0),
        },
        "after": {
            "record_count": after_audit.get("record_count", 0),
            "repairable_count": after_audit.get("repairable_count", 0),
            "blocking_count": after_audit.get("blocking_count", 0),
        },
        "rewritten_files": rewritten_files,
        "backup_files": backup_paths,
        "mapping_file": str(mapping_path),
        "blocking_issues_after_repair": [
            item for item in after_audit["issues"] if item["severity"] == "blocking"
        ],
    }
    repair_path = audit_dir / f"metadata_repair_{stamp}.json"
    _atomic_json(repair_path, repair_report)
    repair_report["repair_report"] = str(repair_path)
    repair_report["after_audit"] = after_audit
    return repair_report


def _audit_one_record(
    context: Mapping,
    expected_id: str,
    base_width: int | None,
    base_height: int | None,
    compute_sha: bool,
) -> list[dict]:
    record = context["record"]
    issues: list[dict] = []
    common = {
        "annotation_file": str(context["annotation_file"]),
        "root_relative_path": context["root_relative_path"],
        "dataset": context["dataset"],
        "source_relative_path": context["source_relative_path"],
    }
    current_id = str(record.get("image_id") or "")
    if current_id != expected_id:
        issues.append(
            {
                "severity": "repairable",
                "type": "image_id_not_path_based",
                "current_image_id": current_id,
                "expected_image_id": expected_id,
                **common,
            }
        )
    if record.get("relative_path") != context["record_relative_path"]:
        issues.append(
            {
                "severity": "repairable",
                "type": "relative_path_not_canonical",
                "current_relative_path": record.get("relative_path"),
                "expected_relative_path": context["record_relative_path"],
                **common,
            }
        )
    if context["dataset"] and record.get("source_relative_path") != context["source_relative_path"]:
        issues.append(
            {
                "severity": "repairable",
                "type": "source_relative_path_not_canonical",
                "current_source_relative_path": record.get("source_relative_path"),
                "expected_source_relative_path": context["source_relative_path"],
                **common,
            }
        )

    image_path = context["image_path"]
    if not image_path.exists():
        issues.append(
            {
                "severity": "blocking",
                "type": "missing_image_file",
                "image_path": str(image_path),
                **common,
            }
        )
        return issues

    labels = record.get("labels")
    if not isinstance(labels, list) or not labels:
        issues.append({"severity": "blocking", "type": "missing_labels", **common})
        return issues

    plan = _current_padding_plan(context, base_width, base_height)
    if plan is None:
        issues.append(
            {
                "severity": "blocking",
                "type": "missing_base_grid_size",
                **common,
            }
        )
        return issues
    if len(labels) != plan.rows or any(
        not isinstance(row, list) or len(row) != plan.cols for row in labels
    ):
        issues.append(
            {
                "severity": "blocking",
                "type": "label_grid_mismatch",
                "expected_rows": plan.rows,
                "expected_cols": plan.cols,
                "actual_rows": len(labels),
                "actual_cols": [
                    len(row) if isinstance(row, list) else None for row in labels[:5]
                ],
                **common,
            }
        )
        return issues

    for key, expected in (
        ("original_width", plan.original_width),
        ("original_height", plan.original_height),
        ("rows", plan.rows),
        ("cols", plan.cols),
        ("base_width", plan.base_width),
        ("base_height", plan.base_height),
    ):
        if record.get(key) is not None and record.get(key) != expected:
            issues.append(
                {
                    "severity": "repairable",
                    "type": "grid_metadata_mismatch",
                    "field": key,
                    "current": record.get(key),
                    "expected": expected,
                    **common,
                }
            )

    if compute_sha:
        actual_sha = sha256_file(image_path)
        if str(record.get("sha256") or "") != actual_sha:
            issues.append(
                {
                    "severity": "repairable",
                    "type": "sha256_metadata_mismatch",
                    "current_sha256": record.get("sha256"),
                    "expected_sha256": actual_sha,
                    **common,
                }
            )
    return issues


def _collect_contexts(
    image_root: Path,
    annotation_root: Path,
) -> tuple[list[dict], list[dict]]:
    contexts: list[dict] = []
    warnings: list[dict] = []
    for annotation_file, dataset_prefix in _iter_annotation_files(annotation_root):
        try:
            payload = _read_json(annotation_file)
        except (OSError, json.JSONDecodeError) as exc:
            warnings.append(
                {
                    "annotation_file": str(annotation_file),
                    "message": f"{annotation_file}: {exc}",
                }
            )
            continue
        images = payload.get("images")
        if not isinstance(images, dict):
            warnings.append(
                {
                    "annotation_file": str(annotation_file),
                    "message": f"{annotation_file}: missing images object",
                }
            )
            continue
        for key, value in images.items():
            if not isinstance(value, Mapping):
                continue
            contexts.append(
                _record_context(
                    image_root,
                    annotation_root,
                    annotation_file,
                    dataset_prefix,
                    str(key),
                    dict(value),
                    payload,
                )
            )
    return contexts, warnings


def _iter_annotation_files(annotation_root: Path) -> list[tuple[Path, str]]:
    files: list[tuple[Path, str]] = []
    root_file = annotation_root / "annotations.json"
    if root_file.exists():
        files.append((root_file, ""))
    if annotation_root.exists():
        for child in sorted(path for path in annotation_root.iterdir() if path.is_dir()):
            if child.name.lower() in SKIP_ANNOTATION_DIRS:
                continue
            annotation_file = child / "annotations.json"
            if annotation_file.exists():
                files.append((annotation_file, child.name))
    return files


def _record_context(
    image_root: Path,
    annotation_root: Path,
    annotation_file: Path,
    dataset_prefix: str,
    key: str,
    record: dict,
    file_payload: Mapping,
) -> dict:
    raw_relative = str(record.get("relative_path") or key).replace("\\", "/").strip("/")
    raw_dataset = str(record.get("dataset") or dataset_prefix).replace("\\", "/").strip("/")
    raw_source = str(record.get("source_relative_path") or "").replace("\\", "/").strip("/")

    if dataset_prefix:
        dataset = dataset_prefix
        source_relative = raw_source or raw_relative
        if source_relative.startswith(f"{dataset}/"):
            source_relative = source_relative[len(dataset) + 1 :]
        record_relative = source_relative
        root_relative = f"{dataset}/{source_relative}".strip("/")
    else:
        dataset = raw_dataset
        if dataset and raw_source:
            source_relative = raw_source
            root_relative = f"{dataset}/{source_relative}".strip("/")
        else:
            root_relative = raw_relative
            parts = root_relative.split("/", 1)
            if len(parts) == 2:
                dataset = dataset or parts[0]
                source_relative = raw_source or parts[1]
            else:
                source_relative = raw_source or root_relative
        record_relative = root_relative

    return {
        "annotation_root": annotation_root,
        "annotation_file": annotation_file,
        "dataset_prefix": dataset_prefix,
        "record_key": key,
        "record": record,
        "file_payload": file_payload,
        "dataset": dataset,
        "source_relative_path": source_relative,
        "root_relative_path": root_relative,
        "record_relative_path": record_relative,
        "image_path": image_root / root_relative,
    }


def _path_identity_map(contexts: list[dict]) -> tuple[dict[str, str], dict[str, list[str]]]:
    roots = sorted({context["root_relative_path"] for context in contexts})
    groups: dict[str, list[str]] = defaultdict(list)
    for root_relative in roots:
        groups[path_based_image_id(root_relative)].append(root_relative)

    result: dict[str, str] = {}
    used: set[str] = set()
    for base_id, paths in sorted(groups.items()):
        suffix = 1
        for index, root_relative in enumerate(sorted(paths), start=1):
            candidate = base_id if index == 1 else f"{base_id}__dup{index}"
            while candidate in used:
                suffix += 1
                candidate = f"{base_id}__dup{suffix}"
            used.add(candidate)
            result[root_relative] = candidate
    return result, dict(groups)


def _duplicate_image_ids(contexts: list[dict]) -> dict[str, list[str]]:
    groups: dict[str, set[str]] = defaultdict(set)
    for context in contexts:
        image_id = str(context["record"].get("image_id") or "")
        if image_id:
            groups[image_id].add(context["root_relative_path"])
    return {key: sorted(value) for key, value in groups.items() if len(value) > 1}


def _current_padding_plan(
    context: Mapping,
    base_width: int | None,
    base_height: int | None,
):
    record = context["record"]
    file_payload = context["file_payload"]
    resolved_base_width = (
        record.get("base_width") or file_payload.get("base_width") or base_width
    )
    resolved_base_height = (
        record.get("base_height") or file_payload.get("base_height") or base_height
    )
    if not resolved_base_width or not resolved_base_height:
        return None
    image_path = context["image_path"]
    try:
        with Image.open(image_path) as probe:
            width, height = probe.size
    except OSError:
        return None
    return make_padding_plan(width, height, int(resolved_base_width), int(resolved_base_height))


def _backup_annotation_file(
    annotation_file: Path,
    annotation_root: Path,
    audit_dir: Path,
    stamp: str,
) -> Path:
    try:
        relative_parent = annotation_file.parent.relative_to(annotation_root).as_posix()
    except ValueError:
        relative_parent = annotation_file.parent.name
    if relative_parent in ("", "."):
        name = f"annotations.backup_{stamp}.json"
    else:
        name = f"{_safe_file_part(relative_parent)}__annotations.backup_{stamp}.json"
    backup = audit_dir / name
    shutil.copy2(annotation_file, backup)
    return backup


def _safe_file_part(value: str) -> str:
    safe = value.replace("\\", "__").replace("/", "__")
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in safe)
    return safe.strip("_") or "root"


def _read_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        loaded = json.load(handle)
    if not isinstance(loaded, dict):
        raise ValueError(f"{path}: JSON root must be an object")
    return loaded


def _atomic_json(path: Path, payload: Mapping) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    os.replace(temporary, path)


def _iso_timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _file_timestamp() -> str:
    return time.strftime("%Y%m%d_%H%M%S")
