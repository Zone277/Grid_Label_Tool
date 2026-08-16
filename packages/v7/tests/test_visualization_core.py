import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from gridlabeltool_v7.visualization_core import (
    coverage_grid,
    fuse_ground_truth,
    fuse_predictions,
    load_predictions,
    resolve_source_image,
)


class VisualizationCoreTests(unittest.TestCase):
    def _record(self, sample_id, row, col, label, exported=True):
        return {
            "sample_id": sample_id,
            "image_id": "img",
            "dataset": "cloth",
            "source_relative_path": "source.png",
            "source_image": "missing.png",
            "base_size": [16, 16],
            "window": {
                "row": row,
                "col": col,
                "kernel": 2,
                "stride": 1,
                "x": col * 16,
                "y": row * 16,
                "width": 32,
                "height": 32,
            },
            "original_size": [48, 32],
            "padded_size": [48, 32],
            "label": label,
            "confidence": 1.0,
            "exported": exported,
        }

    def test_prediction_loading_and_fusion_modes(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "predictions.jsonl"
            path.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "sample_id": "left",
                                "predicted_label": "1",
                                "confidence": 0.9,
                                "probabilities": {
                                    "0": 0.02,
                                    "1": 0.9,
                                    "2": 0.04,
                                    "3": 0.04,
                                },
                            }
                        ),
                        json.dumps(
                            {
                                "sample_id": "right",
                                "predicted_label": "3",
                                "confidence": 0.2,
                            }
                        ),
                    ]
                ),
                encoding="utf-8",
            )
            predictions = load_predictions(path)
            records = [
                self._record("left", 0, 0, "1"),
                self._record("right", 0, 1, "3"),
            ]
            weighted = fuse_predictions(records, predictions, "confidence_weighted")
            highest = fuse_predictions(records, predictions, "highest_risk")
            self.assertEqual(weighted["labels"][0][1], "1")
            self.assertEqual(highest["labels"][0][1], "3")
            self.assertEqual(weighted["missing_predictions"], 0)

    def test_missing_prediction_remains_unknown_and_coverage_keeps_skipped(self):
        records = [
            self._record("left", 0, 0, "0", exported=False),
            self._record("right", 0, 1, "2", exported=True),
        ]
        prediction = fuse_predictions(records, {}, "confidence_weighted")
        self.assertIsNone(prediction["labels"][0][0])
        coverage = coverage_grid(records)
        self.assertEqual(coverage["states"][0][0], "skipped")
        self.assertEqual(coverage["states"][0][2], "retained")
        truth = fuse_ground_truth(records, "highest_risk")
        self.assertEqual(truth["labels"][0][1], "2")

    def test_source_path_fallback(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            material = root / "cloth"
            material.mkdir()
            image_path = material / "source.png"
            Image.new("RGB", (16, 16), "white").save(image_path)
            record = self._record("sample", 0, 0, "1")
            self.assertEqual(resolve_source_image(record, root), image_path)


if __name__ == "__main__":
    unittest.main()
