from __future__ import annotations

import math
from pathlib import Path


def block_positions(image_size: int, block_size: int) -> list[int]:
    """Return fixed-size block starts, pinning the final block to the edge."""
    if image_size <= 0:
        raise ValueError("image_size must be positive")
    if block_size <= 0:
        raise ValueError("block_size must be positive")

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


def crop_positions(
    image_width: int,
    image_height: int,
    rows: int,
    cols: int,
    *,
    mode: str = "rowcol",
    block_width: int | None = None,
    block_height: int | None = None,
) -> dict[tuple[int, int], tuple[int, int, int, int]]:
    if rows <= 0 or cols <= 0:
        raise ValueError("rows and cols must be positive")

    positions: dict[tuple[int, int], tuple[int, int, int, int]] = {}
    if mode == "pixel":
        if block_width is None or block_height is None:
            raise ValueError("pixel mode requires block_width and block_height")
        col_positions = block_positions(image_width, block_width)
        row_positions = block_positions(image_height, block_height)
        for row in range(rows):
            for col in range(cols):
                x1 = col_positions[col]
                y1 = row_positions[row]
                positions[(row, col)] = (x1, y1, x1 + block_width, y1 + block_height)
        return positions

    if mode != "rowcol":
        raise ValueError(f"unsupported grid mode: {mode}")

    cell_width = image_width / cols
    cell_height = image_height / rows
    for row in range(rows):
        for col in range(cols):
            x1 = int(col * cell_width)
            y1 = int(row * cell_height)
            x2 = min(int((col + 1) * cell_width), image_width)
            y2 = min(int((row + 1) * cell_height), image_height)
            positions[(row, col)] = (x1, y1, x2, y2)
    return positions


def normalize_labels(raw: str | list[str] | tuple[str, ...], fallback: list[str] | None = None) -> list[str]:
    if isinstance(raw, str):
        values = raw.split(",")
    elif isinstance(raw, (list, tuple)):
        values = raw
    else:
        values = []

    labels: list[str] = []
    seen: set[str] = set()
    for value in values:
        label = str(value).strip()
        if label and label not in seen:
            labels.append(label)
            seen.add(label)
    return labels or list(fallback or [])


def export_filename(image_name: str, row: int, col: int, suffix: str = ".png") -> str:
    stem = Path(image_name).stem
    return f"{stem}_{row}_{col}{suffix}"

