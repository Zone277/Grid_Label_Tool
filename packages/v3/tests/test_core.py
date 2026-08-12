import unittest

from gridlabeltool_v3.core import (
    block_positions,
    crop_positions,
    default_cell,
    grid_from_pixels,
    normalize_directions,
    normalize_labels,
    parse_cell_name,
    serialize_image_annotations,
)


class V3CoreTests(unittest.TestCase):
    def test_block_positions_pin_final_block_to_edge(self):
        self.assertEqual(block_positions(34, 16), [0, 16, 18])

    def test_grid_from_pixels_returns_rows_cols_and_positions(self):
        rows, cols, row_pos, col_pos = grid_from_pixels(34, 50, 16, 16)
        self.assertEqual((rows, cols), (4, 3))
        self.assertEqual(row_pos[-1], 34)
        self.assertEqual(col_pos[-1], 18)

    def test_crop_positions_support_pixel_and_rowcol_modes(self):
        pixel = crop_positions(34, 50, 4, 3, mode="pixel", block_width=16, block_height=16)
        self.assertEqual(pixel[(3, 2)], (18, 34, 34, 50))
        rowcol = crop_positions(9, 6, 2, 3, mode="rowcol")
        self.assertEqual(rowcol[(1, 2)], (6, 3, 9, 6))

    def test_normalize_labels_deduplicates(self):
        self.assertEqual(normalize_labels("0,1,1, 2 "), ["0", "1", "2"])
        self.assertEqual(normalize_labels("", ["0"]), ["0"])

    def test_parse_cell_name_keeps_underscores(self):
        self.assertEqual(parse_cell_name("cloth_sample_4_5.png"), ("cloth_sample", 4, 5))
        self.assertIsNone(parse_cell_name("bad.png"))

    def test_default_cell_and_direction_normalization(self):
        self.assertEqual(default_cell("1"), {"layer": "1", "directions": set()})
        self.assertEqual(normalize_directions(["up", "bad", "up", "left"]), ["up", "left"])

    def test_serialize_image_annotations_orders_cells(self):
        payload = serialize_image_annotations(
            "cloth.png",
            1,
            2,
            {
                (0, 0): {"layer": "1", "directions": {"left"}},
                (0, 1): {"layer": "2", "directions": {"up", "left"}},
            },
        )
        self.assertEqual(payload["image_name"], "cloth")
        self.assertEqual(payload["cells"][0], {"row": 0, "col": 0, "layer": "1", "directions": ["left"]})
        self.assertEqual(payload["cells"][1]["directions"], ["left", "up"])


if __name__ == "__main__":
    unittest.main()

