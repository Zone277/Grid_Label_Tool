import unittest

from gridlabeltool_v1.core import (
    block_positions,
    crop_positions,
    export_filename,
    grid_from_pixels,
    normalize_labels,
)


class V1CoreTests(unittest.TestCase):
    def test_block_positions_pin_final_block_to_edge(self):
        self.assertEqual(block_positions(50, 16), [0, 16, 32, 34])

    def test_grid_from_pixels_returns_rows_cols_and_positions(self):
        rows, cols, row_pos, col_pos = grid_from_pixels(50, 33, 16, 16)
        self.assertEqual((rows, cols), (3, 4))
        self.assertEqual(row_pos, [0, 16, 17])
        self.assertEqual(col_pos, [0, 16, 32, 34])

    def test_rowcol_crop_positions_cover_image(self):
        positions = crop_positions(10, 9, 3, 2, mode="rowcol")
        self.assertEqual(positions[(0, 0)], (0, 0, 5, 3))
        self.assertEqual(positions[(2, 1)], (5, 6, 10, 9))

    def test_pixel_crop_positions_use_fixed_blocks(self):
        positions = crop_positions(50, 33, 3, 4, mode="pixel", block_width=16, block_height=16)
        self.assertEqual(positions[(2, 3)], (34, 17, 50, 33))

    def test_normalize_labels_deduplicates_and_uses_fallback(self):
        self.assertEqual(normalize_labels(" 1,2,1, bg "), ["1", "2", "bg"])
        self.assertEqual(normalize_labels("", ["1"]), ["1"])

    def test_export_filename_uses_stem_row_and_col(self):
        self.assertEqual(export_filename("cloth.sample.jpg", 2, 3), "cloth.sample_2_3.png")


if __name__ == "__main__":
    unittest.main()

