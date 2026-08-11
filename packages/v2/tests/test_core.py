import unittest
from pathlib import Path

from gridlabeltool_v2.core import (
    annotation_record_from_export,
    block_positions,
    crop_positions,
    grid_from_pixels,
    image_output_folder,
    normalize_labels,
    parse_slice_name,
)


class V2CoreTests(unittest.TestCase):
    def test_block_positions_pin_final_block_to_edge(self):
        self.assertEqual(block_positions(65, 16), [0, 16, 32, 48, 49])

    def test_grid_from_pixels_returns_rows_cols_and_positions(self):
        rows, cols, row_pos, col_pos = grid_from_pixels(65, 33, 16, 16)
        self.assertEqual((rows, cols), (3, 5))
        self.assertEqual(row_pos, [0, 16, 17])
        self.assertEqual(col_pos[-1], 49)

    def test_rowcol_crop_positions_cover_image(self):
        positions = crop_positions(12, 10, 2, 3, mode="rowcol")
        self.assertEqual(positions[(0, 0)], (0, 0, 4, 5))
        self.assertEqual(positions[(1, 2)], (8, 5, 12, 10))

    def test_pixel_crop_positions_use_fixed_blocks(self):
        positions = crop_positions(65, 33, 3, 5, mode="pixel", block_width=16, block_height=16)
        self.assertEqual(positions[(2, 4)], (49, 17, 65, 33))

    def test_normalize_labels_deduplicates_and_falls_back(self):
        self.assertEqual(normalize_labels(["1", "2", "1", " bg "]), ["1", "2", "bg"])
        self.assertEqual(normalize_labels([], ["1", "2"]), ["1", "2"])

    def test_parse_slice_name_keeps_underscores_in_image_name(self):
        self.assertEqual(parse_slice_name("cloth_sample_12_3.png"), ("cloth_sample", 12, 3))
        self.assertIsNone(parse_slice_name("bad_name.png"))

    def test_annotation_record_from_export_uses_label_folder(self):
        root = Path("dataset")
        path = root / "image_a" / "2" / "cloth_1_0.png"
        self.assertEqual(annotation_record_from_export(path, root), ("cloth", (1, 0), "2"))

    def test_image_output_folder_uses_stem(self):
        self.assertEqual(image_output_folder("cloth.sample.jpg"), "cloth.sample")


if __name__ == "__main__":
    unittest.main()

