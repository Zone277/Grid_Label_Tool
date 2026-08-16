import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from gridlabeltool_v7.multiscale_core import MODE_THRESHOLDS, ScaleSpec
from gridlabeltool_v7.v7_export import estimate_v7_export, export_multiscale_v7


def read_jsonl(path):
    with Path(path).open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


class V7ExportTests(unittest.TestCase):
    def _record(self, root, name, labels, dataset="cloth"):
        path = root / f"{name}.png"
        Image.new("RGB", (32, 32), "#804020").save(path)
        return {
            "image_id": name,
            "image_path": str(path),
            "sha256": name,
            "labels": labels,
            "dataset": dataset,
            "source_relative_path": path.name,
        }

    def test_pure_background_only_skips_sampled(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            record = self._record(root, "pure", [["0", "0"], ["0", "0"]])
            result = export_multiscale_v7(
                [record],
                root / "out",
                16,
                16,
                [ScaleSpec(2, 2)],
                [{"source_mode": "risk_priority", "thresholds": None}],
                {
                    "export_full": True,
                    "export_sampled": True,
                    "pure_background_keep_ratio": 0,
                    "sampling_seed": 0,
                    "max_sampling_weight": 5,
                },
            )
            run = Path(result["run_root"])
            scale_name = "kernel_2_stride_2_size_32x32"
            full = run / "full" / "risk_priority" / scale_name
            sampled = (
                run
                / "sampled"
                / "pure_bg_keep_0_seed_0"
                / "risk_priority"
                / scale_name
            )
            self.assertEqual(len(read_jsonl(full / "manifest.jsonl")), 1)
            self.assertEqual(read_jsonl(sampled / "manifest.jsonl"), [])
            window = read_jsonl(sampled / "window_index.jsonl")[0]
            self.assertFalse(window["exported"])
            self.assertEqual(window["skip_reason"], "pure_background")
            self.assertEqual(read_jsonl(sampled / "training_index.jsonl"), [])
            self.assertEqual(
                json.loads((sampled / "summary.json").read_text(encoding="utf-8"))[
                    "pure_background_skipped"
                ],
                1,
            )

    def test_boundary_window_aggregated_as_zero_is_retained(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            record = self._record(
                root, "boundary", [["0", "0"], ["0", "1"]]
            )
            result = export_multiscale_v7(
                [record],
                root / "out",
                16,
                16,
                [ScaleSpec(2, 2)],
                [
                    {
                        "source_mode": "precision_first",
                        "thresholds": MODE_THRESHOLDS["precision_first"],
                    }
                ],
                {"pure_background_keep_ratio": 0},
            )
            run = Path(result["run_root"])
            sampled = (
                run
                / "sampled"
                / "pure_bg_keep_0_seed_0"
                / "precision_first"
                / "kernel_2_stride_2_size_32x32"
            )
            record_out = read_jsonl(sampled / "manifest.jsonl")[0]
            self.assertEqual(record_out["label"], "0")
            self.assertFalse(record_out["is_pure_background"])
            self.assertTrue(Path(run / record_out["relative_path"]).exists())

    def test_estimate_and_export_all_selected_scales(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            records = [
                self._record(root, "a", [["0", "0"], ["0", "0"]], "a"),
                self._record(root, "b", [["1", "2"], ["3", "0"]], "b"),
            ]
            specs = [ScaleSpec(1, 1), ScaleSpec(2, 2)]
            profiles = [
                {"source_mode": "risk_priority", "thresholds": None},
                {
                    "source_mode": "balanced",
                    "thresholds": MODE_THRESHOLDS["balanced"],
                },
            ]
            estimate = estimate_v7_export(
                records,
                16,
                16,
                specs,
                profiles,
                {"pure_background_keep_ratio": 0},
            )
            self.assertEqual(estimate["candidate_count"], 10)
            self.assertEqual(estimate["pure_background_count"], 6)
            self.assertEqual(estimate["sampled_window_count"], 4)
            self.assertEqual(estimate["expected_output_samples"], 28)
            self.assertEqual(
                estimate["mode_class_counts"]["risk_priority"],
                {
                    "full": {"0": 6, "1": 1, "2": 1, "3": 2},
                    "sampled": {"0": 0, "1": 1, "2": 1, "3": 2},
                },
            )
            self.assertEqual(
                estimate["dataset_sampling_counts"]["a"],
                {
                    "candidate_count": 5,
                    "pure_background_count": 5,
                    "sampled_window_count": 0,
                    "skipped_window_count": 5,
                },
            )

    def test_sample_ids_use_path_identity_instead_of_stale_image_id(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            records = []
            for material, label in (("59", "1"), ("60", "2")):
                folder = root / material
                folder.mkdir()
                image = folder / "0.png"
                Image.new("RGB", (16, 16), "#804020").save(image)
                records.append(
                    {
                        "image_id": "img_same",
                        "image_path": str(image),
                        "sha256": "stale",
                        "labels": [[label]],
                        "dataset": material,
                        "source_relative_path": "0.png",
                        "relative_path": f"{material}/0.png",
                        "root_relative_path": f"{material}/0.png",
                    }
                )

            estimate = estimate_v7_export(
                records,
                16,
                16,
                [ScaleSpec(1, 1)],
                [{"source_mode": "risk_priority", "thresholds": None}],
                {"pure_background_keep_ratio": 0},
            )
            self.assertEqual(estimate["candidate_count"], 2)

            result = export_multiscale_v7(
                records,
                root / "out",
                16,
                16,
                [ScaleSpec(1, 1)],
                [{"source_mode": "risk_priority", "thresholds": None}],
                {"pure_background_keep_ratio": 0},
            )
            manifest = read_jsonl(
                Path(result["run_root"])
                / "full"
                / "risk_priority"
                / "kernel_1_stride_1_size_16x16"
                / "manifest.jsonl"
            )
            self.assertEqual(
                {item["sample_id"] for item in manifest},
                {
                    "59__0_png_r0000_c0000_k1_s1",
                    "60__0_png_r0000_c0000_k1_s1",
                },
            )
            self.assertEqual({item["metadata_image_id"] for item in manifest}, {"img_same"})

    def test_sampling_is_shared_across_modes_and_training_metadata_is_complete(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            record = self._record(
                root, "shared", [["0", "0"], ["1", "2"]], "cotton"
            )
            result = export_multiscale_v7(
                [record],
                root / "out",
                16,
                16,
                [ScaleSpec(1, 1)],
                [
                    {"source_mode": "risk_priority", "thresholds": None},
                    {
                        "source_mode": "balanced",
                        "thresholds": MODE_THRESHOLDS["balanced"],
                    },
                ],
                {
                    "pure_background_keep_ratio": 0.5,
                    "sampling_seed": 17,
                    "max_sampling_weight": 5,
                },
            )
            sampled = (
                Path(result["run_root"])
                / "sampled"
                / "pure_bg_keep_50_seed_17"
            )
            scale = "kernel_1_stride_1_size_16x16"
            risk_root = sampled / "risk_priority" / scale
            balanced_root = sampled / "balanced" / scale
            risk_decisions = {
                item["sample_id"]: item["exported"]
                for item in read_jsonl(risk_root / "window_index.jsonl")
            }
            balanced_decisions = {
                item["sample_id"]: item["exported"]
                for item in read_jsonl(balanced_root / "window_index.jsonl")
            }
            self.assertEqual(risk_decisions, balanced_decisions)
            self.assertEqual(sum(risk_decisions.values()), 3)

            training = read_jsonl(risk_root / "training_index.jsonl")
            self.assertEqual(len(training), 3)
            self.assertTrue(
                all(
                    {
                        "sample_id",
                        "relative_path",
                        "image_id",
                        "dataset",
                        "label",
                        "kernel",
                        "stride",
                        "sampling_weight",
                        "hierarchical_targets",
                        "is_pure_background",
                    }.issubset(item)
                    for item in training
                )
            )
            stats = json.loads(
                (risk_root / "training_stats.json").read_text(encoding="utf-8")
            )
            self.assertEqual(sum(stats["class_counts_before_sampling"].values()), 4)
            self.assertEqual(sum(stats["class_counts"].values()), 3)
            self.assertAlmostEqual(sum(stats["original_class_priors"].values()), 1)
            self.assertAlmostEqual(sum(stats["sampled_class_priors"].values()), 1)
            self.assertAlmostEqual(
                sum(stats["sampling_weight_target_distribution"].values()), 1
            )


if __name__ == "__main__":
    unittest.main()
