from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "docs" / "release-assets.sha256.json"
DOC_PATH = ROOT / "docs" / "release-assets.md"
HASH_RE = re.compile(r"^[A-F0-9]{64}$")
ASSET_RE = re.compile(r"GridLabelTool-v[0-9][A-Za-z0-9_.-]*\.exe")


def load_manifest() -> list[dict[str, object]]:
    with MANIFEST_PATH.open("r", encoding="utf-8-sig") as handle:
        payload = json.load(handle)
    if not isinstance(payload, list):
        raise ValueError("checksum manifest must be a list")
    return payload


def validate_manifest(records: list[dict[str, object]]) -> set[str]:
    seen: set[str] = set()
    for index, record in enumerate(records, start=1):
        if not isinstance(record, dict):
            raise ValueError(f"record {index} must be an object")
        name = record.get("File")
        size = record.get("SizeMB")
        digest = record.get("SHA256")
        if not isinstance(name, str) or not ASSET_RE.fullmatch(name):
            raise ValueError(f"record {index} has invalid File value")
        if name in seen:
            raise ValueError(f"duplicate asset entry: {name}")
        if not isinstance(size, int | float) or size <= 0:
            raise ValueError(f"{name} has invalid SizeMB value")
        if not isinstance(digest, str) or not HASH_RE.fullmatch(digest):
            raise ValueError(f"{name} has invalid SHA256 value")
        seen.add(name)
    return seen


def validate_document_references(manifest_assets: set[str]) -> None:
    text = DOC_PATH.read_text(encoding="utf-8")
    documented_assets = set(ASSET_RE.findall(text))
    missing_in_doc = sorted(manifest_assets - documented_assets)
    missing_in_manifest = sorted(documented_assets - manifest_assets)
    if missing_in_doc:
        raise ValueError(f"manifest assets missing from docs: {', '.join(missing_in_doc)}")
    if missing_in_manifest:
        raise ValueError(f"documented assets missing from manifest: {', '.join(missing_in_manifest)}")


def main() -> None:
    records = load_manifest()
    manifest_assets = validate_manifest(records)
    validate_document_references(manifest_assets)
    print(f"Validated {len(manifest_assets)} release asset checksum entries.")


if __name__ == "__main__":
    main()
