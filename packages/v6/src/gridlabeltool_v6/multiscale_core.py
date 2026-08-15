"""Pure multi-scale aggregation and export logic for LabelTool v6."""

from __future__ import annotations

import json
import math
import os
import shutil
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence

from PIL import Image


LABELS = ("0", "1", "2", "3")
RISK_PRIORITY_MODE = "risk_priority"
MODE_THRESHOLDS = {
    "safety_first": {"cloth": 0.25, "multi": 0.30, "wrinkle": 0.15},
    "balanced": {"cloth": 0.50, "multi": 0.50, "wrinkle": 0.30},
    "precision_first": {"cloth": 0.70, "multi": 0.70, "wrinkle": 0.50},
}


@dataclass(frozen=True)
class PaddingPlan:
    original_width: int
    original_height: int
    padded_width: int
    padded_height: int
    pad_right: int
    pad_bottom: int
    base_width: int
    base_height: int
    rows: int
    cols: int


@dataclass(frozen=True)
class ScaleSpec:
    kernel: int
    stride: int

    def validate(self, rows: int | None = None, cols: int | None = None) -> None:
        if self.kernel < 1:
            raise ValueError("kernel must be >= 1")
        if self.stride < 1 or self.stride > self.kernel:
            raise ValueError("stride must satisfy 1 <= stride <= kernel")
        if rows is not None and self.kernel > rows:
            raise ValueError(f"kernel {self.kernel} exceeds grid rows {rows}")
        if cols is not None and self.kernel > cols:
            raise ValueError(f"kernel {self.kernel} exceeds grid cols {cols}")


def make_padding_plan(width: int, height: int, base_width: int, base_height: int) -> PaddingPlan:
    values = (width, height, base_width, base_height)
    if any(int(value) <= 0 for value in values):
        raise ValueError("image and base dimensions must be positive")
    padded_width = int(math.ceil(width / base_width) * base_width)
    padded_height = int(math.ceil(height / base_height) * base_height)
    return PaddingPlan(
        original_width=width,
        original_height=height,
        padded_width=padded_width,
        padded_height=padded_height,
        pad_right=padded_width - width,
        pad_bottom=padded_height - height,
        base_width=base_width,
        base_height=base_height,
        rows=padded_height // base_height,
        cols=padded_width // base_width,
    )


def reflect_pad_image(image: Image.Image, plan: PaddingPlan) -> Image.Image:
    """Mirror-pad right/bottom while keeping the original origin at (0, 0)."""
    if image.size != (plan.original_width, plan.original_height):
        raise ValueError("image dimensions do not match padding plan")
    if plan.pad_right >= plan.original_width or plan.pad_bottom >= plan.original_height:
        raise ValueError("base block must not exceed the original image dimensions")

    padded = Image.new(image.mode, (plan.padded_width, plan.padded_height))
    padded.paste(image, (0, 0))
    if plan.pad_right:
        right = image.crop(
            (plan.original_width - plan.pad_right, 0, plan.original_width, plan.original_height)
        ).transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        padded.paste(right, (plan.original_width, 0))
    if plan.pad_bottom:
        bottom = padded.crop(
            (0, plan.original_height - plan.pad_bottom, plan.padded_width, plan.original_height)
        ).transpose(Image.Transpose.FLIP_TOP_BOTTOM)
        padded.paste(bottom, (0, plan.original_height))
    return padded


def valid_area_grid(plan: PaddingPlan) -> list[list[int]]:
    """Return real-image pixel area for each base cell; mirrored area has weight zero."""
    areas: list[list[int]] = []
    for row in range(plan.rows):
        y1 = row * plan.base_height
        real_h = max(0, min(plan.base_height, plan.original_height - y1))
        row_areas = []
        for col in range(plan.cols):
            x1 = col * plan.base_width
            real_w = max(0, min(plan.base_width, plan.original_width - x1))
            row_areas.append(real_w * real_h)
        areas.append(row_areas)
    return areas


def window_starts(length: int, kernel: int, stride: int) -> list[int]:
    """Cover a dimension and append the last edge-aligned start when necessary."""
    if length < 1:
        raise ValueError("length must be positive")
    spec = ScaleSpec(kernel, stride)
    spec.validate(length, length)
    last = length - kernel
    starts = list(range(0, last + 1, stride))
    if starts[-1] != last:
        starts.append(last)
    return starts


def window_positions(rows: int, cols: int, spec: ScaleSpec) -> list[tuple[int, int]]:
    spec.validate(rows, cols)
    return [
        (row, col)
        for row in window_starts(rows, spec.kernel, spec.stride)
        for col in window_starts(cols, spec.kernel, spec.stride)
    ]


def expected_window_count(rows: int, cols: int, spec: ScaleSpec) -> int:
    spec.validate(rows, cols)
    return len(window_starts(rows, spec.kernel, spec.stride)) * len(
        window_starts(cols, spec.kernel, spec.stride)
    )


def normalize_thresholds(thresholds: Mapping[str, float]) -> dict[str, float]:
    result = {}
    for name in ("cloth", "multi", "wrinkle"):
        if name not in thresholds:
            raise ValueError(f"missing threshold: {name}")
        value = float(thresholds[name])
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"{name} threshold must be in [0, 1]")
        result[name] = value
    return result


def resolve_profile_name(source_mode: str, thresholds: Mapping[str, float]) -> str:
    actual = normalize_thresholds(thresholds)
    default = MODE_THRESHOLDS.get(source_mode)
    if default is None:
        return "custom"
    if all(math.isclose(actual[key], default[key], abs_tol=1e-12) for key in actual):
        return source_mode
    return "custom"


def aggregate_window(
    labels: Sequence[Sequence[str]],
    valid_areas: Sequence[Sequence[int]],
    row: int,
    col: int,
    kernel: int,
    thresholds: Mapping[str, float],
) -> dict:
    thresholds = normalize_thresholds(thresholds)
    areas = {label: 0 for label in LABELS}
    total_capacity = 0

    if not labels or not labels[0]:
        raise ValueError("labels cannot be empty")
    for r in range(row, row + kernel):
        if r >= len(labels) or r >= len(valid_areas):
            raise ValueError("window exceeds label grid")
        for c in range(col, col + kernel):
            if c >= len(labels[r]) or c >= len(valid_areas[r]):
                raise ValueError("window exceeds label grid")
            label = str(labels[r][c])
            if label not in areas:
                raise ValueError(f"unsupported label {label!r}; expected 0, 1, 2, or 3")
            area = int(valid_areas[r][c])
            if area < 0:
                raise ValueError("valid area cannot be negative")
            areas[label] += area
            total_capacity += max(valid_areas[r][c], 0)

    total = sum(areas.values())
    if total <= 0:
        raise ValueError("window has no valid original-image area")

    cloth_area = areas["1"] + areas["2"] + areas["3"]
    multi_area = areas["2"] + areas["3"]
    p_cloth = cloth_area / total
    p_multi = multi_area / cloth_area if cloth_area else 0.0
    p_wrinkle = areas["3"] / multi_area if multi_area else 0.0

    if p_cloth < thresholds["cloth"]:
        label = "0"
        confidence = 1.0 - p_cloth
        relevant = (p_cloth - thresholds["cloth"],)
    elif p_multi < thresholds["multi"]:
        label = "1"
        confidence = min(p_cloth, 1.0 - p_multi)
        relevant = (
            p_cloth - thresholds["cloth"],
            p_multi - thresholds["multi"],
        )
    elif p_wrinkle < thresholds["wrinkle"]:
        label = "2"
        confidence = min(p_cloth, p_multi, 1.0 - p_wrinkle)
        relevant = (
            p_cloth - thresholds["cloth"],
            p_multi - thresholds["multi"],
            p_wrinkle - thresholds["wrinkle"],
        )
    else:
        label = "3"
        confidence = min(p_cloth, p_multi, p_wrinkle)
        relevant = (
            p_cloth - thresholds["cloth"],
            p_multi - thresholds["multi"],
            p_wrinkle - thresholds["wrinkle"],
        )

    area_ratios = {key: value / total for key, value in areas.items()}
    threshold_margin = min(abs(value) for value in relevant)
    tie = any(math.isclose(value, 0.0, abs_tol=1e-12) for value in relevant)
    return {
        "label": label,
        "areas": areas,
        "area_ratios": area_ratios,
        "p_cloth": p_cloth,
        "p_multi": p_multi,
        "p_wrinkle": p_wrinkle,
        "confidence": confidence,
        "threshold_margin": threshold_margin,
        "tie": tie,
        "valid_area": total,
        "valid_ratio": 1.0,
        "aggregation_rule": "hierarchical_threshold",
    }


def aggregate_risk_priority_window(
    labels: Sequence[Sequence[str]],
    valid_areas: Sequence[Sequence[int]],
    row: int,
    col: int,
    kernel: int,
) -> dict:
    """Select the highest-risk label that occupies non-zero real-image area."""
    areas = {label: 0 for label in LABELS}
    if not labels or not labels[0]:
        raise ValueError("labels cannot be empty")
    for r in range(row, row + kernel):
        if r >= len(labels) or r >= len(valid_areas):
            raise ValueError("window exceeds label grid")
        for c in range(col, col + kernel):
            if c >= len(labels[r]) or c >= len(valid_areas[r]):
                raise ValueError("window exceeds label grid")
            label = str(labels[r][c])
            if label not in areas:
                raise ValueError(f"unsupported label {label!r}; expected 0, 1, 2, or 3")
            area = int(valid_areas[r][c])
            if area < 0:
                raise ValueError("valid area cannot be negative")
            areas[label] += area

    total = sum(areas.values())
    if total <= 0:
        raise ValueError("window has no valid original-image area")
    cloth_area = areas["1"] + areas["2"] + areas["3"]
    multi_area = areas["2"] + areas["3"]
    p_cloth = cloth_area / total
    p_multi = multi_area / cloth_area if cloth_area else 0.0
    p_wrinkle = areas["3"] / multi_area if multi_area else 0.0
    label = next(item for item in reversed(LABELS) if areas[item] > 0)
    selected_area_ratio = areas[label] / total
    return {
        "label": label,
        "areas": areas,
        "area_ratios": {key: value / total for key, value in areas.items()},
        "p_cloth": p_cloth,
        "p_multi": p_multi,
        "p_wrinkle": p_wrinkle,
        "confidence": selected_area_ratio,
        "selected_area_ratio": selected_area_ratio,
        "threshold_margin": None,
        "tie": False,
        "valid_area": total,
        "valid_ratio": 1.0,
        "aggregation_rule": "highest_present_label",
    }


def _scale_dir_name(spec: ScaleSpec, base_width: int, base_height: int) -> str:
    return (
        f"kernel_{spec.kernel}_stride_{spec.stride}_"
        f"size_{base_width * spec.kernel}x{base_height * spec.kernel}"
    )


def _remove_same_sample_from_classes(layer_root: Path, filename: str) -> None:
    for label in LABELS:
        candidate = layer_root / label / filename
        if candidate.exists():
            candidate.unlink()


def _link_or_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.unlink()
    try:
        os.link(source, target)
    except OSError:
        shutil.copy2(source, target)


def _safe_image_stem(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in value)
    return cleaned.strip("_") or "image"


def _safe_path_part(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in value)
    return cleaned.strip("._") or "dataset"


def _record_dataset(record: Mapping) -> tuple[str, str]:
    raw = str(record.get("dataset") or record.get("material") or "").strip()
    return raw, _safe_path_part(raw) if raw else ""


def export_multiscale(
    records: Iterable[Mapping],
    output_folder: str | os.PathLike,
    base_width: int,
    base_height: int,
    scale_specs: Sequence[ScaleSpec],
    profiles: Sequence[Mapping],
    progress: Callable[[int, int, str], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> dict:
    """Export all selected scales/modes and write a manifest plus summary per scale."""
    records = list(records)
    if not records:
        raise ValueError("no annotated image records to export")
    if not scale_specs:
        raise ValueError("at least one scale spec is required")
    if not profiles:
        raise ValueError("at least one aggregation profile is required")

    normalized_profiles = []
    used_names = set()
    for profile in profiles:
        source_mode = str(profile["source_mode"])
        if source_mode == RISK_PRIORITY_MODE:
            thresholds = None
            profile_name = RISK_PRIORITY_MODE
            aggregation_rule = "highest_present_label"
        else:
            thresholds = normalize_thresholds(profile["thresholds"])
            profile_name = resolve_profile_name(source_mode, thresholds)
            aggregation_rule = "hierarchical_threshold"
        if profile_name in used_names:
            raise ValueError(
                "multiple selected profiles resolve to the same output name; "
                "only one modified custom profile can be exported at a time"
            )
        used_names.add(profile_name)
        normalized_profiles.append(
            {
                "name": profile_name,
                "source_mode": source_mode,
                "thresholds": thresholds,
                "aggregation_rule": aggregation_rule,
            }
        )

    total = 0
    prepared_records = []
    for record in records:
        image_path = Path(record["image_path"])
        with Image.open(image_path) as probe:
            width, height = probe.size
        plan = make_padding_plan(width, height, base_width, base_height)
        labels = record["labels"]
        if len(labels) != plan.rows or any(len(row) != plan.cols for row in labels):
            raise ValueError(
                f"{image_path.name}: label grid is not {plan.rows}x{plan.cols} "
                f"for base block {base_width}x{base_height}"
            )
        valid = valid_area_grid(plan)
        applicable = []
        for spec in scale_specs:
            spec.validate(plan.rows, plan.cols)
            count = expected_window_count(plan.rows, plan.cols, spec)
            total += count * len(normalized_profiles)
            applicable.append(spec)
        prepared_records.append((record, plan, valid, applicable))

    root = Path(output_folder) / "exports"
    root.mkdir(parents=True, exist_ok=True)
    manifest_handles = {}
    stats = {}
    processed = 0
    try:
        for profile in normalized_profiles:
            for spec in scale_specs:
                scale_name = _scale_dir_name(spec, base_width, base_height)
                scale_root = root / profile["name"] / scale_name
                datasets = sorted(
                    {
                        _record_dataset(record)[1]
                        for record, _plan, _valid, _applicable in prepared_records
                        if _record_dataset(record)[1]
                    }
                )
                if datasets:
                    for dataset in datasets:
                        layer_root = scale_root / dataset / "layer_4"
                        for label in LABELS:
                            (layer_root / label).mkdir(parents=True, exist_ok=True)
                else:
                    layer_root = scale_root / "layer_4"
                    for label in LABELS:
                        (layer_root / label).mkdir(parents=True, exist_ok=True)
                manifest_path = scale_root / "manifest.jsonl"
                manifest_handles[(profile["name"], spec.kernel, spec.stride)] = manifest_path.open(
                    "w", encoding="utf-8", newline="\n"
                )
                stats[(profile["name"], spec.kernel, spec.stride)] = {
                    "profile": profile,
                    "scale": {
                        "kernel": spec.kernel,
                        "stride": spec.stride,
                        "width": base_width * spec.kernel,
                        "height": base_height * spec.kernel,
                    },
                    "class_counts": Counter(),
                    "dataset_counts": Counter(),
                    "dataset_class_counts": {},
                    "confidence_sum": 0.0,
                    "tie_count": 0,
                    "sample_count": 0,
                }

        for record, plan, valid, applicable in prepared_records:
            if cancelled and cancelled():
                break
            image_path = Path(record["image_path"])
            image_id = str(record.get("image_id") or image_path.stem)
            safe_stem = _safe_image_stem(image_id)
            dataset_name, dataset_folder = _record_dataset(record)
            labels = record["labels"]
            with Image.open(image_path) as opened:
                padded = reflect_pad_image(opened.convert("RGB"), plan)
                for spec in applicable:
                    for row, col in window_positions(plan.rows, plan.cols, spec):
                        if cancelled and cancelled():
                            break
                        x1 = col * base_width
                        y1 = row * base_height
                        x2 = x1 + spec.kernel * base_width
                        y2 = y1 + spec.kernel * base_height
                        filename = (
                            f"{safe_stem}_r{row:04d}_c{col:04d}_"
                            f"k{spec.kernel}_s{spec.stride}.png"
                        )
                        crop = padded.crop((x1, y1, x2, y2))
                        first_path = None
                        for profile in normalized_profiles:
                            if profile["aggregation_rule"] == "highest_present_label":
                                result = aggregate_risk_priority_window(
                                    labels, valid, row, col, spec.kernel
                                )
                            else:
                                result = aggregate_window(
                                    labels,
                                    valid,
                                    row,
                                    col,
                                    spec.kernel,
                                    profile["thresholds"],
                                )
                            full_area = (
                                spec.kernel
                                * base_width
                                * spec.kernel
                                * base_height
                            )
                            result["valid_ratio"] = result["valid_area"] / full_area
                            scale_name = _scale_dir_name(spec, base_width, base_height)
                            scale_root = root / profile["name"] / scale_name
                            sample_root = scale_root / dataset_folder if dataset_folder else scale_root
                            layer_root = sample_root / "layer_4"
                            _remove_same_sample_from_classes(layer_root, filename)
                            target = layer_root / result["label"] / filename
                            if first_path is None:
                                target.parent.mkdir(parents=True, exist_ok=True)
                                crop.save(target, format="PNG")
                                first_path = target
                            else:
                                _link_or_copy(first_path, target)

                            manifest_record = {
                                "schema_version": 2,
                                "sample_id": filename[:-4],
                                "image_id": image_id,
                                "dataset": dataset_name,
                                "source_image": str(image_path),
                                "source_relative_path": record.get("source_relative_path"),
                                "source_sha256": record.get("sha256"),
                                "window": {
                                    "row": row,
                                    "col": col,
                                    "kernel": spec.kernel,
                                    "stride": spec.stride,
                                    "x": x1,
                                    "y": y1,
                                    "width": x2 - x1,
                                    "height": y2 - y1,
                                },
                                "original_size": [plan.original_width, plan.original_height],
                                "padded_size": [plan.padded_width, plan.padded_height],
                                "padding": {
                                    "right": plan.pad_right,
                                    "bottom": plan.pad_bottom,
                                    "mode": "reflect",
                                    "counted_as_valid_area": False,
                                },
                                "label": result["label"],
                                "areas": result["areas"],
                                "area_ratios": result["area_ratios"],
                                "p_cloth": result["p_cloth"],
                                "p_multi": result["p_multi"],
                                "p_wrinkle": result["p_wrinkle"],
                                "confidence": result["confidence"],
                                "selected_area_ratio": result.get("selected_area_ratio"),
                                "threshold_margin": result["threshold_margin"],
                                "tie": result["tie"],
                                "valid_ratio": result["valid_ratio"],
                                "aggregation_mode": profile["name"],
                                "aggregation_rule": profile["aggregation_rule"],
                                "source_mode": profile["source_mode"],
                                "thresholds": profile["thresholds"],
                                "relative_path": str(target.relative_to(root)).replace("\\", "/"),
                            }
                            key = (profile["name"], spec.kernel, spec.stride)
                            handle = manifest_handles[key]
                            handle.write(json.dumps(manifest_record, ensure_ascii=False) + "\n")
                            stat = stats[key]
                            stat["class_counts"][result["label"]] += 1
                            stat["dataset_counts"][dataset_name] += 1
                            stat["dataset_class_counts"].setdefault(
                                dataset_name, Counter()
                            )[result["label"]] += 1
                            stat["confidence_sum"] += result["confidence"]
                            stat["tie_count"] += int(result["tie"])
                            stat["sample_count"] += 1
                            processed += 1
                            if progress:
                                progress(processed, total, f"{profile['name']} / {scale_name}")
                    if cancelled and cancelled():
                        break
            if cancelled and cancelled():
                break
    finally:
        for handle in manifest_handles.values():
            handle.close()

    summaries = {}
    for (profile_name, kernel, stride), stat in stats.items():
        count = stat["sample_count"]
        class_counts = {label: int(stat["class_counts"].get(label, 0)) for label in LABELS}
        dataset_counts = {
            dataset: int(value)
            for dataset, value in sorted(stat["dataset_counts"].items())
            if dataset
        }
        dataset_class_counts = {
            dataset: {label: int(counter.get(label, 0)) for label in LABELS}
            for dataset, counter in sorted(stat["dataset_class_counts"].items())
            if dataset
        }
        summary = {
            "profile": stat["profile"],
            "scale": stat["scale"],
            "sample_count": count,
            "class_counts": class_counts,
            "dataset_counts": dataset_counts,
            "dataset_class_counts": dataset_class_counts,
            "class_ratios": {
                label: (class_counts[label] / count if count else 0.0) for label in LABELS
            },
            "average_confidence": stat["confidence_sum"] / count if count else 0.0,
            "tie_count": stat["tie_count"],
            "tie_rate": stat["tie_count"] / count if count else 0.0,
            "cancelled": bool(cancelled and cancelled()),
        }
        scale_name = _scale_dir_name(ScaleSpec(kernel, stride), base_width, base_height)
        summary_path = root / profile_name / scale_name / "summary.json"
        with summary_path.open("w", encoding="utf-8") as handle:
            json.dump(summary, handle, ensure_ascii=False, indent=2)
        summaries[f"{profile_name}/{scale_name}"] = summary

    return {
        "processed": processed,
        "expected": total,
        "cancelled": bool(cancelled and cancelled()),
        "summaries": summaries,
    }


def parse_scale_specs(value: str) -> list[ScaleSpec]:
    specs = []
    seen = set()
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        if ":" in item:
            kernel_text, stride_text = item.split(":", 1)
        else:
            kernel_text = stride_text = item
        try:
            spec = ScaleSpec(int(kernel_text), int(stride_text))
        except ValueError as exc:
            raise ValueError(f"invalid scale item: {item!r}") from exc
        spec.validate()
        key = (spec.kernel, spec.stride)
        if key not in seen:
            specs.append(spec)
            seen.add(key)
    if not specs:
        raise ValueError("scale list cannot be empty")
    return specs
