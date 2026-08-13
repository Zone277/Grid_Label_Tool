from __future__ import annotations

import math
from pathlib import Path
from typing import Iterable, Mapping


def normalize_labels(raw: str | Iterable[object], fallback: Iterable[object] | None = None) -> list[str]:
    if isinstance(raw, str):
        values = raw.split(",")
    else:
        try:
            values = list(raw)
        except TypeError:
            values = []

    labels: list[str] = []
    seen: set[str] = set()
    for value in values:
        label = str(value).strip()
        if label and label not in seen:
            labels.append(label)
            seen.add(label)
    if labels:
        return labels
    return [str(value).strip() for value in (fallback or []) if str(value).strip()]


def normalize_transparent_labels(values: Iterable[object], labels: Iterable[str]) -> list[str]:
    valid = set(labels)
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        label = str(value).strip()
        if label in valid and label not in seen:
            result.append(label)
            seen.add(label)
    return result


def layer_export_folder(labels: Iterable[str]) -> str:
    label_list = list(labels)
    if not label_list:
        raise ValueError("at least one layer label is required")
    return f"layer_{len(label_list)}"


def normalize_shortcut(value: object, fallback: str) -> str:
    shortcut = str(value).strip()
    return shortcut or fallback


def block_positions(image_size: int, block_size: int) -> list[int]:
    if image_size <= 0 or block_size <= 0:
        raise ValueError("image_size and block_size must be positive")
    count = max(math.ceil(image_size / block_size), 1)
    positions: list[int] = []
    for index in range(count):
        start = index * block_size
        if start + block_size > image_size:
            start = image_size - block_size
        positions.append(max(start, 0))
    return positions


def grid_from_pixels(
    image_width: int,
    image_height: int,
    block_width: int,
    block_height: int,
) -> tuple[int, int, list[int], list[int]]:
    col_positions = block_positions(image_width, block_width)
    row_positions = block_positions(image_height, block_height)
    return len(row_positions), len(col_positions), row_positions, col_positions


def parse_cell_name(filename: str) -> tuple[str, int, int] | None:
    parts = Path(filename).stem.rsplit("_", 2)
    if len(parts) != 3:
        return None
    image_name, row_text, col_text = parts
    try:
        return image_name, int(row_text), int(col_text)
    except ValueError:
        return None


def default_cell(layer: str) -> dict[str, object]:
    return {"layer": layer, "directions": set()}


def replace_invalid_layers(
    cells: Mapping[tuple[int, int], Mapping[str, object]],
    labels: Iterable[str],
) -> dict[tuple[int, int], dict[str, object]]:
    valid_labels = list(labels)
    if not valid_labels:
        raise ValueError("at least one layer label is required")
    fallback = valid_labels[0]
    valid_set = set(valid_labels)
    result: dict[tuple[int, int], dict[str, object]] = {}
    for key, cell in cells.items():
        layer = str(cell.get("layer", fallback))
        directions = set(str(value) for value in cell.get("directions", set()))
        result[key] = {
            "layer": layer if layer in valid_set else fallback,
            "directions": directions,
        }
    return result


def serialize_image_annotations(
    filename: str,
    rows: int,
    cols: int,
    cells: Mapping[tuple[int, int], Mapping[str, object]],
    labels: Iterable[str],
    descriptions: Mapping[str, str] | None = None,
) -> dict[str, object]:
    label_list = list(labels)
    if not label_list:
        raise ValueError("at least one layer label is required")
    normalized = replace_invalid_layers(cells, label_list)
    records: list[dict[str, object]] = []
    for row in range(rows):
        for col in range(cols):
            cell = normalized.get((row, col), default_cell(label_list[0]))
            records.append(
                {
                    "row": row,
                    "col": col,
                    "layer": cell["layer"],
                    "directions": sorted(cell["directions"]),
                }
            )
    return {
        "image": filename,
        "image_name": Path(filename).stem,
        "rows": rows,
        "cols": cols,
        "layer_options": label_list,
        "layer_descriptions": {label: (descriptions or {}).get(label, "") for label in label_list},
        "cells": records,
    }

