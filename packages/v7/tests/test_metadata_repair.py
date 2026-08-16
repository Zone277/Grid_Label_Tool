import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from gridlabeltool_v7.metadata_repair import (
    audit_annotation_tree,
    repair_annotation_tree,
    resolve_export_output_folder,
)


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle)


class MetadataRepairTests(unittest.TestCase):
    def test_duplicate_old_image_ids_are_repaired_from_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for material, color in (("59", "#804020"), ("60", "#204080")):
                folder = root / material
                folder.mkdir()
                Image.new("RGB", (16, 16), color).save(folder / "0.jpg")
            write_json(
                root / "annotations.json",
                {
                    "schema_version": 6,
                    "base_width": 16,
                    "base_height": 16,
                    "images": {
                        "59/0.jpg": {
                            "image_id": "img_same",
                            "relative_path": "59/0.jpg",
                            "sha256": "old",
                            "labels": [["1"]],
                        },
                        "60/0.jpg": {
                            "image_id": "img_same",
                            "relative_path": "60/0.jpg",
                            "sha256": "old",
                            "labels": [["2"]],
                        },
                    },
                },
            )

            audit = audit_annotation_tree(root, root, 16, 16)
            self.assertTrue(audit["can_repair"])
            self.assertEqual(audit["blocking_count"], 0)

            repair = repair_annotation_tree(root, root, 16, 16, audit=audit)
            self.assertEqual(repair["after"]["blocking_count"], 0)

            repaired = json.loads((root / "annotations.json").read_text(encoding="utf-8"))
            self.assertEqual(repaired["images"]["59/0.jpg"]["image_id"], "59__0_jpg")
            self.assertEqual(repaired["images"]["60/0.jpg"]["image_id"], "60__0_jpg")
            self.assertEqual(
                repaired["images"]["59/0.jpg"]["previous_image_id"],
                "img_same",
            )
            self.assertTrue(list((root / "metadata_audit").glob("annotations.backup_*.json")))
            self.assertTrue(list((root / "metadata_audit").glob("image_id_mapping_*.json")))

    def test_repairs_material_child_annotations_without_rekeying_to_root_path(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cotton = root / "cotton"
            cotton.mkdir()
            Image.new("RGB", (16, 16), "#804020").save(cotton / "0.png")
            write_json(
                cotton / "annotations.json",
                {
                    "schema_version": 6,
                    "base_width": 16,
                    "base_height": 16,
                    "images": {
                        "0.png": {
                            "image_id": "img_old",
                            "relative_path": "0.png",
                            "labels": [["3"]],
                        }
                    },
                },
            )

            repair_annotation_tree(root, root, 16, 16)

            repaired = json.loads(
                (cotton / "annotations.json").read_text(encoding="utf-8")
            )
            self.assertIn("0.png", repaired["images"])
            record = repaired["images"]["0.png"]
            self.assertEqual(record["image_id"], "cotton__0_png")
            self.assertEqual(record["relative_path"], "0.png")
            self.assertEqual(record["dataset"], "cotton")
            self.assertEqual(record["source_relative_path"], "0.png")

    def test_grid_mismatch_blocks_export_after_repairable_items(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            Image.new("RGB", (32, 16), "#804020").save(root / "0.png")
            write_json(
                root / "annotations.json",
                {
                    "schema_version": 6,
                    "base_width": 16,
                    "base_height": 16,
                    "images": {
                        "0.png": {
                            "image_id": "img_old",
                            "relative_path": "0.png",
                            "labels": [["1"]],
                        }
                    },
                },
            )

            audit = audit_annotation_tree(root, root, 16, 16)

            self.assertGreater(audit["repairable_count"], 0)
            self.assertEqual(audit["blocking_count"], 1)
            self.assertFalse(audit["can_export"])

    def test_same_input_output_is_redirected_to_sibling_exports_folder(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "cloth_labeled"
            root.mkdir()

            resolved = resolve_export_output_folder(root, root)

            self.assertEqual(resolved, root.parent / "cloth_labeled_v7_exports")


if __name__ == "__main__":
    unittest.main()
