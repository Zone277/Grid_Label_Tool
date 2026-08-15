import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from gridlabeltool_v6.collaboration import (
    append_annotation_event,
    compact_worker_log,
    create_project_manifest,
    merge_return_bundles,
    split_assignments,
)


class CollaborationTests(unittest.TestCase):
    def test_manifest_assign_log_compact_and_merge(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            images = root / "images"
            output = root / "output"
            images.mkdir()
            for index in range(5):
                Image.new("RGB", (4, 4), (index, 0, 0)).save(images / f"{index}.png")
            manifest = create_project_manifest(images, output / "project_manifest.json")
            self.assertEqual(manifest["image_count"], 5)
            assignment = split_assignments(manifest, ["a", "b"])
            self.assertEqual(
                [assignment["assignments"][name]["image_count"] for name in ("a", "b")],
                [3, 2],
            )

            record = manifest["images"][0]
            event = {
                "image_id": record["image_id"],
                "relative_path": record["relative_path"],
                "sha256": record["sha256"],
                "labels": [["0"]],
            }
            append_annotation_event(output, "a", event)
            append_annotation_event(output, "a", {**event, "labels": [["1"]]})
            bundle = root / "a.zip"
            compact = compact_worker_log(output, "a", bundle)
            self.assertEqual(compact["image_count"], 1)
            self.assertEqual(compact["annotations"][0]["labels"], [["1"]])
            merged = merge_return_bundles([bundle], root / "merged.json")
            self.assertEqual(merged["image_count"], 1)
            self.assertEqual(merged["conflict_count"], 0)
            with (root / "merged.json").open("r", encoding="utf-8") as handle:
                self.assertEqual(json.load(handle)["image_count"], 1)


if __name__ == "__main__":
    unittest.main()
