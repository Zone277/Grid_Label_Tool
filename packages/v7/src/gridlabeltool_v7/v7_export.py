"""LabelTool v7 full/sampled multi-scale export pipeline."""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence

from PIL import Image

from .multiscale_core import (
    LABELS,
    RISK_PRIORITY_MODE,
    ScaleSpec,
    aggregate_risk_priority_window,
    aggregate_window,
    make_padding_plan,
    normalize_thresholds,
    reflect_pad_image,
    resolve_profile_name,
    valid_area_grid,
    window_positions,
)
from .metadata_repair import canonical_root_relative_from_record, path_image_id_from_record
from .training_balance import (
    attach_soft_balance_weights,
    hierarchical_targets,
    is_pure_background_window,
    select_retained_backgrounds,
)


DEFAULT_EXPORT_OPTIONS = {
    "export_full": True,
    "export_sampled": True,
    "pure_background_keep_ratio": 0.0,
    "sampling_seed": 0,
    "max_sampling_weight": 5.0,
}


def normalize_export_options(value: Mapping | None) -> dict:
    result = dict(DEFAULT_EXPORT_OPTIONS)
    if value:
        result.update(value)
    result["export_full"] = True
    result["export_sampled"] = True
    result["pure_background_keep_ratio"] = float(
        result["pure_background_keep_ratio"]
    )
    if not 0.0 <= result["pure_background_keep_ratio"] <= 1.0:
        raise ValueError("pure-background keep ratio must be in [0, 1]")
    result["sampling_seed"] = int(result["sampling_seed"])
    result["max_sampling_weight"] = float(result["max_sampling_weight"])
    if result["max_sampling_weight"] < 1.0:
        raise ValueError("maximum sampling weight must be >= 1")
    return result


def _normalize_profiles(profiles: Sequence[Mapping]) -> list[dict]:
    normalized = []
    used_names = set()
    for profile in profiles:
        source_mode = str(profile["source_mode"])
        if source_mode == RISK_PRIORITY_MODE:
            thresholds = None
            name = RISK_PRIORITY_MODE
            rule = "highest_present_label"
        else:
            thresholds = normalize_thresholds(profile["thresholds"])
            name = resolve_profile_name(source_mode, thresholds)
            rule = "hierarchical_threshold"
        if name in used_names:
            raise ValueError(
                "multiple profiles resolve to the same output name; "
                "only one modified custom profile can be exported at a time"
            )
        used_names.add(name)
        normalized.append(
            {
                "name": name,
                "source_mode": source_mode,
                "thresholds": thresholds,
                "aggregation_rule": rule,
            }
        )
    if not normalized:
        raise ValueError("at least one aggregation profile is required")
    return normalized


def _safe_part(value: str, fallback: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in value)
    return cleaned.strip("._") or fallback


def _scale_name(spec: ScaleSpec, base_width: int, base_height: int) -> str:
    return (
        f"kernel_{spec.kernel}_stride_{spec.stride}_"
        f"size_{base_width * spec.kernel}x{base_height * spec.kernel}"
    )


def _ratio_name(ratio: float) -> str:
    percent = f"{ratio * 100:.4f}".rstrip("0").rstrip(".")
    return percent.replace(".", "p") or "0"


def _atomic_json(path: Path, payload: Mapping) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    os.replace(temporary, path)


def _write_jsonl(path: Path, records: Iterable[Mapping]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(dict(record), ensure_ascii=False) + "\n")


def _link_or_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, target)
    except OSError:
        shutil.copy2(source, target)


def _labels_digest(labels: Sequence[Sequence[str]]) -> str:
    encoded = json.dumps(labels, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _unique_path_image_ids(records: Sequence[Mapping]) -> dict[int, str]:
    groups: dict[str, list[int]] = defaultdict(list)
    for index, record in enumerate(records):
        groups[path_image_id_from_record(record)].append(index)

    result: dict[int, str] = {}
    used: set[str] = set()
    for base_id, indexes in sorted(groups.items()):
        suffix = 1
        for position, index in enumerate(indexes, start=1):
            candidate = base_id if position == 1 else f"{base_id}__dup{position}"
            while candidate in used:
                suffix += 1
                candidate = f"{base_id}__dup{suffix}"
            used.add(candidate)
            result[index] = candidate
    return result


def _prepare_candidates(
    records: Sequence[Mapping],
    base_width: int,
    base_height: int,
    scale_specs: Sequence[ScaleSpec],
) -> list[dict]:
    if not records:
        raise ValueError("no annotated image records to export")
    if not scale_specs:
        raise ValueError("at least one scale spec is required")

    candidates = []
    seen_sample_ids = set()
    export_image_ids = _unique_path_image_ids(records)
    for record_index, record in enumerate(records):
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
        valid_areas = valid_area_grid(plan)
        metadata_image_id = str(record.get("image_id") or "")
        image_id = export_image_ids[record_index]
        path_identity = canonical_root_relative_from_record(record)
        safe_image_id = _safe_part(image_id, "image")
        dataset = str(record.get("dataset") or "")
        dataset_folder = _safe_part(dataset, "dataset") if dataset else ""
        for spec in scale_specs:
            spec.validate(plan.rows, plan.cols)
            for row, col in window_positions(plan.rows, plan.cols, spec):
                sample_id = (
                    f"{safe_image_id}_r{row:04d}_c{col:04d}_"
                    f"k{spec.kernel}_s{spec.stride}"
                )
                if sample_id in seen_sample_ids:
                    raise ValueError(f"duplicate sample_id: {sample_id}")
                seen_sample_ids.add(sample_id)
                candidates.append(
                    {
                        "record": record,
                        "image_path": image_path,
                        "image_id": image_id,
                        "metadata_image_id": metadata_image_id,
                        "path_identity": path_identity,
                        "dataset": dataset,
                        "dataset_folder": dataset_folder,
                        "plan": plan,
                        "valid_areas": valid_areas,
                        "labels": labels,
                        "spec": spec,
                        "kernel": spec.kernel,
                        "stride": spec.stride,
                        "row": row,
                        "col": col,
                        "sample_id": sample_id,
                        "filename": sample_id + ".png",
                        "is_pure_background": is_pure_background_window(
                            labels, valid_areas, row, col, spec.kernel
                        ),
                    }
                )
    return candidates


def _aggregate(candidate: Mapping, profile: Mapping) -> dict:
    if profile["aggregation_rule"] == "highest_present_label":
        result = aggregate_risk_priority_window(
            candidate["labels"],
            candidate["valid_areas"],
            candidate["row"],
            candidate["col"],
            candidate["kernel"],
        )
    else:
        result = aggregate_window(
            candidate["labels"],
            candidate["valid_areas"],
            candidate["row"],
            candidate["col"],
            candidate["kernel"],
            profile["thresholds"],
        )
    plan = candidate["plan"]
    full_area = (
        candidate["kernel"]
        * plan.base_width
        * candidate["kernel"]
        * plan.base_height
    )
    result["valid_ratio"] = result["valid_area"] / full_area
    return result


def _sampling_decisions(
    candidates: Sequence[Mapping],
    options: Mapping,
) -> set[str]:
    retained_backgrounds = select_retained_backgrounds(
        candidates,
        options["pure_background_keep_ratio"],
        options["sampling_seed"],
    )
    return {
        str(candidate["sample_id"])
        for candidate in candidates
        if not candidate["is_pure_background"]
        or candidate["sample_id"] in retained_backgrounds
    }


def estimate_v7_export(
    records: Iterable[Mapping],
    base_width: int,
    base_height: int,
    scale_specs: Sequence[ScaleSpec],
    profiles: Sequence[Mapping],
    export_options: Mapping | None = None,
) -> dict:
    records = list(records)
    options = normalize_export_options(export_options)
    normalized_profiles = _normalize_profiles(profiles)
    candidates = _prepare_candidates(records, base_width, base_height, scale_specs)
    retained = _sampling_decisions(candidates, options)
    dataset_counts = Counter(str(item.get("dataset") or "__root__") for item in candidates)
    scale_counts = Counter(
        _scale_name(item["spec"], base_width, base_height) for item in candidates
    )
    dataset_sampling_counts = defaultdict(
        lambda: {
            "candidate_count": 0,
            "pure_background_count": 0,
            "sampled_window_count": 0,
            "skipped_window_count": 0,
        }
    )
    for candidate in candidates:
        dataset = str(candidate.get("dataset") or "__root__")
        counts = dataset_sampling_counts[dataset]
        counts["candidate_count"] += 1
        counts["pure_background_count"] += int(candidate["is_pure_background"])
        if candidate["sample_id"] in retained:
            counts["sampled_window_count"] += 1
        else:
            counts["skipped_window_count"] += 1

    mode_class_counts = {}
    mode_dataset_class_counts = {}
    for profile in normalized_profiles:
        full_counts = Counter()
        sampled_counts = Counter()
        by_dataset = defaultdict(
            lambda: {"full": Counter(), "sampled": Counter()}
        )
        for candidate in candidates:
            label = str(_aggregate(candidate, profile)["label"])
            dataset = str(candidate.get("dataset") or "__root__")
            full_counts[label] += 1
            by_dataset[dataset]["full"][label] += 1
            if candidate["sample_id"] in retained:
                sampled_counts[label] += 1
                by_dataset[dataset]["sampled"][label] += 1
        mode_class_counts[profile["name"]] = {
            "full": {label: int(full_counts.get(label, 0)) for label in LABELS},
            "sampled": {
                label: int(sampled_counts.get(label, 0)) for label in LABELS
            },
        }
        mode_dataset_class_counts[profile["name"]] = {
            dataset: {
                variant: {
                    label: int(counters[variant].get(label, 0))
                    for label in LABELS
                }
                for variant in ("full", "sampled")
            }
            for dataset, counters in sorted(by_dataset.items())
        }
    pure_count = sum(bool(item["is_pure_background"]) for item in candidates)
    sampled_count = sum(item["sample_id"] in retained for item in candidates)
    expected = len(normalized_profiles) * (
        (len(candidates) if options["export_full"] else 0)
        + (sampled_count if options["export_sampled"] else 0)
    )
    return {
        "record_count": len(records),
        "profile_count": len(normalized_profiles),
        "candidate_count": len(candidates),
        "pure_background_count": pure_count,
        "sampled_window_count": sampled_count,
        "skipped_window_count": len(candidates) - sampled_count,
        "expected_output_samples": expected,
        "dataset_candidate_counts": dict(sorted(dataset_counts.items())),
        "scale_candidate_counts": dict(sorted(scale_counts.items())),
        "dataset_sampling_counts": {
            dataset: dict(counts)
            for dataset, counts in sorted(dataset_sampling_counts.items())
        },
        "mode_class_counts": mode_class_counts,
        "mode_dataset_class_counts": mode_dataset_class_counts,
        "options": options,
    }


def _run_signature(
    records: Sequence[Mapping],
    base_width: int,
    base_height: int,
    scale_specs: Sequence[ScaleSpec],
    profiles: Sequence[Mapping],
    options: Mapping,
) -> str:
    payload = {
        "base": [base_width, base_height],
        "scales": [[item.kernel, item.stride] for item in scale_specs],
        "profiles": profiles,
        "sampling": options,
        "records": sorted(
            [
                {
                    "image_id": path_image_id_from_record(record),
                    "metadata_image_id": str(record.get("image_id") or ""),
                    "root_relative_path": canonical_root_relative_from_record(record),
                    "sha256": str(record.get("sha256") or ""),
                    "labels_sha256": _labels_digest(record["labels"]),
                }
                for record in records
            ],
            key=lambda item: (item["root_relative_path"], item["image_id"]),
        ),
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


def _new_run_root(output_folder: str | os.PathLike, signature: str) -> Path:
    stamp = time.strftime("%Y%m%d_%H%M%S")
    base = Path(output_folder) / "exports" / f"v7_run_{stamp}_{signature}"
    candidate = base
    suffix = 2
    while candidate.exists():
        candidate = Path(str(base) + f"_{suffix}")
        suffix += 1
    candidate.mkdir(parents=True)
    return candidate


def _new_stat(
    profile: Mapping,
    spec: ScaleSpec,
    base_width: int,
    base_height: int,
) -> dict:
    return {
        "profile": dict(profile),
        "scale": {
            "kernel": spec.kernel,
            "stride": spec.stride,
            "width": base_width * spec.kernel,
            "height": base_height * spec.kernel,
        },
        "candidate_count": 0,
        "sample_count": 0,
        "pure_background_count": 0,
        "pure_background_retained": 0,
        "pure_background_skipped": 0,
        "class_counts_before": Counter(),
        "class_counts": Counter(),
        "dataset_counts": Counter(),
        "dataset_class_counts": defaultdict(Counter),
        "confidence_sum": 0.0,
        "tie_count": 0,
    }


def _manifest_record(
    candidate: Mapping,
    profile: Mapping,
    result: Mapping,
    variant: str,
    relative_path: str,
    options: Mapping,
) -> dict:
    plan = candidate["plan"]
    spec = candidate["spec"]
    x = candidate["col"] * plan.base_width
    y = candidate["row"] * plan.base_height
    return {
        "schema_version": 3,
        "tool_version": 7,
        "variant": variant,
        "sample_id": candidate["sample_id"],
        "image_id": candidate["image_id"],
        "metadata_image_id": candidate.get("metadata_image_id"),
        "path_identity": candidate.get("path_identity"),
        "dataset": candidate["dataset"],
        "source_image": str(candidate["image_path"]),
        "root_relative_path": candidate["record"].get("root_relative_path"),
        "source_relative_path": candidate["record"].get("source_relative_path"),
        "source_sha256": candidate["record"].get("sha256"),
        "base_size": [plan.base_width, plan.base_height],
        "window": {
            "row": candidate["row"],
            "col": candidate["col"],
            "kernel": spec.kernel,
            "stride": spec.stride,
            "x": x,
            "y": y,
            "width": spec.kernel * plan.base_width,
            "height": spec.kernel * plan.base_height,
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
        "hierarchical_targets": hierarchical_targets(result["label"]),
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
        "is_pure_background": bool(candidate["is_pure_background"]),
        "aggregation_mode": profile["name"],
        "aggregation_rule": profile["aggregation_rule"],
        "source_mode": profile["source_mode"],
        "thresholds": profile["thresholds"],
        "sampling": (
            {
                "pure_background_keep_ratio": options[
                    "pure_background_keep_ratio"
                ],
                "sampling_seed": options["sampling_seed"],
            }
            if variant == "sampled"
            else None
        ),
        "relative_path": relative_path,
    }


def _update_stat(
    stat: dict,
    candidate: Mapping,
    result: Mapping,
    exported: bool,
) -> None:
    label = str(result["label"])
    dataset = str(candidate.get("dataset") or "")
    stat["candidate_count"] += 1
    stat["class_counts_before"][label] += 1
    stat["pure_background_count"] += int(candidate["is_pure_background"])
    if not exported:
        stat["pure_background_skipped"] += int(candidate["is_pure_background"])
        return
    stat["sample_count"] += 1
    stat["class_counts"][label] += 1
    stat["dataset_counts"][dataset] += 1
    stat["dataset_class_counts"][dataset][label] += 1
    stat["confidence_sum"] += float(result["confidence"])
    stat["tie_count"] += int(result["tie"])
    stat["pure_background_retained"] += int(candidate["is_pure_background"])


def _finalize_summary(
    stat: Mapping,
    variant: str,
    options: Mapping,
    cancelled: bool,
) -> dict:
    count = int(stat["sample_count"])
    class_counts = {
        label: int(stat["class_counts"].get(label, 0)) for label in LABELS
    }
    before = {
        label: int(stat["class_counts_before"].get(label, 0)) for label in LABELS
    }
    return {
        "schema_version": 2,
        "tool_version": 7,
        "variant": variant,
        "profile": stat["profile"],
        "scale": stat["scale"],
        "candidate_count": int(stat["candidate_count"]),
        "sample_count": count,
        "skipped_count": int(stat["candidate_count"]) - count,
        "pure_background_count": int(stat["pure_background_count"]),
        "pure_background_retained": int(stat["pure_background_retained"]),
        "pure_background_skipped": int(stat["pure_background_skipped"]),
        "class_counts_before": before,
        "class_counts": class_counts,
        "class_ratios": {
            label: (class_counts[label] / count if count else 0.0)
            for label in LABELS
        },
        "dataset_counts": {
            dataset: int(value)
            for dataset, value in sorted(stat["dataset_counts"].items())
            if dataset
        },
        "dataset_class_counts": {
            dataset: {
                label: int(counter.get(label, 0)) for label in LABELS
            }
            for dataset, counter in sorted(stat["dataset_class_counts"].items())
            if dataset
        },
        "average_confidence": (
            stat["confidence_sum"] / count if count else 0.0
        ),
        "tie_count": int(stat["tie_count"]),
        "tie_rate": stat["tie_count"] / count if count else 0.0,
        "sampling": (
            {
                "pure_background_keep_ratio": options[
                    "pure_background_keep_ratio"
                ],
                "sampling_seed": options["sampling_seed"],
            }
            if variant == "sampled"
            else None
        ),
        "cancelled": bool(cancelled),
    }


def export_multiscale_v7(
    records: Iterable[Mapping],
    output_folder: str | os.PathLike,
    base_width: int,
    base_height: int,
    scale_specs: Sequence[ScaleSpec],
    profiles: Sequence[Mapping],
    export_options: Mapping | None = None,
    progress: Callable[[int, int, str], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> dict:
    records = list(records)
    options = normalize_export_options(export_options)
    normalized_profiles = _normalize_profiles(profiles)
    candidates = _prepare_candidates(records, base_width, base_height, scale_specs)
    retained_sampled = _sampling_decisions(candidates, options)
    signature = _run_signature(
        records,
        base_width,
        base_height,
        scale_specs,
        normalized_profiles,
        options,
    )
    run_root = _new_run_root(output_folder, signature)
    sampled_profile = (
        f"pure_bg_keep_{_ratio_name(options['pure_background_keep_ratio'])}"
        f"_seed_{options['sampling_seed']}"
    )
    sampled_base = run_root / "sampled" / sampled_profile

    expected = len(normalized_profiles) * (
        (len(candidates) if options["export_full"] else 0)
        + (
            sum(item["sample_id"] in retained_sampled for item in candidates)
            if options["export_sampled"]
            else 0
        )
    )
    config_payload = {
        "schema_version": 1,
        "tool_version": 7,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "signature": signature,
        "base_size": [base_width, base_height],
        "scale_specs": [
            {"kernel": item.kernel, "stride": item.stride}
            for item in scale_specs
        ],
        "profiles": normalized_profiles,
        "sampling": options,
        "record_count": len(records),
        "candidate_window_count": len(candidates),
        "expected_output_samples": expected,
        "training_recommendation": {
            "sampled_usage": "training_only",
            "full_usage": "validation_test_inference",
            "default_baseline": "sqrt_sampling_plus_balanced_softmax",
            "required_ablations": [
                "natural_cross_entropy",
                "pure_background_filter_cross_entropy",
                "sqrt_sampling_cross_entropy",
                "natural_balanced_softmax",
                "sqrt_sampling_balanced_softmax",
                "hierarchical_sqrt_sampling_balanced_softmax",
            ],
        },
    }
    _atomic_json(run_root / "export_config.json", config_payload)

    handles = {}
    window_handles = {}
    stats = {}
    training_records: dict[tuple[str, int, int], list[dict]] = defaultdict(list)
    dataset_folders = sorted(
        {str(item["dataset_folder"]) for item in candidates if item["dataset_folder"]}
    )

    def prepare_layer_folders(root: Path) -> None:
        folders = dataset_folders or [""]
        for dataset_folder in folders:
            layer_root = root / dataset_folder / "layer_4" if dataset_folder else root / "layer_4"
            for label in LABELS:
                (layer_root / label).mkdir(parents=True, exist_ok=True)

    for profile in normalized_profiles:
        for spec in scale_specs:
            scale = _scale_name(spec, base_width, base_height)
            key = (profile["name"], spec.kernel, spec.stride)
            if options["export_full"]:
                root = run_root / "full" / profile["name"] / scale
                root.mkdir(parents=True, exist_ok=True)
                prepare_layer_folders(root)
                handles[("full", *key)] = (root / "manifest.jsonl").open(
                    "w", encoding="utf-8", newline="\n"
                )
                stats[("full", *key)] = _new_stat(
                    profile, spec, base_width, base_height
                )
            if options["export_sampled"]:
                root = sampled_base / profile["name"] / scale
                root.mkdir(parents=True, exist_ok=True)
                prepare_layer_folders(root)
                handles[("sampled", *key)] = (root / "manifest.jsonl").open(
                    "w", encoding="utf-8", newline="\n"
                )
                window_handles[key] = (root / "window_index.jsonl").open(
                    "w", encoding="utf-8", newline="\n"
                )
                stats[("sampled", *key)] = _new_stat(
                    profile, spec, base_width, base_height
                )

    processed = 0
    current_image_path = None
    current_padded = None
    stopped = False
    try:
        for candidate in candidates:
            if cancelled and cancelled():
                stopped = True
                break
            if candidate["image_path"] != current_image_path:
                with Image.open(candidate["image_path"]) as opened:
                    current_padded = reflect_pad_image(
                        opened.convert("RGB"), candidate["plan"]
                    )
                current_image_path = candidate["image_path"]
            plan = candidate["plan"]
            spec = candidate["spec"]
            x1 = candidate["col"] * base_width
            y1 = candidate["row"] * base_height
            x2 = x1 + spec.kernel * base_width
            y2 = y1 + spec.kernel * base_height
            crop = current_padded.crop((x1, y1, x2, y2))
            retained = candidate["sample_id"] in retained_sampled
            candidate_first_path = None

            for profile in normalized_profiles:
                result = _aggregate(candidate, profile)
                key = (profile["name"], spec.kernel, spec.stride)
                scale = _scale_name(spec, base_width, base_height)
                full_target = None
                sampled_target = None

                if options["export_full"]:
                    sample_root = run_root / "full" / profile["name"] / scale
                    if candidate["dataset_folder"]:
                        sample_root = sample_root / candidate["dataset_folder"]
                    full_target = (
                        sample_root
                        / "layer_4"
                        / result["label"]
                        / candidate["filename"]
                    )
                    if candidate_first_path is None:
                        full_target.parent.mkdir(parents=True, exist_ok=True)
                        crop.save(full_target, format="PNG")
                        candidate_first_path = full_target
                    else:
                        _link_or_copy(candidate_first_path, full_target)
                    relative = full_target.relative_to(run_root).as_posix()
                    manifest = _manifest_record(
                        candidate, profile, result, "full", relative, options
                    )
                    handles[("full", *key)].write(
                        json.dumps(manifest, ensure_ascii=False) + "\n"
                    )
                    _update_stat(stats[("full", *key)], candidate, result, True)
                    processed += 1
                    if progress:
                        progress(processed, expected, f"full / {profile['name']} / {scale}")

                if options["export_sampled"] and retained:
                    sample_root = sampled_base / profile["name"] / scale
                    if candidate["dataset_folder"]:
                        sample_root = sample_root / candidate["dataset_folder"]
                    sampled_target = (
                        sample_root
                        / "layer_4"
                        / result["label"]
                        / candidate["filename"]
                    )
                    if candidate_first_path is None:
                        sampled_target.parent.mkdir(parents=True, exist_ok=True)
                        crop.save(sampled_target, format="PNG")
                        candidate_first_path = sampled_target
                    else:
                        _link_or_copy(candidate_first_path, sampled_target)
                    relative = sampled_target.relative_to(run_root).as_posix()
                    manifest = _manifest_record(
                        candidate, profile, result, "sampled", relative, options
                    )
                    handles[("sampled", *key)].write(
                        json.dumps(manifest, ensure_ascii=False) + "\n"
                    )
                    _update_stat(stats[("sampled", *key)], candidate, result, True)
                    training_records[key].append(
                        {
                            "schema_version": 1,
                            "tool_version": 7,
                            "sample_id": candidate["sample_id"],
                            "relative_path": relative,
                            "image_id": candidate["image_id"],
                            "dataset": candidate["dataset"],
                            "source_relative_path": candidate["record"].get(
                                "source_relative_path"
                            ),
                            "label": result["label"],
                            "kernel": spec.kernel,
                            "stride": spec.stride,
                            "is_pure_background": bool(
                                candidate["is_pure_background"]
                            ),
                        }
                    )
                    processed += 1
                    if progress:
                        progress(
                            processed,
                            expected,
                            f"sampled / {profile['name']} / {scale}",
                        )
                elif options["export_sampled"]:
                    _update_stat(
                        stats[("sampled", *key)], candidate, result, False
                    )

                if options["export_sampled"]:
                    window_record = _manifest_record(
                        candidate,
                        profile,
                        result,
                        "sampled",
                        (
                            sampled_target.relative_to(run_root).as_posix()
                            if sampled_target
                            else ""
                        ),
                        options,
                    )
                    window_record.update(
                        {
                            "exported": bool(retained),
                            "skip_reason": None if retained else "pure_background",
                            "full_relative_path": (
                                full_target.relative_to(run_root).as_posix()
                                if full_target
                                else None
                            ),
                            "sampled_relative_path": (
                                sampled_target.relative_to(run_root).as_posix()
                                if sampled_target
                                else None
                            ),
                        }
                    )
                    window_handles[key].write(
                        json.dumps(window_record, ensure_ascii=False) + "\n"
                    )
    finally:
        for handle in handles.values():
            handle.close()
        for handle in window_handles.values():
            handle.close()

    summaries = {}
    for stat_key, stat in stats.items():
        variant, profile_name, kernel, stride = stat_key
        scale = _scale_name(ScaleSpec(kernel, stride), base_width, base_height)
        root = (
            run_root / "full" / profile_name / scale
            if variant == "full"
            else sampled_base / profile_name / scale
        )
        summary = _finalize_summary(stat, variant, options, stopped)
        _atomic_json(root / "summary.json", summary)
        summaries[f"{variant}/{profile_name}/{scale}"] = summary

        if variant == "sampled":
            weighted, training_stats = attach_soft_balance_weights(
                training_records[(profile_name, kernel, stride)],
                options["max_sampling_weight"],
            )
            before_counts = summary["class_counts_before"]
            before_total = sum(before_counts.values())
            training_stats.update(
                {
                    "tool_version": 7,
                    "aggregation_mode": profile_name,
                    "scale": summary["scale"],
                    "class_counts_before_sampling": before_counts,
                    "original_class_priors": {
                        label: (
                            before_counts[label] / before_total
                            if before_total
                            else 0.0
                        )
                        for label in LABELS
                    },
                    "sampled_class_priors": training_stats["class_priors"],
                    "pure_background_sampling": summary["sampling"],
                    "sampling_seed": options["sampling_seed"],
                }
            )
            _write_jsonl(root / "training_index.jsonl", weighted)
            _atomic_json(root / "training_stats.json", training_stats)

    result = {
        "run_root": str(run_root),
        "signature": signature,
        "processed": processed,
        "expected": expected,
        "cancelled": stopped,
        "sampled_profile": sampled_profile,
        "summaries": summaries,
    }
    _atomic_json(run_root / "export_result.json", result)
    return result
