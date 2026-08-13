import unittest

from gridlabeltool_v4.core import (
    block_positions,
    grid_from_pixels,
    layer_export_folder,
    normalize_labels,
    normalize_shortcut,
    normalize_transparent_labels,
    parse_cell_name,
    replace_invalid_layers,
    serialize_image_annotations,
)


class V4CoreTests(unittest.TestCase):
    def test_normalize_labels_supports_dynamic_values(self):
        self.assertEqual(normalize_labels("0,1,2,1, custom"), ["0", "1", "2", "custom"])
        self.assertEqual(normalize_labels([], ["0", "1"]), ["0", "1"])

    def test_transparent_labels_are_filtered(self):
        self.assertEqual(normalize_transparent_labels(["0", "missing", "0", "2"], ["0", "1", "2"]), ["0", "2"])

    def test_layer_export_folder_uses_label_count(self):
        self.assertEqual(layer_export_folder(["0", "1", "2"]), "layer_3")
        with self.assertRaises(ValueError):
            layer_export_folder([])

    def test_shortcut_normalization_uses_fallback(self):
        self.assertEqual(normalize_shortcut(" Ctrl+S ", "S"), "Ctrl+S")
        self.assertEqual(normalize_shortcut("", "S"), "S")

    def test_grid_geometry_pins_edges(self):
        self.assertEqual(block_positions(49, 16), [0, 16, 32, 33])
        rows, cols, row_pos, col_pos = grid_from_pixels(49, 33, 16, 16)
        self.assertEqual((rows, cols), (3, 4))
        self.assertEqual(row_pos[-1], 17)
        self.assertEqual(col_pos[-1], 33)

    def test_parse_cell_name(self):
        self.assertEqual(parse_cell_name("material_a_5_6.png"), ("material_a", 5, 6))
        self.assertIsNone(parse_cell_name("material.png"))

    def test_replace_invalid_layers_keeps_directions(self):
        result = replace_invalid_layers(
            {(0, 0): {"layer": "bad", "directions": {"up"}}},
            ["0", "1"],
        )
        self.assertEqual(result[(0, 0)], {"layer": "0", "directions": {"up"}})

    def test_serialize_dynamic_annotations(self):
        payload = serialize_image_annotations(
            "cloth.jpg",
            1,
            2,
            {(0, 1): {"layer": "2", "directions": {"left", "up"}}},
            ["0", "1", "2"],
            {"2": "multi"},
        )
        self.assertEqual(payload["layer_options"], ["0", "1", "2"])
        self.assertEqual(payload["layer_descriptions"]["2"], "multi")
        self.assertEqual(payload["cells"][1]["directions"], ["left", "up"])


if __name__ == "__main__":
    unittest.main()

