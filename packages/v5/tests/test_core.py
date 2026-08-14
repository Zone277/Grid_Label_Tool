import unittest

from gridlabeltool_v5.core import (
    block_positions,
    grid_from_pixels,
    layer_export_folder,
    normalize_labels,
    normalize_shortcut,
    normalize_transparent_labels,
    parse_cell_name,
    replace_invalid_layers,
    select_label_by_digit,
    serialize_layer_annotations,
    toggle_layer,
)


class V5CoreTests(unittest.TestCase):
    def test_normalize_labels_deduplicates_custom_values(self):
        self.assertEqual(normalize_labels("0,1,2,1,fold"), ["0", "1", "2", "fold"])
        self.assertEqual(normalize_labels([], ["0", "1"]), ["0", "1"])

    def test_transparent_layers_keep_valid_labels_only(self):
        self.assertEqual(normalize_transparent_labels(["0", "missing", "0", "2"], ["0", "1", "2"]), ["0", "2"])

    def test_layer_export_folder_uses_active_label_count(self):
        self.assertEqual(layer_export_folder(["0", "1", "2", "3"]), "layer_4")
        with self.assertRaises(ValueError):
            layer_export_folder([])

    def test_shortcut_normalization_wraps_single_keys(self):
        self.assertEqual(normalize_shortcut(" f ", "F"), "<KeyPress-f>")
        self.assertEqual(normalize_shortcut("<Left>", "F"), "<Left>")
        self.assertEqual(normalize_shortcut("", "F"), "F")

    def test_digit_selection_prefers_exact_labels(self):
        self.assertEqual(select_label_by_digit("2", ["0", "1", "2", "3"]), "2")
        self.assertEqual(select_label_by_digit("2", ["bg", "single", "multi"]), "multi")
        self.assertIsNone(select_label_by_digit("9", ["bg", "single"]))

    def test_grid_geometry_pins_edges(self):
        self.assertEqual(block_positions(49, 16), [0, 16, 32, 33])
        rows, cols, row_pos, col_pos = grid_from_pixels(49, 33, 16, 16)
        self.assertEqual((rows, cols), (3, 4))
        self.assertEqual(row_pos[-1], 17)
        self.assertEqual(col_pos[-1], 33)

    def test_parse_cell_name(self):
        self.assertEqual(parse_cell_name("material_a_5_6.png"), ("material_a", 5, 6))
        self.assertIsNone(parse_cell_name("material.png"))

    def test_replace_invalid_layers_removes_legacy_direction_payload(self):
        result = replace_invalid_layers(
            {(0, 0): {"layer": "bad", "directions": {"up"}}},
            ["0", "1"],
        )
        self.assertEqual(result[(0, 0)], {"layer": "0"})

    def test_toggle_layer_resets_repeated_non_default_selection(self):
        self.assertEqual(toggle_layer("1", "1", "0"), "0")
        self.assertEqual(toggle_layer("0", "1", "0"), "1")
        self.assertEqual(toggle_layer("1", "0", "0"), "0")

    def test_serialize_layer_only_annotations(self):
        payload = serialize_layer_annotations(
            "cloth.jpg",
            1,
            2,
            {(0, 1): {"layer": "3", "directions": {"left", "up"}}},
            ["0", "1", "2", "3"],
            {"3": "wrinkle"},
        )
        self.assertEqual(payload["layer_options"], ["0", "1", "2", "3"])
        self.assertEqual(payload["layer_descriptions"]["3"], "wrinkle")
        self.assertEqual(payload["cells"][1], {"row": 0, "col": 1, "layer": "3"})


if __name__ == "__main__":
    unittest.main()

