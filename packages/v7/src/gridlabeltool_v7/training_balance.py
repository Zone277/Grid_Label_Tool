"""Sampling and training-index helpers for LabelTool v7."""

from __future__ import annotations

import hashlib
import math
from collections import Counter, defaultdict
from typing import Iterable, Mapping, Sequence


LABELS = ("0", "1", "2", "3")
HIERARCHICAL_TARGETS = {
    "0": {"cloth": 0, "multi": -1, "wrinkle": -1},
    "1": {"cloth": 1, "multi": 0, "wrinkle": -1},
    "2": {"cloth": 1, "multi": 1, "wrinkle": 0},
    "3": {"cloth": 1, "multi": 1, "wrinkle": 1},
}


def hierarchical_targets(label: str) -> dict[str, int]:
    value = str(label)
    if value not in HIERARCHICAL_TARGETS:
        raise ValueError(f"unsupported label {value!r}")
    return dict(HIERARCHICAL_TARGETS[value])


def is_pure_background_window(
    labels: Sequence[Sequence[str]],
    valid_areas: Sequence[Sequence[int]],
    row: int,
    col: int,
    kernel: int,
) -> bool:
    """Return true only when every real-image cell in the window is label 0."""
    found_valid_area = False
    for r in range(row, row + kernel):
        if r >= len(labels) or r >= len(valid_areas):
            raise ValueError("window exceeds label grid")
        for c in range(col, col + kernel):
            if c >= len(labels[r]) or c >= len(valid_areas[r]):
                raise ValueError("window exceeds label grid")
            area = int(valid_areas[r][c])
            if area < 0:
                raise ValueError("valid area cannot be negative")
            if area == 0:
                continue
            found_valid_area = True
            if str(labels[r][c]) != "0":
                return False
    if not found_valid_area:
        raise ValueError("window has no valid original-image area")
    return True


def stable_sampling_score(
    seed: int,
    image_id: str,
    kernel: int,
    stride: int,
    row: int,
    col: int,
) -> str:
    payload = f"{int(seed)}\0{image_id}\0{kernel}\0{stride}\0{row}\0{col}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def select_retained_backgrounds(
    candidates: Iterable[Mapping],
    keep_ratio: float,
    seed: int,
) -> set[str]:
    """Select an exact, stable share of pure-background windows per material/scale."""
    ratio = float(keep_ratio)
    if not 0.0 <= ratio <= 1.0:
        raise ValueError("pure-background keep ratio must be in [0, 1]")
    groups: dict[tuple[str, int, int], list[tuple[str, str]]] = defaultdict(list)
    for candidate in candidates:
        if not candidate.get("is_pure_background"):
            continue
        sample_id = str(candidate["sample_id"])
        group = (
            str(candidate.get("dataset") or "__root__"),
            int(candidate["kernel"]),
            int(candidate["stride"]),
        )
        score = stable_sampling_score(
            seed,
            str(candidate["image_id"]),
            int(candidate["kernel"]),
            int(candidate["stride"]),
            int(candidate["row"]),
            int(candidate["col"]),
        )
        groups[group].append((score, sample_id))

    retained: set[str] = set()
    for values in groups.values():
        values.sort()
        keep_count = int(math.floor(len(values) * ratio + 0.5))
        retained.update(sample_id for _score, sample_id in values[:keep_count])
    return retained


def _normalize_with_cap(raw_weights: Sequence[float], cap: float) -> list[float]:
    """Scale positive weights to mean 1 while respecting an upper cap."""
    if not raw_weights:
        return []
    if cap < 1.0:
        raise ValueError("maximum sampling weight must be >= 1")
    if any(value <= 0 or not math.isfinite(value) for value in raw_weights):
        raise ValueError("sampling weights must be finite and positive")

    target_sum = float(len(raw_weights))
    remaining = set(range(len(raw_weights)))
    result = [0.0] * len(raw_weights)
    remaining_target = target_sum
    while remaining:
        raw_sum = sum(raw_weights[index] for index in remaining)
        scale = remaining_target / raw_sum
        over_cap = {
            index for index in remaining if raw_weights[index] * scale > cap
        }
        if not over_cap:
            for index in remaining:
                result[index] = raw_weights[index] * scale
            break
        for index in over_cap:
            result[index] = cap
        remaining.difference_update(over_cap)
        remaining_target -= cap * len(over_cap)
        if remaining_target <= 0:
            raise ValueError("sampling weight cap is too small")
    return result


def attach_soft_balance_weights(
    records: Sequence[Mapping],
    max_weight: float = 5.0,
) -> tuple[list[dict], dict]:
    """Attach sqrt-frequency weights and return reproducible training statistics."""
    group_counts = Counter(
        (
            str(record.get("dataset") or "__root__"),
            str(record["label"]),
        )
        for record in records
    )
    raw_weights = [
        1.0
        / math.sqrt(
            group_counts[
                (
                    str(record.get("dataset") or "__root__"),
                    str(record["label"]),
                )
            ]
        )
        for record in records
    ]
    weights = _normalize_with_cap(raw_weights, float(max_weight))

    weighted_records = []
    for record, weight in zip(records, weights):
        copied = dict(record)
        label = str(copied["label"])
        copied["sampling_weight"] = weight
        copied["hierarchical_targets"] = hierarchical_targets(label)
        weighted_records.append(copied)

    class_counts = Counter(str(record["label"]) for record in records)
    dataset_class_counts: dict[str, Counter] = defaultdict(Counter)
    dataset_weight_totals: dict[str, Counter] = defaultdict(Counter)
    class_weight_totals = Counter()
    for record, weight in zip(records, weights):
        dataset = str(record.get("dataset") or "__root__")
        label = str(record["label"])
        dataset_class_counts[dataset][label] += 1
        dataset_weight_totals[dataset][label] += weight
        class_weight_totals[label] += weight
    total = len(records)
    total_weight = sum(weights)

    stats = {
        "schema_version": 1,
        "sample_count": total,
        "weighting": {
            "method": "sqrt_frequency_by_dataset_and_class",
            "raw_formula": "1 / sqrt(n_dataset_scale_class)",
            "normalized_mean": 1.0,
            "max_weight": float(max_weight),
            "batch_constraint": "at_most_one_window_per_image_id_per_batch",
        },
        "class_counts": {
            label: int(class_counts.get(label, 0)) for label in LABELS
        },
        "class_priors": {
            label: (class_counts.get(label, 0) / total if total else 0.0)
            for label in LABELS
        },
        "balanced_softmax_counts": {
            label: int(class_counts.get(label, 0)) for label in LABELS
        },
        "sampling_weight_target_distribution": {
            label: (
                class_weight_totals.get(label, 0.0) / total_weight
                if total_weight
                else 0.0
            )
            for label in LABELS
        },
        "dataset_class_counts": {
            dataset: {
                label: int(counter.get(label, 0)) for label in LABELS
            }
            for dataset, counter in sorted(dataset_class_counts.items())
        },
        "dataset_sampling_weight_distribution": {
            dataset: {
                label: (
                    counter.get(label, 0.0) / sum(counter.values())
                    if sum(counter.values())
                    else 0.0
                )
                for label in LABELS
            }
            for dataset, counter in sorted(dataset_weight_totals.items())
        },
        "weight_summary": {
            "minimum": min(weights) if weights else 0.0,
            "maximum": max(weights) if weights else 0.0,
            "mean": sum(weights) / len(weights) if weights else 0.0,
        },
        "hierarchical_targets": {
            label: hierarchical_targets(label) for label in LABELS
        },
    }
    return weighted_records, stats
