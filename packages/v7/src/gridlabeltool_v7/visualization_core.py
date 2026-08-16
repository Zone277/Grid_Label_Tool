"""Pure manifest and prediction fusion logic for LabelTool v7 visualization."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Iterable, Mapping


LABELS = ("0", "1", "2", "3")


def load_jsonl(path: str | Path) -> list[dict]:
    source = Path(path)
    records = []
    with source.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{source}:{line_number}: invalid JSON") from exc
            if not isinstance(value, dict):
                raise ValueError(f"{source}:{line_number}: record must be an object")
            records.append(value)
    return records


def load_scale_records(folder: str | Path) -> tuple[list[dict], Path]:
    root = Path(folder)
    if root.is_file():
        source = root
        root = root.parent
    else:
        window_index = root / "window_index.jsonl"
        manifest = root / "manifest.jsonl"
        source = window_index if window_index.exists() else manifest
    if not source.exists():
        raise ValueError("selected folder has no window_index.jsonl or manifest.jsonl")
    records = load_jsonl(source)
    if not records:
        raise ValueError(f"{source} contains no window records")
    required = {"sample_id", "image_id", "window", "label", "original_size"}
    for index, record in enumerate(records, start=1):
        missing = sorted(required.difference(record))
        if missing:
            raise ValueError(
                f"{source}:{index}: missing fields {', '.join(missing)}"
            )
    return records, root


def load_predictions(path: str | Path) -> dict[str, dict]:
    predictions = {}
    for line_number, record in enumerate(load_jsonl(path), start=1):
        sample_id = str(record.get("sample_id") or "")
        if not sample_id:
            raise ValueError(f"prediction line {line_number}: missing sample_id")
        if sample_id in predictions:
            raise ValueError(f"duplicate prediction sample_id: {sample_id}")
        label = str(record.get("predicted_label") or "")
        if label not in LABELS:
            raise ValueError(
                f"prediction {sample_id}: predicted_label must be 0, 1, 2, or 3"
            )
        confidence = record.get("confidence", 1.0)
        try:
            confidence = float(confidence)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"prediction {sample_id}: invalid confidence") from exc
        if not 0.0 <= confidence <= 1.0:
            raise ValueError(f"prediction {sample_id}: confidence must be in [0, 1]")

        probabilities = record.get("probabilities")
        normalized_probabilities = None
        if probabilities is not None:
            if not isinstance(probabilities, Mapping):
                raise ValueError(
                    f"prediction {sample_id}: probabilities must be an object"
                )
            if set(map(str, probabilities)) != set(LABELS):
                raise ValueError(
                    f"prediction {sample_id}: probabilities must contain 0,1,2,3"
                )
            normalized_probabilities = {}
            for item in LABELS:
                try:
                    value = float(probabilities[item])
                except (TypeError, ValueError, KeyError) as exc:
                    raise ValueError(
                        f"prediction {sample_id}: invalid probability for {item}"
                    ) from exc
                if not 0.0 <= value <= 1.0:
                    raise ValueError(
                        f"prediction {sample_id}: probability must be in [0, 1]"
                    )
                normalized_probabilities[item] = value
            total = sum(normalized_probabilities.values())
            if not math.isclose(total, 1.0, abs_tol=0.01):
                raise ValueError(
                    f"prediction {sample_id}: probabilities must sum to 1"
                )
            if total > 0:
                normalized_probabilities = {
                    item: value / total
                    for item, value in normalized_probabilities.items()
                }
        predictions[sample_id] = {
            "sample_id": sample_id,
            "predicted_label": label,
            "confidence": confidence,
            "probabilities": normalized_probabilities,
        }
    return predictions


def image_key(record: Mapping) -> str:
    dataset = str(record.get("dataset") or "")
    relative = str(record.get("source_relative_path") or "")
    if relative:
        return f"{dataset}/{relative}".strip("/")
    return str(record.get("image_id") or record.get("source_image") or "")


def group_records_by_image(records: Iterable[Mapping]) -> dict[str, list[dict]]:
    result: dict[str, list[dict]] = {}
    for record in records:
        result.setdefault(image_key(record), []).append(dict(record))
    return dict(sorted(result.items()))


def resolve_source_image(
    record: Mapping,
    source_root: str | Path | None = None,
) -> Path | None:
    direct = Path(str(record.get("source_image") or ""))
    if str(direct) and direct.is_file():
        return direct
    if source_root is None:
        return None
    root = Path(source_root)
    dataset = str(record.get("dataset") or "")
    relative = str(record.get("source_relative_path") or "")
    candidates = []
    if dataset and relative:
        candidates.append(root / dataset / Path(relative))
    if relative:
        candidates.append(root / Path(relative))
    if dataset and direct.name:
        candidates.append(root / dataset / direct.name)
    if direct.name:
        candidates.append(root / direct.name)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _grid_metadata(records: list[Mapping]) -> tuple[int, int, int, int]:
    if not records:
        raise ValueError("records cannot be empty")
    first = records[0]
    base_size = first.get("base_size")
    if not base_size:
        window = first["window"]
        kernel = int(window["kernel"])
        base_size = [
            int(window["width"]) // kernel,
            int(window["height"]) // kernel,
        ]
    base_width, base_height = map(int, base_size)
    padded = first.get("padded_size") or first["original_size"]
    cols = int(math.ceil(int(padded[0]) / base_width))
    rows = int(math.ceil(int(padded[1]) / base_height))
    return rows, cols, base_width, base_height


def _empty_scores(rows: int, cols: int) -> list[list[dict[str, float]]]:
    return [[{} for _col in range(cols)] for _row in range(rows)]


def _apply_window_scores(
    scores: list[list[dict[str, float]]],
    record: Mapping,
    values: Mapping[str, float],
) -> None:
    window = record["window"]
    start_row = int(window["row"])
    start_col = int(window["col"])
    kernel = int(window["kernel"])
    for row in range(start_row, start_row + kernel):
        for col in range(start_col, start_col + kernel):
            if not (0 <= row < len(scores) and 0 <= col < len(scores[row])):
                continue
            target = scores[row][col]
            for label, value in values.items():
                target[label] = target.get(label, 0.0) + float(value)


def _labels_from_scores(
    scores: list[list[dict[str, float]]],
) -> tuple[list[list[str | None]], list[list[float]]]:
    labels = []
    confidence = []
    for row in scores:
        label_row = []
        confidence_row = []
        for values in row:
            if not values:
                label_row.append(None)
                confidence_row.append(0.0)
                continue
            maximum = max(values.values())
            winners = [
                label
                for label, value in values.items()
                if math.isclose(value, maximum, abs_tol=1e-12)
            ]
            selected = max(winners, key=int)
            total = sum(values.values())
            label_row.append(selected)
            confidence_row.append(maximum / total if total else 0.0)
        labels.append(label_row)
        confidence.append(confidence_row)
    return labels, confidence


def _highest_present_from_scores(
    scores: list[list[dict[str, float]]],
) -> tuple[list[list[str | None]], list[list[float]]]:
    labels = []
    confidence = []
    for row in scores:
        label_row = []
        confidence_row = []
        for values in row:
            present = [label for label, value in values.items() if value > 0]
            label_row.append(max(present, key=int) if present else None)
            confidence_row.append(1.0 if present else 0.0)
        labels.append(label_row)
        confidence.append(confidence_row)
    return labels, confidence


def fuse_ground_truth(
    records: list[Mapping],
    method: str = "confidence_weighted",
) -> dict:
    rows, cols, base_width, base_height = _grid_metadata(records)
    scores = _empty_scores(rows, cols)
    for record in records:
        label = str(record["label"])
        if method == "highest_risk":
            values = {label: 1.0}
        elif method == "confidence_weighted":
            values = {label: float(record.get("confidence", 1.0))}
        else:
            raise ValueError(f"unsupported fusion method: {method}")
        _apply_window_scores(scores, record, values)
    if method == "highest_risk":
        labels, confidence = _highest_present_from_scores(scores)
    else:
        labels, confidence = _labels_from_scores(scores)
    return {
        "labels": labels,
        "confidence": confidence,
        "rows": rows,
        "cols": cols,
        "base_width": base_width,
        "base_height": base_height,
    }


def fuse_predictions(
    records: list[Mapping],
    predictions: Mapping[str, Mapping],
    method: str = "confidence_weighted",
) -> dict:
    rows, cols, base_width, base_height = _grid_metadata(records)
    scores = _empty_scores(rows, cols)
    matched = 0
    for record in records:
        prediction = predictions.get(str(record["sample_id"]))
        if prediction is None:
            continue
        matched += 1
        label = str(prediction["predicted_label"])
        if method == "highest_risk":
            values = {label: 1.0}
        elif method == "confidence_weighted":
            probabilities = prediction.get("probabilities")
            if probabilities:
                values = {item: float(probabilities[item]) for item in LABELS}
            else:
                values = {label: float(prediction.get("confidence", 1.0))}
        else:
            raise ValueError(f"unsupported fusion method: {method}")
        _apply_window_scores(scores, record, values)
    if method == "highest_risk":
        labels, confidence = _highest_present_from_scores(scores)
    else:
        labels, confidence = _labels_from_scores(scores)
    return {
        "labels": labels,
        "confidence": confidence,
        "rows": rows,
        "cols": cols,
        "base_width": base_width,
        "base_height": base_height,
        "matched_predictions": matched,
        "missing_predictions": len(records) - matched,
    }


def coverage_grid(records: list[Mapping]) -> dict:
    rows, cols, base_width, base_height = _grid_metadata(records)
    retained = [[False for _col in range(cols)] for _row in range(rows)]
    skipped = [[False for _col in range(cols)] for _row in range(rows)]
    for record in records:
        window = record["window"]
        exported = bool(record.get("exported", True))
        for row in range(int(window["row"]), int(window["row"]) + int(window["kernel"])):
            for col in range(int(window["col"]), int(window["col"]) + int(window["kernel"])):
                if not (0 <= row < rows and 0 <= col < cols):
                    continue
                if exported:
                    retained[row][col] = True
                else:
                    skipped[row][col] = True
    states = []
    for row in range(rows):
        state_row = []
        for col in range(cols):
            if retained[row][col]:
                state_row.append("retained")
            elif skipped[row][col]:
                state_row.append("skipped")
            else:
                state_row.append("uncovered")
        states.append(state_row)
    return {
        "states": states,
        "rows": rows,
        "cols": cols,
        "base_width": base_width,
        "base_height": base_height,
    }
