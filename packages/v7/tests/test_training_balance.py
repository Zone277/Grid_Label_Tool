import math
import unittest

from gridlabeltool_v7.training_balance import (
    attach_soft_balance_weights,
    hierarchical_targets,
    is_pure_background_window,
    select_retained_backgrounds,
)


class TrainingBalanceTests(unittest.TestCase):
    def test_hierarchical_targets(self):
        self.assertEqual(
            hierarchical_targets("0"),
            {"cloth": 0, "multi": -1, "wrinkle": -1},
        )
        self.assertEqual(
            hierarchical_targets("3"),
            {"cloth": 1, "multi": 1, "wrinkle": 1},
        )

    def test_pure_background_ignores_zero_valid_area(self):
        self.assertTrue(
            is_pure_background_window(
                [["0", "3"], ["3", "3"]],
                [[256, 0], [0, 0]],
                0,
                0,
                2,
            )
        )
        self.assertFalse(
            is_pure_background_window(
                [["0", "1"], ["0", "0"]],
                [[256, 256], [256, 256]],
                0,
                0,
                2,
            )
        )

    def test_background_selection_is_exact_stable_and_grouped(self):
        candidates = []
        for dataset in ("a", "b"):
            for index in range(10):
                candidates.append(
                    {
                        "sample_id": f"{dataset}_{index}",
                        "image_id": f"img_{dataset}_{index}",
                        "dataset": dataset,
                        "kernel": 2,
                        "stride": 2,
                        "row": index,
                        "col": 0,
                        "is_pure_background": True,
                    }
                )
        first = select_retained_backgrounds(candidates, 0.3, 7)
        second = select_retained_backgrounds(candidates, 0.3, 7)
        changed = select_retained_backgrounds(candidates, 0.3, 8)
        self.assertEqual(first, second)
        self.assertEqual(len([item for item in first if item.startswith("a_")]), 3)
        self.assertEqual(len([item for item in first if item.startswith("b_")]), 3)
        self.assertNotEqual(first, changed)

    def test_soft_weights_are_mean_one_and_capped(self):
        records = [
            {"sample_id": f"a{i}", "dataset": "a", "label": "1"}
            for i in range(20)
        ]
        records += [
            {"sample_id": "a_tail", "dataset": "a", "label": "3"},
            {"sample_id": "b_tail", "dataset": "b", "label": "3"},
        ]
        weighted, stats = attach_soft_balance_weights(records, max_weight=3.0)
        values = [item["sampling_weight"] for item in weighted]
        self.assertTrue(math.isclose(sum(values) / len(values), 1.0))
        self.assertLessEqual(max(values), 3.0)
        self.assertEqual(
            weighted[-1]["hierarchical_targets"],
            {"cloth": 1, "multi": 1, "wrinkle": 1},
        )
        self.assertEqual(stats["balanced_softmax_counts"]["1"], 20)
        self.assertEqual(stats["balanced_softmax_counts"]["3"], 2)


if __name__ == "__main__":
    unittest.main()
