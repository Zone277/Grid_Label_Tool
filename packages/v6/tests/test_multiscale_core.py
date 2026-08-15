import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from gridlabeltool_v6.multiscale_core import (
    MODE_THRESHOLDS,
    ScaleSpec,
    aggregate_window,
    aggregate_risk_priority_window,
    expected_window_count,
    export_multiscale,
    make_padding_plan,
    parse_scale_specs,
    reflect_pad_image,
    valid_area_grid,
    window_starts,
)


class MultiscaleCoreTests(unittest.TestCase):
    def test_padding_and_valid_areas(self):
        plan = make_padding_plan(35, 33, 16, 16)
        self.assertEqual((plan.cols, plan.rows), (3, 3))
        self.assertEqual((plan.pad_right, plan.pad_bottom), (13, 15))
        areas = valid_area_grid(plan)
        self.assertEqual(areas[0], [256, 256, 48])
        self.assertEqual(areas[2], [16, 16, 3])
        self.assertEqual(sum(map(sum, areas)), 35 * 33)

    def test_reflect_padding_preserves_origin_and_size(self):
        image = Image.new("RGB", (3, 3))
        for y in range(3):
            for x in range(3):
                image.putpixel((x, y), (x, y, 0))
        plan = make_padding_plan(3, 3, 2, 2)
        padded = reflect_pad_image(image, plan)
        self.assertEqual(padded.size, (4, 4))
        self.assertEqual(padded.getpixel((0, 0)), (0, 0, 0))
        self.assertEqual(padded.getpixel((3, 0)), (2, 0, 0))
        self.assertEqual(padded.getpixel((0, 3)), (0, 2, 0))
        self.assertEqual(padded.getpixel((3, 3)), (2, 2, 0))

    def test_edge_window_is_appended(self):
        self.assertEqual(window_starts(40, 3, 3)[-2:], [36, 37])
        self.assertEqual(window_starts(30, 3, 3)[-1], 27)
        self.assertEqual(expected_window_count(30, 40, ScaleSpec(3, 3)), 140)
        self.assertEqual(expected_window_count(30, 40, ScaleSpec(3, 2)), 300)
        self.assertEqual(expected_window_count(30, 40, ScaleSpec(3, 1)), 1064)

    def test_plan_examples_use_hierarchical_voting(self):
        labels = [
            ["0", "0", "1", "1"],
            ["1", "1", "1", "1"],
            ["2", "2", "2", "2"],
            ["2", "2", "2", "3"],
        ]
        areas = [[1] * 4 for _ in range(4)]
        result = aggregate_window(
            labels, areas, 0, 0, 4, MODE_THRESHOLDS["balanced"]
        )
        self.assertEqual(result["areas"], {"0": 2, "1": 6, "2": 7, "3": 1})
        self.assertAlmostEqual(result["p_cloth"], 14 / 16)
        self.assertAlmostEqual(result["p_multi"], 8 / 14)
        self.assertAlmostEqual(result["p_wrinkle"], 1 / 8)
        self.assertEqual(result["label"], "2")

        safety = aggregate_window(
            labels, areas, 0, 0, 4, MODE_THRESHOLDS["safety_first"]
        )
        precision = aggregate_window(
            labels, areas, 0, 0, 4, MODE_THRESHOLDS["precision_first"]
        )
        self.assertEqual(safety["label"], "2")
        self.assertEqual(precision["label"], "1")

    def test_second_and_third_examples(self):
        areas = [[1] * 4 for _ in range(4)]
        labels = [
            ["0", "0", "1", "1"],
            ["1", "1", "2", "2"],
            ["2", "2", "2", "3"],
            ["3", "3", "3", "3"],
        ]
        for mode in MODE_THRESHOLDS.values():
            self.assertEqual(aggregate_window(labels, areas, 0, 0, 4, mode)["label"], "3")

        labels = [
            ["0", "0", "0", "0"],
            ["0", "0", "0", "0"],
            ["1", "1", "1", "1"],
            ["2", "2", "2", "3"],
        ]
        self.assertEqual(
            aggregate_window(labels, areas, 0, 0, 4, MODE_THRESHOLDS["safety_first"])[
                "label"
            ],
            "3",
        )
        self.assertEqual(
            aggregate_window(labels, areas, 0, 0, 4, MODE_THRESHOLDS["balanced"])[
                "label"
            ],
            "2",
        )
        self.assertEqual(
            aggregate_window(labels, areas, 0, 0, 4, MODE_THRESHOLDS["precision_first"])[
                "label"
            ],
            "0",
        )

    def test_risk_priority_uses_highest_nonzero_valid_label(self):
        labels = [
            ["0", "0", "0", "0"],
            ["0", "1", "1", "0"],
            ["0", "1", "3", "0"],
            ["0", "0", "0", "0"],
        ]
        result = aggregate_risk_priority_window(
            labels, [[1] * 4 for _ in range(4)], 0, 0, 4
        )
        self.assertEqual(result["label"], "3")
        self.assertEqual(result["aggregation_rule"], "highest_present_label")
        self.assertAlmostEqual(result["selected_area_ratio"], 1 / 16)
        self.assertIsNone(result["threshold_margin"])

    def test_risk_priority_ignores_padding_only_label(self):
        labels = [["1", "3"], ["3", "3"]]
        result = aggregate_risk_priority_window(
            labels, [[256, 0], [0, 0]], 0, 0, 2
        )
        self.assertEqual(result["label"], "1")
        self.assertEqual(result["areas"]["3"], 0)

    def test_scale_parser(self):
        specs = parse_scale_specs("1:1, 2:2, 3:1, 3:1")
        self.assertEqual([(item.kernel, item.stride) for item in specs], [(1, 1), (2, 2), (3, 1)])
        with self.assertRaises(ValueError):
            parse_scale_specs("2:3")

    def test_export_writes_manifest_images_and_summary(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            image_path = root / "source.png"
            Image.new("RGB", (32, 32), "#804020").save(image_path)
            labels = [["1", "2"], ["3", "0"]]
            result = export_multiscale(
                [
                    {
                        "image_id": "img_test",
                        "image_path": str(image_path),
                        "sha256": "abc",
                        "labels": labels,
                    }
                ],
                root / "output",
                16,
                16,
                [ScaleSpec(2, 2)],
                [
                    {
                        "source_mode": "balanced",
                        "thresholds": MODE_THRESHOLDS["balanced"],
                    }
                ],
            )
            self.assertEqual(result["processed"], 1)
            scale = root / "output" / "exports" / "balanced" / "kernel_2_stride_2_size_32x32"
            manifest_path = scale / "manifest.jsonl"
            with manifest_path.open("r", encoding="utf-8") as handle:
                manifest = json.loads(handle.readline())
            self.assertEqual(manifest["label"], "3")
            self.assertEqual(manifest["valid_ratio"], 1.0)
            self.assertTrue(
                (root / "output" / "exports" / manifest["relative_path"]).exists()
            )
            self.assertTrue((scale / "summary.json").exists())

    def test_export_keeps_material_folder_dimension(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            image_path = root / "source.png"
            Image.new("RGB", (16, 16), "#804020").save(image_path)
            result = export_multiscale(
                [
                    {
                        "image_id": "img_material",
                        "image_path": str(image_path),
                        "sha256": "abc",
                        "labels": [["2"]],
                        "dataset": "cotton",
                        "source_relative_path": "0.png",
                    }
                ],
                root / "output",
                16,
                16,
                [ScaleSpec(1, 1)],
                [
                    {
                        "source_mode": "balanced",
                        "thresholds": MODE_THRESHOLDS["balanced"],
                    }
                ],
            )
            self.assertEqual(result["processed"], 1)
            scale = root / "output" / "exports" / "balanced" / "kernel_1_stride_1_size_16x16"
            manifest_path = scale / "manifest.jsonl"
            with manifest_path.open("r", encoding="utf-8") as handle:
                manifest = json.loads(handle.readline())
            self.assertEqual(manifest["dataset"], "cotton")
            self.assertEqual(manifest["source_relative_path"], "0.png")
            self.assertTrue((scale / "cotton" / "layer_4" / "2").exists())
            self.assertTrue((root / "output" / "exports" / manifest["relative_path"]).exists())
            with (scale / "summary.json").open("r", encoding="utf-8") as handle:
                summary = json.load(handle)
            self.assertEqual(summary["dataset_counts"], {"cotton": 1})
            self.assertEqual(summary["dataset_class_counts"]["cotton"]["2"], 1)

    def test_export_writes_isolated_risk_priority_profile(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            image_path = root / "source.png"
            Image.new("RGB", (32, 32), "#804020").save(image_path)
            export_multiscale(
                [{"image_id": "img_risk", "image_path": str(image_path), "labels": [["0", "0"], ["0", "3"]]}],
                root / "output",
                16,
                16,
                [ScaleSpec(2, 2)],
                [{"source_mode": "risk_priority", "thresholds": None}],
            )
            scale = root / "output" / "exports" / "risk_priority" / "kernel_2_stride_2_size_32x32"
            with (scale / "manifest.jsonl").open("r", encoding="utf-8") as handle:
                record = json.loads(handle.readline())
            self.assertEqual(record["schema_version"], 2)
            self.assertEqual(record["label"], "3")
            self.assertEqual(record["aggregation_rule"], "highest_present_label")
            self.assertIsNone(record["thresholds"])
            self.assertTrue((scale / "layer_4" / "3" / f"{record['sample_id']}.png").exists())


if __name__ == "__main__":
    unittest.main()
