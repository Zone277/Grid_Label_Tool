import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from gridlabeltool_v7.collaboration import (
    append_annotation_event,
    compact_worker_log,
    create_project_manifest,
    list_images,
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

    def test_image_listing_uses_natural_numeric_order(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            images = root / "images"
            output = root / "output"
            images.mkdir()
            for name in ("1.png", "10.png", "2.png", "20.png", "11.png", "3.png"):
                Image.new("RGB", (4, 4), "#203040").save(images / name)

            ordered = [path.name for path in list_images(images)]
            self.assertEqual(
                ordered,
                ["1.png", "2.png", "3.png", "10.png", "11.png", "20.png"],
            )

            manifest = create_project_manifest(images, output / "project_manifest.json")
            self.assertEqual(
                [record["relative_path"] for record in manifest["images"]],
                ["1.png", "2.png", "3.png", "10.png", "11.png", "20.png"],
            )


if __name__ == "__main__":
    unittest.main()
