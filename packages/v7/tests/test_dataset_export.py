import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from gridlabeltool_v7.dataset_export import collect_export_records, collect_history_store, is_source_image_path


class DatasetExportTests(unittest.TestCase):
    def test_collects_material_child_annotations(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            material = root / "cotton"
            material.mkdir()
            image = material / "0.png"
            Image.new("RGB", (16, 16), "#804020").save(image)
            payload = {
                "schema_version": 6,
                "images": {
                    "0.png": {
                        "image_id": "img_cotton",
                        "relative_path": "0.png",
                        "sha256": "abc",
                        "labels": [["1"]],
                    }
                },
            }
            with (material / "annotations.json").open("w", encoding="utf-8") as handle:
                json.dump(payload, handle)

            result = collect_export_records(root, root / "exports", {"images": {}})

            self.assertEqual(len(result["records"]), 1)
            self.assertEqual(result["records"][0]["dataset"], "cotton")
            self.assertEqual(result["records"][0]["source_relative_path"], "0.png")
            self.assertEqual(result["sources"][0]["kind"], "material_folder")

    def test_source_image_filter_skips_exports(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "cotton" / "0.png"
            exported = root / "exports" / "balanced" / "sample.png"
            source.parent.mkdir(parents=True)
            exported.parent.mkdir(parents=True)
            Image.new("RGB", (4, 4)).save(source)
            Image.new("RGB", (4, 4)).save(exported)

            self.assertTrue(is_source_image_path(root, source))
            self.assertFalse(is_source_image_path(root, exported))

    def test_history_store_prefixes_material_annotations(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            material = root / "cotton"
            material.mkdir()
            Image.new("RGB", (16, 16), "#804020").save(material / "0.png")
            payload = {
                "schema_version": 6,
                "base_width": 16,
                "base_height": 16,
                "images": {
                    "0.png": {
                        "image_id": "img_cotton",
                        "relative_path": "0.png",
                        "sha256": "abc",
                        "base_width": 16,
                        "base_height": 16,
                        "rows": 1,
                        "cols": 1,
                        "labels": [["2"]],
                    }
                },
            }
            with (material / "annotations.json").open("w", encoding="utf-8") as handle:
                json.dump(payload, handle)

            store = collect_history_store(root, root)

            self.assertIn("cotton/0.png", store["images"])
            record = store["images"]["cotton/0.png"]
            self.assertEqual(record["dataset"], "cotton")
            self.assertEqual(record["source_relative_path"], "0.png")
            self.assertEqual(record["labels"], [["2"]])

            export_info = collect_export_records(root, root, store)
            self.assertEqual(len(export_info["records"]), 1)
            self.assertEqual(export_info["records"][0]["dataset"], "cotton")

    def test_workers_do_not_override_annotations_json_records(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            Image.new("RGB", (16, 16), "#804020").save(root / "0.png")
            payload = {
                "schema_version": 6,
                "base_width": 16,
                "base_height": 16,
                "images": {
                    "0.png": {
                        "image_id": "0_png",
                        "relative_path": "0.png",
                        "labels": [["1"]],
                        "metadata_repaired": True,
                    }
                },
            }
            with (root / "annotations.json").open("w", encoding="utf-8") as handle:
                json.dump(payload, handle)
            log = root / "workers" / "a" / "annotations.jsonl"
            log.parent.mkdir(parents=True)
            with log.open("w", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        {
                            "image_id": "0_png",
                            "relative_path": "0.png",
                            "labels": [["3"]],
                        }
                    )
                    + "\n"
                )

            store = collect_history_store(root, root)

            self.assertEqual(store["images"]["0.png"]["labels"], [["1"]])


if __name__ == "__main__":
    unittest.main()
