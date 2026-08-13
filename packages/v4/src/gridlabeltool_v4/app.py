import json
import math
import os
import sys
import colorsys
import tkinter as tk
from copy import deepcopy
from tkinter import colorchooser, filedialog, messagebox, ttk

from PIL import Image, ImageTk


BASE_DIR = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.path.dirname(__file__)
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
DEFAULT_LAYER_OPTIONS = ["0", "1", "2"]
DIRECTION_OPTIONS = ["左上", "上", "右上", "左", "右", "左下", "下", "右下"]
DIRECTION_SYMBOLS = {
    "左上": "↖",
    "上": "↑",
    "右上": "↗",
    "左": "←",
    "右": "→",
    "左下": "↙",
    "下": "↓",
    "右下": "↘",
}
DIRECTION_SLOT = {
    "左上": (-0.28, -0.28),
    "上": (0.0, -0.28),
    "右上": (0.28, -0.28),
    "左": (-0.28, 0.0),
    "右": (0.28, 0.0),
    "左下": (-0.28, 0.28),
    "下": (0.0, 0.28),
    "右下": (0.28, 0.28),
}
DEFAULT_CONFIG = {
    "layer_options": list(DEFAULT_LAYER_OPTIONS),
    "layer_colors": {"0": "#f28585", "1": "#85f285", "2": "#8585f2"},
    "direction_colors": {
        "左上": "#f1c40f",
        "上": "#f39c12",
        "右上": "#e67e22",
        "左": "#1abc9c",
        "右": "#3498db",
        "左下": "#9b59b6",
        "下": "#e84393",
        "右下": "#e74c3c",
    },
    "show_layer_overlay": True,
    "show_direction_marks": True,
    "show_text": True,
    "layer_overlay_stipple": "gray25",
    "transparent_layers": ["0"],
    "prev_image_key": "<Left>",
    "next_image_key": "<Right>",
    "zoom_in_key": "<Control-equal>",
    "zoom_out_key": "<Control-minus>",
    "fit_image_key": "f",
    "actual_size_key": "1",
    "default_grid_mode": "pixel",
    "default_block_w": 128,
    "default_block_h": 128,
    "default_rows": 3,
    "default_cols": 4,
}


class EnhancedGridLabelTool:
    def __init__(self, root):
        self.root = root
        self.root.title("v4 动态层数分类增强版网格标注工具")
        self.root.geometry("1480x900")
        self.root.minsize(1180, 700)

        self.config_data = self._load_config()

        self.image_folder = ""
        self.output_folder = ""
        self.image_list = []
        self.current_index = -1
        self.original_image = None
        self.display_image = None
        self.tk_image = None

        self.grid_mode = self.config_data.get("default_grid_mode", "pixel")
        self.block_w = int(self.config_data.get("default_block_w", 128))
        self.block_h = int(self.config_data.get("default_block_h", 128))
        self.grid_rows = int(self.config_data.get("default_rows", 3))
        self.grid_cols = int(self.config_data.get("default_cols", 4))
        self.layer_options = self._normalize_label_list(self.config_data.get("layer_options"), DEFAULT_LAYER_OPTIONS)
        self.direction_options = list(DIRECTION_OPTIONS)
        self.current_layer_label = self.layer_options[0]
        self.active_edit_mode = "layer"
        self.cell_annotations = {}
        self.annotations_cache = {}
        self.loaded_annotations = {}
        self.dirty_indices = set()
        self.history = []

        self._row_positions = []
        self._col_positions = []
        self._canvas_w = 0
        self._canvas_h = 0
        self._offset_x = 0
        self._offset_y = 0
        self._scale = 1.0
        self.base_scale = 1.0
        self.zoom_factor = 1.0
        self.view_mode = "fit"
        self.min_zoom = 0.25
        self.max_zoom = 8.0
        self.pan_x = 0.0
        self.pan_y = 0.0
        self._cell_rects = {}
        self._pan_start = None

        self.layer_colors = dict(self.config_data.get("layer_colors", {}))
        self.direction_colors = dict(self.config_data.get("direction_colors", {}))
        self.transparent_layers = set(self.config_data.get("transparent_layers", []))

        self._bound_sequences = []

        self._build_ui()
        self._ensure_default_colors()
        self._rebuild_layer_selector()
        self._rebuild_color_controls()
        self._rebuild_legend()
        self._apply_config_to_ui()
        self._bind_shortcuts()

    def _load_config(self):
        config = deepcopy(DEFAULT_CONFIG)
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    for key, value in loaded.items():
                        if isinstance(config.get(key), dict) and isinstance(value, dict):
                            config[key].update(value)
                        else:
                            config[key] = value
            except (OSError, json.JSONDecodeError):
                pass
        return config

    @staticmethod
    def _normalize_label_list(labels, fallback=None):
        if isinstance(labels, str):
            raw_items = labels.split(",")
        elif isinstance(labels, (list, tuple)):
            raw_items = labels
        else:
            raw_items = []

        normalized = []
        seen = set()
        for item in raw_items:
            label = str(item).strip()
            if label and label not in seen:
                normalized.append(label)
                seen.add(label)
        if normalized:
            return normalized
        return list(fallback or [])

    def _generate_colors(self, n):
        colors = []
        for i in range(max(n, 1)):
            hue = i / max(n, 1)
            r, g, b = colorsys.hsv_to_rgb(hue, 0.45, 0.95)
            colors.append(f"#{int(r * 255):02x}{int(g * 255):02x}{int(b * 255):02x}")
        return colors

    def _get_layer_export_folder_name(self):
        return f"layer_{len(self.layer_options)}"

    def _get_layer_export_root(self):
        return os.path.join(self.output_folder, self._get_layer_export_folder_name())

    def _get_drape_export_root(self):
        return os.path.join(self.output_folder, "drape_8")

    def _get_layer_scan_roots(self):
        if not self.output_folder or not os.path.isdir(self.output_folder):
            return []
        preferred = self._get_layer_export_root()
        if os.path.isdir(preferred):
            return [preferred]
        roots = []
        for name in sorted(os.listdir(self.output_folder)):
            path = os.path.join(self.output_folder, name)
            if name.startswith("layer_") and os.path.isdir(path):
                roots.append(path)
        return roots

    def _get_all_layer_export_roots(self):
        if not self.output_folder or not os.path.isdir(self.output_folder):
            return []
        roots = []
        for name in sorted(os.listdir(self.output_folder)):
            path = os.path.join(self.output_folder, name)
            if name.startswith("layer_") and os.path.isdir(path):
                roots.append(path)
        current = self._get_layer_export_root()
        if current not in roots:
            roots.append(current)
        return roots

    def _safe_int(self, value, fallback):
        try:
            return int(value)
        except (TypeError, ValueError):
            return fallback

    def _normalize_shortcut(self, value, fallback):
        raw = (value or "").strip()
        if not raw:
            raw = fallback
        if raw.startswith("<") and raw.endswith(">"):
            inner = raw[1:-1].strip()
            if len(inner) == 1 and inner.isdigit():
                return f"<KeyPress-{inner}>"
            return raw
        aliases = {
            "left": "<Left>",
            "right": "<Right>",
            "up": "<Up>",
            "down": "<Down>",
            "space": "<space>",
            "enter": "<Return>",
            "return": "<Return>",
            "plus": "<plus>",
            "minus": "<minus>",
        }
        if len(raw) == 1 and raw.isdigit():
            return f"<KeyPress-{raw}>"
        return aliases.get(raw.lower(), f"<{raw}>")

    def _save_config(self):
        self.config_data["layer_options"] = list(self.layer_options)
        self.config_data["layer_colors"] = dict(self.layer_colors)
        self.config_data["direction_colors"] = dict(self.direction_colors)
        self.config_data["transparent_layers"] = sorted(label for label in self.transparent_layers if label in self.layer_options)
        self.config_data["show_layer_overlay"] = bool(self.show_layer_overlay_var.get())
        self.config_data["show_direction_marks"] = bool(self.show_direction_marks_var.get())
        self.config_data["show_text"] = bool(self.show_text_var.get())
        self.config_data["layer_overlay_stipple"] = self.layer_overlay_stipple_var.get()
        self.config_data["prev_image_key"] = self._normalize_shortcut(self.prev_key_var.get(), DEFAULT_CONFIG["prev_image_key"])
        self.config_data["next_image_key"] = self._normalize_shortcut(self.next_key_var.get(), DEFAULT_CONFIG["next_image_key"])
        self.config_data["zoom_in_key"] = self._normalize_shortcut(self.zoom_in_key_var.get(), DEFAULT_CONFIG["zoom_in_key"])
        self.config_data["zoom_out_key"] = self._normalize_shortcut(self.zoom_out_key_var.get(), DEFAULT_CONFIG["zoom_out_key"])
        self.config_data["fit_image_key"] = self._normalize_shortcut(self.fit_key_var.get(), DEFAULT_CONFIG["fit_image_key"])
        self.config_data["actual_size_key"] = self._normalize_shortcut(self.actual_size_key_var.get(), DEFAULT_CONFIG["actual_size_key"])
        self.config_data["default_grid_mode"] = self.grid_mode_var.get()
        self.config_data["default_block_w"] = self._safe_int(self.block_w_var.get(), self.block_w)
        self.config_data["default_block_h"] = self._safe_int(self.block_h_var.get(), self.block_h)
        self.config_data["default_rows"] = self._safe_int(self.rows_var.get(), self.grid_rows)
        self.config_data["default_cols"] = self._safe_int(self.cols_var.get(), self.grid_cols)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(self.config_data, f, ensure_ascii=False, indent=2)

    def _unbind_existing_shortcuts(self):
        for sequence in self._bound_sequences:
            self.root.unbind_all(sequence)
        self._bound_sequences = []

    def _set_shortcut_vars_from_config(self):
        self.prev_key_var.set(self._normalize_shortcut(self.config_data.get("prev_image_key"), DEFAULT_CONFIG["prev_image_key"]))
        self.next_key_var.set(self._normalize_shortcut(self.config_data.get("next_image_key"), DEFAULT_CONFIG["next_image_key"]))
        self.zoom_in_key_var.set(self._normalize_shortcut(self.config_data.get("zoom_in_key"), DEFAULT_CONFIG["zoom_in_key"]))
        self.zoom_out_key_var.set(self._normalize_shortcut(self.config_data.get("zoom_out_key"), DEFAULT_CONFIG["zoom_out_key"]))
        self.fit_key_var.set(self._normalize_shortcut(self.config_data.get("fit_image_key"), DEFAULT_CONFIG["fit_image_key"]))
        self.actual_size_key_var.set(self._normalize_shortcut(self.config_data.get("actual_size_key"), DEFAULT_CONFIG["actual_size_key"]))

    def _apply_shortcut_vars(self):
        self.prev_key_var.set(self._normalize_shortcut(self.prev_key_var.get(), DEFAULT_CONFIG["prev_image_key"]))
        self.next_key_var.set(self._normalize_shortcut(self.next_key_var.get(), DEFAULT_CONFIG["next_image_key"]))
        self.zoom_in_key_var.set(self._normalize_shortcut(self.zoom_in_key_var.get(), DEFAULT_CONFIG["zoom_in_key"]))
        self.zoom_out_key_var.set(self._normalize_shortcut(self.zoom_out_key_var.get(), DEFAULT_CONFIG["zoom_out_key"]))
        self.fit_key_var.set(self._normalize_shortcut(self.fit_key_var.get(), DEFAULT_CONFIG["fit_image_key"]))
        self.actual_size_key_var.set(self._normalize_shortcut(self.actual_size_key_var.get(), DEFAULT_CONFIG["actual_size_key"]))

    def _bind_shortcuts(self):
        self._apply_shortcut_vars()
        self._unbind_existing_shortcuts()
        for sequence, callback in self._get_shortcut_bindings().items():
            if sequence:
                self.root.bind_all(sequence, callback)
                self._bound_sequences.append(sequence)

    def _build_ui(self):
        main_frame = ttk.Frame(self.root)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        left_container = ttk.Frame(main_frame, width=470)
        left_container.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 5))
        left_container.pack_propagate(False)

        self.left_canvas = tk.Canvas(left_container, highlightthickness=0, width=470)
        self.left_scrollbar = ttk.Scrollbar(left_container, orient=tk.VERTICAL, command=self.left_canvas.yview)
        self.left_canvas.configure(yscrollcommand=self.left_scrollbar.set)
        self.left_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.left_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        left_panel = ttk.Frame(self.left_canvas, width=445)
        self.left_canvas_window = self.left_canvas.create_window((0, 0), window=left_panel, anchor=tk.NW)
        left_panel.bind("<Configure>", self._on_left_panel_configure)
        self.left_canvas.bind("<Configure>", self._on_left_canvas_configure)

        sec1 = ttk.LabelFrame(left_panel, text="图片文件夹")
        sec1.pack(fill=tk.X, pady=(0, 5))
        ttk.Button(sec1, text="选择文件夹...", command=self._select_image_folder).pack(fill=tk.X, padx=5, pady=2)
        self.folder_label = ttk.Label(sec1, text="未选择", wraplength=420)
        self.folder_label.pack(fill=tk.X, padx=5)
        self.image_info_label = ttk.Label(sec1, text="")
        self.image_info_label.pack(fill=tk.X, padx=5, pady=(0, 5))

        nav_frame = ttk.Frame(sec1)
        nav_frame.pack(fill=tk.X, padx=5, pady=(0, 5))
        ttk.Button(nav_frame, text="◀ 上一张", command=self._prev_image).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 2))
        ttk.Button(nav_frame, text="下一张 ▶", command=self._next_image).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(2, 0))
        sec8 = ttk.LabelFrame(left_panel, text="输出路径")
        sec8.pack(fill=tk.X, pady=(0, 5))
        ttk.Button(sec8, text="选择输出路径...", command=self._select_output_folder).pack(fill=tk.X, padx=5, pady=2)
        self.output_label = ttk.Label(sec8, text="未选择", wraplength=420)
        self.output_label.pack(fill=tk.X, padx=5, pady=(0, 2))
        self.output_hint_label = ttk.Label(sec8, text="", wraplength=420, foreground="#555555")
        self.output_hint_label.pack(fill=tk.X, padx=5, pady=(0, 5))
        self._update_output_hint()

        sec2 = ttk.LabelFrame(left_panel, text="网格设置")
        sec2.pack(fill=tk.X, pady=(0, 5))
        self.grid_mode_var = tk.StringVar(value=self.grid_mode)
        mode_frame = ttk.Frame(sec2)
        mode_frame.pack(fill=tk.X, padx=5, pady=2)
        ttk.Radiobutton(mode_frame, text="按像素大小", variable=self.grid_mode_var, value="pixel", command=self._on_grid_mode_change).pack(side=tk.LEFT, padx=(0, 10))
        ttk.Radiobutton(mode_frame, text="按行列数", variable=self.grid_mode_var, value="rowcol", command=self._on_grid_mode_change).pack(side=tk.LEFT)

        self.pixel_frame = ttk.Frame(sec2)
        self.pixel_frame.pack(fill=tk.X, padx=5, pady=2)
        ttk.Label(self.pixel_frame, text="块宽:").pack(side=tk.LEFT)
        self.block_w_var = tk.StringVar(value=str(self.block_w))
        ttk.Entry(self.pixel_frame, textvariable=self.block_w_var, width=7).pack(side=tk.LEFT, padx=3)
        ttk.Label(self.pixel_frame, text="块高:").pack(side=tk.LEFT)
        self.block_h_var = tk.StringVar(value=str(self.block_h))
        ttk.Entry(self.pixel_frame, textvariable=self.block_h_var, width=7).pack(side=tk.LEFT, padx=3)
        ttk.Label(self.pixel_frame, text="px").pack(side=tk.LEFT)

        self.rowcol_frame = ttk.Frame(sec2)
        self.rowcol_frame.pack(fill=tk.X, padx=5, pady=2)
        ttk.Label(self.rowcol_frame, text="行数:").pack(side=tk.LEFT)
        self.rows_var = tk.StringVar(value=str(self.grid_rows))
        ttk.Entry(self.rowcol_frame, textvariable=self.rows_var, width=7).pack(side=tk.LEFT, padx=3)
        ttk.Label(self.rowcol_frame, text="列数:").pack(side=tk.LEFT)
        self.cols_var = tk.StringVar(value=str(self.grid_cols))
        ttk.Entry(self.rowcol_frame, textvariable=self.cols_var, width=7).pack(side=tk.LEFT, padx=3)
        if self.grid_mode == "pixel":
            self.rowcol_frame.pack_forget()
        else:
            self.pixel_frame.pack_forget()

        ttk.Button(sec2, text="应用网格", command=self._apply_grid).pack(fill=tk.X, padx=5, pady=(2, 2))
        self.grid_info_label = ttk.Label(sec2, text="", foreground="blue", wraplength=420)
        self.grid_info_label.pack(fill=tk.X, padx=5, pady=(0, 5))

        sec3 = ttk.LabelFrame(left_panel, text="层数标注")
        sec3.pack(fill=tk.X, pady=(0, 5))
        self.edit_mode_var = tk.StringVar(value="layer")
        ttk.Radiobutton(sec3, text="点击设置层数", variable=self.edit_mode_var, value="layer", command=self._on_edit_mode_change).pack(anchor=tk.W, padx=5, pady=(2, 0))
        ttk.Label(sec3, text="层数分类（逗号分隔）:").pack(anchor=tk.W, padx=5, pady=(4, 0))
        self.layer_options_var = tk.StringVar(value=",".join(self.layer_options))
        ttk.Entry(sec3, textvariable=self.layer_options_var).pack(fill=tk.X, padx=5, pady=2)
        ttk.Button(sec3, text="应用层数分类", command=self._apply_layer_options).pack(fill=tk.X, padx=5, pady=(2, 5))
        self.layer_var = tk.StringVar(value=self.current_layer_label)
        self.layer_button_frame = ttk.Frame(sec3)
        self.layer_button_frame.pack(fill=tk.X, padx=5, pady=5)
        self._rebuild_layer_selector()

        sec4 = ttk.LabelFrame(left_panel, text="褶皱方向标注")
        sec4.pack(fill=tk.X, pady=(0, 5))
        ttk.Radiobutton(sec4, text="点击切换当前选中方向", variable=self.edit_mode_var, value="direction", command=self._on_edit_mode_change).pack(anchor=tk.W, padx=5, pady=(2, 0))
        self.direction_vars = {label: tk.BooleanVar(value=False) for label in self.direction_options}
        direction_grid = ttk.Frame(sec4)
        direction_grid.pack(fill=tk.X, padx=5, pady=5)
        for idx, label in enumerate(self.direction_options):
            ttk.Checkbutton(direction_grid, text=f"{DIRECTION_SYMBOLS[label]} {label}", variable=self.direction_vars[label]).grid(row=idx // 2, column=idx % 2, sticky="w", padx=6, pady=3)
        direction_grid.columnconfigure(0, weight=1)
        direction_grid.columnconfigure(1, weight=1)
        dir_buttons = ttk.Frame(sec4)
        dir_buttons.pack(fill=tk.X, padx=5, pady=(0, 5))
        ttk.Button(dir_buttons, text="应用到当前点击", command=lambda: self.edit_mode_var.set("direction")).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 2))
        ttk.Button(dir_buttons, text="清空当前格方向", command=self._clear_current_cell_directions).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(2, 0))

        sec5 = ttk.LabelFrame(left_panel, text="显示与颜色")
        sec5.pack(fill=tk.X, pady=(0, 5))
        self.show_layer_overlay_var = tk.BooleanVar(value=True)
        self.show_direction_marks_var = tk.BooleanVar(value=True)
        self.show_text_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(sec5, text="显示层数覆盖", variable=self.show_layer_overlay_var, command=self._on_display_setting_change).pack(anchor=tk.W, padx=5, pady=(2, 0))
        ttk.Checkbutton(sec5, text="显示方向标记", variable=self.show_direction_marks_var, command=self._on_display_setting_change).pack(anchor=tk.W, padx=5)
        ttk.Checkbutton(sec5, text="显示文字信息", variable=self.show_text_var, command=self._on_display_setting_change).pack(anchor=tk.W, padx=5)
        ttk.Label(sec5, text="覆盖纹理:").pack(anchor=tk.W, padx=5, pady=(2, 0))
        self.layer_overlay_stipple_var = tk.StringVar(value="gray25")
        stipple_box = ttk.Combobox(sec5, textvariable=self.layer_overlay_stipple_var, values=["gray12", "gray25", "gray50", "gray75"], state="readonly")
        stipple_box.pack(fill=tk.X, padx=5, pady=(0, 4))
        stipple_box.bind("<<ComboboxSelected>>", lambda _e: self._on_display_setting_change())

        ttk.Label(sec5, text="层数颜色:").pack(anchor=tk.W, padx=5)
        self.layer_color_frame = ttk.Frame(sec5)
        self.layer_color_frame.pack(fill=tk.X, padx=5, pady=(0, 4))
        ttk.Label(sec5, text="方向颜色:").pack(anchor=tk.W, padx=5)
        self.direction_color_frame = ttk.Frame(sec5)
        self.direction_color_frame.pack(fill=tk.X, padx=5, pady=(0, 5))
        self._rebuild_color_controls()

        sec6 = ttk.LabelFrame(left_panel, text="快捷键")
        sec6.pack(fill=tk.X, pady=(0, 5))
        key_grid = ttk.Frame(sec6)
        key_grid.pack(fill=tk.X, padx=5, pady=5)
        self.prev_key_var = tk.StringVar()
        self.next_key_var = tk.StringVar()
        self.zoom_in_key_var = tk.StringVar()
        self.zoom_out_key_var = tk.StringVar()
        self.fit_key_var = tk.StringVar()
        self.actual_size_key_var = tk.StringVar()
        key_rows = [
            ("上一张", self.prev_key_var),
            ("下一张", self.next_key_var),
            ("放大", self.zoom_in_key_var),
            ("缩小", self.zoom_out_key_var),
            ("适配窗口", self.fit_key_var),
            ("100%", self.actual_size_key_var),
        ]
        for idx, (label, var) in enumerate(key_rows):
            ttk.Label(key_grid, text=label).grid(row=idx, column=0, sticky="w", padx=(0, 4), pady=2)
            ttk.Entry(key_grid, textvariable=var).grid(row=idx, column=1, sticky="ew", pady=2)
        key_grid.columnconfigure(1, weight=1)
        key_btns = ttk.Frame(sec6)
        key_btns.pack(fill=tk.X, padx=5, pady=(0, 5))
        ttk.Button(key_btns, text="应用快捷键", command=self._apply_shortcut_settings).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 2))
        ttk.Button(key_btns, text="恢复默认", command=self._reset_shortcuts_to_default).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(2, 0))

        sec7 = ttk.LabelFrame(left_panel, text="缩放")
        sec7.pack(fill=tk.X, pady=(0, 5))
        zoom_btns = ttk.Frame(sec7)
        zoom_btns.pack(fill=tk.X, padx=5, pady=5)
        ttk.Button(zoom_btns, text="＋", command=self._zoom_in).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 2))
        ttk.Button(zoom_btns, text="－", command=self._zoom_out).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=2)
        ttk.Button(zoom_btns, text="适配", command=self._fit_to_window).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=2)
        ttk.Button(zoom_btns, text="100%", command=self._show_actual_size).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(2, 0))
        self.zoom_info_label = ttk.Label(sec7, text="缩放: 100%")
        self.zoom_info_label.pack(fill=tk.X, padx=5, pady=(0, 5))


        sec9 = ttk.LabelFrame(left_panel, text="操作")
        sec9.pack(fill=tk.X, pady=(0, 5))
        self.save_modified_only_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(sec9, text="保存全部时仅导出已修改图片", variable=self.save_modified_only_var).pack(anchor=tk.W, padx=5, pady=(5, 0))
        op1 = ttk.Frame(sec9)
        op1.pack(fill=tk.X, padx=5, pady=(5, 2))
        ttk.Button(op1, text="↩ 撤销", command=self._undo).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 2))
        ttk.Button(op1, text="⟲ 重置当前图", command=self._reset_annotations).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(2, 0))
        op2 = ttk.Frame(sec9)
        op2.pack(fill=tk.X, padx=5, pady=(2, 5))
        ttk.Button(op2, text="💾 保存当前图片", command=self._save_current).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 2))
        ttk.Button(op2, text="💾 保存全部图片", command=self._save_all).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(2, 0))

        sec10 = ttk.LabelFrame(left_panel, text="当前格状态")
        sec10.pack(fill=tk.X, pady=(0, 5))
        self.cell_status_label = ttk.Label(sec10, text="未选择格子", wraplength=420)
        self.cell_status_label.pack(fill=tk.X, padx=5, pady=5)
        self.selected_cell = None

        sec11 = ttk.LabelFrame(left_panel, text="图例")
        sec11.pack(fill=tk.X, pady=(0, 5))
        self.legend_frame = ttk.Frame(sec11)
        self.legend_frame.pack(fill=tk.X, padx=5, pady=5)
        self._rebuild_legend()

        canvas_frame = ttk.Frame(main_frame)
        canvas_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.canvas = tk.Canvas(canvas_frame, bg="#2b2b2b", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.canvas.bind("<Button-1>", self._on_canvas_click)
        self.canvas.bind("<Configure>", self._on_canvas_resize)
        self.canvas.bind("<ButtonPress-2>", self._start_pan)
        self.canvas.bind("<ButtonRelease-2>", self._end_pan)
        self.canvas.bind("<B2-Motion>", self._pan_canvas)
        self.canvas.bind("<MouseWheel>", self._on_mousewheel)

    def _on_left_panel_configure(self, _event):
        self.left_canvas.configure(scrollregion=self.left_canvas.bbox("all"))

    def _on_left_canvas_configure(self, event):
        self.left_canvas.itemconfigure(self.left_canvas_window, width=event.width)

    def _ensure_default_colors(self):
        generated = self._generate_colors(len(self.layer_options))
        for idx, label in enumerate(self.layer_options):
            fallback = generated[idx] if idx < len(generated) else "#cccccc"
            self.layer_colors.setdefault(label, DEFAULT_CONFIG["layer_colors"].get(label, fallback))
        for label in self.direction_options:
            self.direction_colors.setdefault(label, DEFAULT_CONFIG["direction_colors"].get(label, "#cccccc"))

    def _apply_config_to_ui(self):
        if hasattr(self, "layer_options_var"):
            self.layer_options_var.set(",".join(self.layer_options))
        self.show_layer_overlay_var.set(bool(self.config_data.get("show_layer_overlay", True)))
        self.show_direction_marks_var.set(bool(self.config_data.get("show_direction_marks", True)))
        self.show_text_var.set(bool(self.config_data.get("show_text", True)))
        self.layer_overlay_stipple_var.set(self.config_data.get("layer_overlay_stipple", "gray25"))
        self._update_output_hint()
        self._set_shortcut_vars_from_config()

    def _get_shortcut_bindings(self):
        return {
            self.prev_key_var.get().strip(): lambda _e: self._prev_image(),
            self.next_key_var.get().strip(): lambda _e: self._next_image(),
            self.zoom_in_key_var.get().strip(): lambda _e: self._zoom_in(),
            self.zoom_out_key_var.get().strip(): lambda _e: self._zoom_out(),
            self.fit_key_var.get().strip(): lambda _e: self._fit_to_window(),
            self.actual_size_key_var.get().strip(): lambda _e: self._show_actual_size(),
        }

    def _apply_shortcut_settings(self):
        self._apply_shortcut_vars()
        self._bind_shortcuts()
        self._save_config()
        messagebox.showinfo("完成", "快捷键配置已保存并生效。")

    def _reset_shortcuts_to_default(self):
        self.prev_key_var.set(DEFAULT_CONFIG["prev_image_key"])
        self.next_key_var.set(DEFAULT_CONFIG["next_image_key"])
        self.zoom_in_key_var.set(DEFAULT_CONFIG["zoom_in_key"])
        self.zoom_out_key_var.set(DEFAULT_CONFIG["zoom_out_key"])
        self.fit_key_var.set(DEFAULT_CONFIG["fit_image_key"])
        self.actual_size_key_var.set(DEFAULT_CONFIG["actual_size_key"])
        self._apply_shortcut_settings()

    def _apply_layer_options(self):
        new_options = self._normalize_label_list(self.layer_options_var.get())
        if not new_options:
            messagebox.showerror("错误", "层数分类不能为空，请使用逗号分隔输入至少一个标签。")
            return
        self._set_layer_options(new_options, reset_invalid=True)
        self._save_config()
        self._refresh_canvas()
        messagebox.showinfo("完成", f"层数分类已更新为 {len(self.layer_options)} 类。")

    def _set_layer_options(self, new_options, reset_invalid=True):
        normalized = self._normalize_label_list(new_options, self.layer_options)
        if not normalized:
            normalized = list(DEFAULT_LAYER_OPTIONS)
        self.layer_options = normalized
        self.transparent_layers = {label for label in self.transparent_layers if label in self.layer_options}
        self._ensure_default_colors()
        if self.current_layer_label not in self.layer_options:
            self.current_layer_label = self.layer_options[0]
        if hasattr(self, "layer_var"):
            self.layer_var.set(self.current_layer_label)
        if hasattr(self, "layer_options_var"):
            self.layer_options_var.set(",".join(self.layer_options))
        if reset_invalid:
            self._replace_invalid_layer_labels()
        if hasattr(self, "layer_button_frame"):
            self._rebuild_layer_selector()
        if hasattr(self, "layer_color_frame") and hasattr(self, "direction_color_frame"):
            self._rebuild_color_controls()
        if hasattr(self, "legend_frame"):
            self._rebuild_legend()
        self._update_output_hint()
        if getattr(self, "selected_cell", None) is not None:
            self._update_cell_status(self.selected_cell)

    def _replace_invalid_layer_labels(self):
        default_layer = self.layer_options[0]

        def normalize_cells(cells):
            for value in cells.values():
                if value.get("layer") not in self.layer_options:
                    value["layer"] = default_layer

        if self.cell_annotations:
            normalize_cells(self.cell_annotations)
        for cached in self.annotations_cache.values():
            normalize_cells(cached.get("cells", {}))
        for image_cells in self.loaded_annotations.values():
            normalize_cells(image_cells)

    def _update_output_hint(self):
        if hasattr(self, "output_hint_label"):
            self.output_hint_label.config(
                text=f"将导出到 {self._get_layer_export_folder_name()}/各层数标签 和 drape_8/八个方向、未标。"
            )

    def _rebuild_layer_selector(self):
        for widget in self.layer_button_frame.winfo_children():
            widget.destroy()
        if self.current_layer_label not in self.layer_options:
            self.current_layer_label = self.layer_options[0]
        for label in self.layer_options:
            color = self.layer_colors.get(label, "#cccccc")
            tk.Radiobutton(
                self.layer_button_frame,
                text=label,
                variable=self.layer_var,
                value=label,
                command=self._on_layer_selection_change,
                bg=color,
                activebackground=color,
                selectcolor=color,
                padx=8,
                pady=2,
            ).pack(side=tk.LEFT, padx=2)
        self.layer_var.set(self.current_layer_label)

    def _rebuild_color_controls(self):
        for widget in self.layer_color_frame.winfo_children():
            widget.destroy()
        for label in self.layer_options:
            row = ttk.Frame(self.layer_color_frame)
            row.pack(fill=tk.X, pady=2)
            ttk.Label(row, text=label, width=8).pack(side=tk.LEFT)
            tk.Label(row, bg=self.layer_colors.get(label, "#cccccc"), width=6, height=1, relief="solid", borderwidth=1).pack(side=tk.LEFT, padx=6)
            ttk.Button(row, text="选择颜色", command=lambda l=label: self._choose_layer_color(l)).pack(side=tk.LEFT, padx=(0, 6))
            is_transparent = tk.BooleanVar(value=label in self.transparent_layers)
            ttk.Checkbutton(row, text="透明", variable=is_transparent, command=lambda l=label, v=is_transparent: self._toggle_layer_transparency(l, v.get())).pack(side=tk.LEFT)

        for widget in self.direction_color_frame.winfo_children():
            widget.destroy()
        direction_grid = ttk.Frame(self.direction_color_frame)
        direction_grid.pack(fill=tk.X)
        for idx, label in enumerate(self.direction_options):
            row = ttk.Frame(direction_grid)
            row.grid(row=idx // 2, column=idx % 2, sticky="ew", padx=4, pady=3)
            ttk.Label(row, text=f"{DIRECTION_SYMBOLS[label]} {label}", width=9).pack(side=tk.LEFT)
            tk.Label(row, bg=self.direction_colors.get(label, "#cccccc"), width=6, height=1, relief="solid", borderwidth=1).pack(side=tk.LEFT, padx=6)
            ttk.Button(row, text="选择颜色", command=lambda l=label: self._choose_direction_color(l)).pack(side=tk.LEFT)
        direction_grid.columnconfigure(0, weight=1)
        direction_grid.columnconfigure(1, weight=1)

    def _rebuild_legend(self):
        for widget in self.legend_frame.winfo_children():
            widget.destroy()
        for label in self.layer_options:
            frame = ttk.Frame(self.legend_frame)
            frame.pack(fill=tk.X, pady=1)
            tk.Label(frame, bg=self.layer_colors.get(label, "#cccccc"), width=2, relief="solid", borderwidth=1).pack(side=tk.LEFT, padx=(0, 4))
            ttk.Label(frame, text=f"层数: {label}").pack(side=tk.LEFT)
        for label in self.direction_options:
            frame = ttk.Frame(self.legend_frame)
            frame.pack(fill=tk.X, pady=1)
            tk.Label(frame, bg=self.direction_colors.get(label, "#cccccc"), width=2, relief="solid", borderwidth=1).pack(side=tk.LEFT, padx=(0, 4))
            ttk.Label(frame, text=f"方向: {DIRECTION_SYMBOLS[label]} {label}").pack(side=tk.LEFT)

    def _choose_layer_color(self, label):
        result = colorchooser.askcolor(title=f"选择 {label} 颜色", color=self.layer_colors.get(label))
        if result and result[1]:
            self.layer_colors[label] = result[1]
            self._rebuild_layer_selector()
            self._rebuild_color_controls()
            self._rebuild_legend()
            self._save_config()
            self._refresh_canvas()

    def _choose_direction_color(self, label):
        result = colorchooser.askcolor(title=f"选择 {label} 颜色", color=self.direction_colors.get(label))
        if result and result[1]:
            self.direction_colors[label] = result[1]
            self._rebuild_color_controls()
            self._rebuild_legend()
            self._save_config()
            self._refresh_canvas()

    def _toggle_layer_transparency(self, label, enabled):
        if enabled:
            self.transparent_layers.add(label)
        else:
            self.transparent_layers.discard(label)
        self._save_config()
        self._refresh_canvas()

    def _on_layer_selection_change(self):
        self.current_layer_label = self.layer_var.get()

    def _on_edit_mode_change(self):
        self.active_edit_mode = self.edit_mode_var.get()

    def _on_display_setting_change(self):
        self._save_config()
        self._refresh_canvas()

    def _select_image_folder(self):
        folder = filedialog.askdirectory(title="选择图片文件夹")
        if not folder:
            return
        self.image_folder = folder
        exts = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}
        self.image_list = sorted(
            f for f in os.listdir(folder)
            if os.path.splitext(f)[1].lower() in exts
        )
        self.annotations_cache.clear()
        self.folder_label.config(text=folder)
        if self.image_list:
            self.current_index = 0
            self._load_current_image()
        else:
            messagebox.showwarning("警告", "所选文件夹中没有找到图片文件。")

    def _select_output_folder(self):
        folder = filedialog.askdirectory(title="选择输出文件夹")
        if folder:
            self.output_folder = folder
            self.output_label.config(text=folder)
            self._scan_existing_exports()

    def _on_grid_mode_change(self):
        mode = self.grid_mode_var.get()
        if mode == "pixel":
            self.rowcol_frame.pack_forget()
            self.pixel_frame.pack(fill=tk.X, padx=5, pady=2, before=self.pixel_frame.master.winfo_children()[-2])
        else:
            self.pixel_frame.pack_forget()
            self.rowcol_frame.pack(fill=tk.X, padx=5, pady=2, before=self.rowcol_frame.master.winfo_children()[-2])

    @staticmethod
    def _calc_block_positions(img_size, block_size):
        n = math.ceil(img_size / block_size)
        n = max(n, 1)
        positions = []
        for i in range(n):
            pos = i * block_size
            if pos + block_size > img_size:
                pos = img_size - block_size
            positions.append(max(pos, 0))
        return positions

    def _calc_grid_from_pixels(self, img_w, img_h):
        col_pos = self._calc_block_positions(img_w, self.block_w)
        row_pos = self._calc_block_positions(img_h, self.block_h)
        return len(row_pos), len(col_pos), row_pos, col_pos

    def _make_default_cell(self):
        return {"layer": self.layer_options[0], "directions": set()}

    def _parse_cell_name(self, filename):
        basename = os.path.splitext(filename)[0]
        parts = basename.rsplit("_", 2)
        if len(parts) != 3:
            return None
        image_name, row_str, col_str = parts
        try:
            return image_name, int(row_str), int(col_str)
        except ValueError:
            return None

    def _scan_existing_exports(self):
        self.loaded_annotations = {}
        if not self.output_folder or not os.path.isdir(self.output_folder):
            return

        discovered_layers = []
        for layer_root in self._get_layer_scan_roots():
            for layer in sorted(os.listdir(layer_root)):
                layer_dir = os.path.join(layer_root, layer)
                if not os.path.isdir(layer_dir):
                    continue
                if layer not in discovered_layers:
                    discovered_layers.append(layer)
                for filename in os.listdir(layer_dir):
                    parsed = self._parse_cell_name(filename)
                    if not parsed:
                        continue
                    image_name, row, col = parsed
                    self.loaded_annotations.setdefault(image_name, {})[(row, col)] = {
                        "layer": layer,
                        "directions": set(),
                    }

        if discovered_layers:
            self._set_layer_options(discovered_layers, reset_invalid=False)
            self._save_config()

        drape_root = self._get_drape_export_root()
        if os.path.isdir(drape_root):
            for direction in self.direction_options:
                direction_dir = os.path.join(drape_root, direction)
                if not os.path.isdir(direction_dir):
                    continue
                for filename in os.listdir(direction_dir):
                    parsed = self._parse_cell_name(filename)
                    if not parsed:
                        continue
                    image_name, row, col = parsed
                    cell = self.loaded_annotations.setdefault(image_name, {}).setdefault(
                        (row, col),
                        {"layer": self.layer_options[0], "directions": set()},
                    )
                    cell["directions"].add(direction)

        if self.image_list and self.current_index >= 0:
            self.annotations_cache.clear()
            self._load_current_image()

    def _apply_loaded_annotations_for_image(self, image_name):
        if image_name not in self.loaded_annotations:
            return
        default_layer = self.layer_options[0]
        for (row, col), value in self.loaded_annotations[image_name].items():
            if row < self.grid_rows and col < self.grid_cols:
                layer = value["layer"] if value["layer"] in self.layer_options else default_layer
                self.cell_annotations[(row, col)] = {
                    "layer": layer,
                    "directions": set(value["directions"]),
                }

    def _init_cell_annotations(self):
        self.cell_annotations = {}
        for r in range(self.grid_rows):
            for c in range(self.grid_cols):
                self.cell_annotations[(r, c)] = self._make_default_cell()
        self.history.clear()
        self.selected_cell = None
        self._update_cell_status(None)

    def _cache_current_annotations(self):
        if self.current_index >= 0 and self.cell_annotations:
            cached_cells = {}
            for key, value in self.cell_annotations.items():
                cached_cells[key] = {"layer": value["layer"], "directions": set(value["directions"])}
            self.annotations_cache[self.current_index] = {
                "rows": self.grid_rows,
                "cols": self.grid_cols,
                "cells": cached_cells,
            }

    def _load_current_image(self):
        if self.current_index < 0 or self.current_index >= len(self.image_list):
            return
        filename = self.image_list[self.current_index]
        path = os.path.join(self.image_folder, filename)
        self.original_image = Image.open(path)
        self.image_info_label.config(text=f"{filename}  ({self.current_index + 1}/{len(self.image_list)})")

        img_w, img_h = self.original_image.size
        mode = self.grid_mode_var.get()
        if mode == "pixel":
            new_rows, new_cols, row_pos, col_pos = self._calc_grid_from_pixels(img_w, img_h)
            self._row_positions = row_pos
            self._col_positions = col_pos
        else:
            new_rows = self.grid_rows
            new_cols = self.grid_cols

        if self.current_index in self.annotations_cache:
            cached = self.annotations_cache[self.current_index]
            if cached["rows"] == new_rows and cached["cols"] == new_cols:
                self.grid_rows = cached["rows"]
                self.grid_cols = cached["cols"]
                self.cell_annotations = {
                    key: {"layer": value["layer"], "directions": set(value["directions"])}
                    for key, value in cached["cells"].items()
                }
            else:
                del self.annotations_cache[self.current_index]
                self.grid_rows = new_rows
                self.grid_cols = new_cols
                self._init_cell_annotations()
        else:
            self.grid_rows = new_rows
            self.grid_cols = new_cols
            self._init_cell_annotations()
            self._apply_loaded_annotations_for_image(os.path.splitext(filename)[0])

        self.view_mode = "fit"
        self.zoom_factor = 1.0
        self.pan_x = 0.0
        self.pan_y = 0.0
        self.history.clear()
        self._update_grid_info()
        self._refresh_canvas(recompute_fit=True)

    def _prev_image(self):
        if self.current_index > 0:
            self._cache_current_annotations()
            self.current_index -= 1
            self._load_current_image()

    def _next_image(self):
        if self.current_index < len(self.image_list) - 1:
            self._cache_current_annotations()
            self.current_index += 1
            self._load_current_image()

    def _update_grid_info(self):
        if self.original_image is None:
            self.grid_info_label.config(text="")
            return
        img_w, img_h = self.original_image.size
        mode = self.grid_mode_var.get()
        if mode == "pixel":
            remainder_w = img_w % self.block_w
            remainder_h = img_h % self.block_h
            info = f"图片: {img_w}×{img_h} px | 网格: {self.grid_rows}行×{self.grid_cols}列\n块大小: {self.block_w}×{self.block_h} px"
            if remainder_w > 0 or remainder_h > 0:
                overlap_w = self.block_w - remainder_w if remainder_w > 0 else 0
                overlap_h = self.block_h - remainder_h if remainder_h > 0 else 0
                info += f"\n末行/列重叠: 水平 {overlap_w} px, 垂直 {overlap_h} px"
        else:
            cell_w = img_w / self.grid_cols
            cell_h = img_h / self.grid_rows
            info = f"图片: {img_w}×{img_h} px | 网格: {self.grid_rows}行×{self.grid_cols}列\n块大小: {cell_w:.1f}×{cell_h:.1f} px"
        self.grid_info_label.config(text=info)

    def _on_canvas_resize(self, event):
        if event.width != self._canvas_w or event.height != self._canvas_h:
            self._canvas_w = event.width
            self._canvas_h = event.height
            self._refresh_canvas(recompute_fit=self.view_mode == "fit")

    def _get_cell_display_rects(self, scale):
        mode = self.grid_mode_var.get()
        img_w, img_h = self.original_image.size
        rects = {}
        if mode == "pixel" and self._col_positions and self._row_positions:
            for r in range(self.grid_rows):
                for c in range(self.grid_cols):
                    px = self._col_positions[c]
                    py = self._row_positions[r]
                    x1 = self._offset_x + px * scale
                    y1 = self._offset_y + py * scale
                    x2 = x1 + self.block_w * scale
                    y2 = y1 + self.block_h * scale
                    rects[(r, c)] = (x1, y1, x2, y2)
        else:
            cell_w = img_w * scale / self.grid_cols
            cell_h = img_h * scale / self.grid_rows
            for r in range(self.grid_rows):
                for c in range(self.grid_cols):
                    x1 = self._offset_x + c * cell_w
                    y1 = self._offset_y + r * cell_h
                    x2 = x1 + cell_w
                    y2 = y1 + cell_h
                    rects[(r, c)] = (x1, y1, x2, y2)
        return rects

    def _refresh_canvas(self, recompute_fit=False):
        self.canvas.delete("all")
        if self.original_image is None:
            if self.output_folder:
                message = "请先选择图片文件夹"
            else:
                message = "请先选择图片文件夹和输出文件夹"
            self.canvas.create_text((self._canvas_w or 500) // 2, (self._canvas_h or 500) // 2, text=message, fill="#888888", font=("微软雅黑", 16))
            return
        if not self.output_folder:
            self.canvas.create_text((self._canvas_w or 500) // 2, (self._canvas_h or 500) // 2, text="请先选择输出文件夹后再开始标注", fill="#888888", font=("微软雅黑", 16))
            return
        cw = self._canvas_w or self.canvas.winfo_width()
        ch = self._canvas_h or self.canvas.winfo_height()
        if cw <= 1 or ch <= 1:
            return

        img_w, img_h = self.original_image.size
        if recompute_fit or self.base_scale <= 0:
            self.base_scale = min(cw / img_w, ch / img_h)
        if self.view_mode == "fit":
            scale = self.base_scale
        else:
            scale = max(self.min_zoom, min(self.max_zoom, self.zoom_factor))
        self._scale = scale
        new_w = max(1, int(img_w * scale))
        new_h = max(1, int(img_h * scale))

        self.display_image = self.original_image.resize((new_w, new_h), Image.LANCZOS)
        self.tk_image = ImageTk.PhotoImage(self.display_image)

        if self.view_mode == "fit":
            base_x = (cw - new_w) / 2
            base_y = (ch - new_h) / 2
            min_x = min(base_x, 0)
            max_x = max(base_x, 0)
            min_y = min(base_y, 0)
            max_y = max(base_y, 0)
            self.pan_x = min(max(self.pan_x, min_x), max_x)
            self.pan_y = min(max(self.pan_y, min_y), max_y)
            self._offset_x = base_x + self.pan_x
            self._offset_y = base_y + self.pan_y
        else:
            if self.view_mode == "actual":
                default_offset_x = 0
                default_offset_y = (ch - new_h) / 2 if new_h <= ch else 0
            else:
                default_offset_x = (cw - new_w) / 2
                default_offset_y = (ch - new_h) / 2
            min_offset_x = min(cw - new_w, default_offset_x)
            max_offset_x = max(cw - new_w, default_offset_x)
            min_offset_y = min(ch - new_h, default_offset_y)
            max_offset_y = max(ch - new_h, default_offset_y)
            self._offset_x = min(max(self._offset_x, min_offset_x), max_offset_x)
            self._offset_y = min(max(self._offset_y, min_offset_y), max_offset_y)
            self.pan_x = self._offset_x - default_offset_x
            self.pan_y = self._offset_y - default_offset_y

        self._offset_x = round(self._offset_x, 4)
        self._offset_y = round(self._offset_y, 4)
        self.pan_x = round(self.pan_x, 4)
        self.pan_y = round(self.pan_y, 4)

        self.canvas.create_image(self._offset_x, self._offset_y, anchor=tk.NW, image=self.tk_image)
        rects = self._get_cell_display_rects(scale)
        self._cell_rects = rects

        if self.show_layer_overlay_var.get():
            for (r, c), (x1, y1, x2, y2) in rects.items():
                annotation = self.cell_annotations.get((r, c), self._make_default_cell())
                layer = annotation["layer"] if annotation["layer"] in self.layer_options else self.layer_options[0]
                if layer in self.transparent_layers:
                    continue
                color = self.layer_colors.get(layer, "#cccccc")
                self.canvas.create_rectangle(x1, y1, x2, y2, fill=color, outline="", stipple=self.layer_overlay_stipple_var.get(), tags="overlay")

        if self.show_direction_marks_var.get():
            for (r, c), (x1, y1, x2, y2) in rects.items():
                annotation = self.cell_annotations.get((r, c), self._make_default_cell())
                cell_w = x2 - x1
                cell_h = y2 - y1
                font_size = max(12, min(int(cell_w / 4), int(cell_h / 4), 28))
                cx = (x1 + x2) / 2
                cy = (y1 + y2) / 2
                for direction in sorted(annotation["directions"]):
                    dx, dy = DIRECTION_SLOT[direction]
                    self.canvas.create_text(
                        cx + dx * cell_w,
                        cy + dy * cell_h,
                        text=DIRECTION_SYMBOLS[direction],
                        fill=self.direction_colors.get(direction, "#ffffff"),
                        font=("Segoe UI Symbol", font_size, "bold"),
                    )
                    self.canvas.create_text(
                        cx + dx * cell_w,
                        cy + dy * cell_h,
                        text=DIRECTION_SYMBOLS[direction],
                        fill=self.direction_colors.get(direction, "#ffffff"),
                        font=("Segoe UI Symbol", font_size + 1, "bold"),
                    )

        for (r, c), (x1, y1, x2, y2) in rects.items():
            self.canvas.create_line(x1, y1, x2, y1, fill="#ffffff", width=1)
            self.canvas.create_line(x1, y1, x1, y2, fill="#ffffff", width=1)
        for r in range(self.grid_rows):
            _, _, x2, y2 = rects[(r, self.grid_cols - 1)]
            _, y1_start, _, _ = rects[(r, self.grid_cols - 1)]
            self.canvas.create_line(x2, y1_start, x2, y2, fill="#ffffff", width=1)
        for c in range(self.grid_cols):
            _, _, x2, y2 = rects[(self.grid_rows - 1, c)]
            x1_start, _, _, _ = rects[(self.grid_rows - 1, c)]
            self.canvas.create_line(x1_start, y2, x2, y2, fill="#ffffff", width=1)

        if self.show_text_var.get():
            for (r, c), (x1, y1, x2, y2) in rects.items():
                annotation = self.cell_annotations.get((r, c), self._make_default_cell())
                layer = annotation["layer"] if annotation["layer"] in self.layer_options else self.layer_options[0]
                directions = "".join(DIRECTION_SYMBOLS[d] for d in sorted(annotation["directions"])) or "-"
                cx = (x1 + x2) / 2
                cy = (y1 + y2) / 2
                cell_w = x2 - x1
                cell_h = y2 - y1
                font_size = max(7, min(int(cell_w / 5), int(cell_h / 5), 14))
                self.canvas.create_text(cx, cy, text=f"{r},{c}\n[{layer}]\n{directions}", fill="#111111", font=("Consolas", font_size, "bold"))

        if self.selected_cell in rects:
            x1, y1, x2, y2 = rects[self.selected_cell]
            self.canvas.create_rectangle(x1, y1, x2, y2, outline="#00ffff", width=2)

        self.zoom_info_label.config(text=self._get_zoom_display_text())

    def _capture_cell_state(self, row, col):
        annotation = self.cell_annotations.get((row, col), self._make_default_cell())
        return annotation["layer"], set(annotation["directions"])

    def _mark_current_image_dirty(self):
        if self.current_index >= 0:
            self.dirty_indices.add(self.current_index)

    def _apply_cell_state(self, row, col, layer, directions):
        self.cell_annotations[(row, col)] = {"layer": layer, "directions": set(directions)}
        self._mark_current_image_dirty()

    def _on_canvas_click(self, event):
        if self.original_image is None:
            if self.output_folder:
                messagebox.showwarning("提示", "请先选择图片文件夹，再开始标注。")
            else:
                messagebox.showwarning("提示", "请先选择图片文件夹和输出文件夹，再开始标注。")
            return
        if not self.output_folder:
            messagebox.showwarning("提示", "请先选择输出文件夹，再开始标注。")
            return
        if not self._cell_rects:
            return
        clicked = None
        for (r, c), (x1, y1, x2, y2) in self._cell_rects.items():
            if x1 <= event.x < x2 and y1 <= event.y < y2:
                clicked = (r, c)
                break
        if clicked is None:
            return
        row, col = clicked
        old_layer, old_directions = self._capture_cell_state(row, col)
        new_layer = old_layer
        new_directions = set(old_directions)

        if self.edit_mode_var.get() == "layer":
            selected_layer = self.layer_var.get()
            default_layer = self.layer_options[0]
            if old_layer == selected_layer and selected_layer != default_layer:
                new_layer = default_layer
            else:
                new_layer = selected_layer
        else:
            selected = {label for label, var in self.direction_vars.items() if var.get()}
            if selected:
                for direction in selected:
                    if direction in new_directions:
                        new_directions.remove(direction)
                    else:
                        new_directions.add(direction)
        if old_layer != new_layer or old_directions != new_directions:
            self.history.append((row, col, old_layer, old_directions))
            self._apply_cell_state(row, col, new_layer, new_directions)
        self.selected_cell = (row, col)
        self._update_cell_status(self.selected_cell)
        self._refresh_canvas()

    def _update_cell_status(self, cell):
        if cell is None:
            self.cell_status_label.config(text="未选择格子")
            return
        annotation = self.cell_annotations.get(cell, self._make_default_cell())
        dirs = "、".join(sorted(annotation["directions"])) if annotation["directions"] else "无"
        self.cell_status_label.config(text=f"当前格: {cell[0]},{cell[1]}\n层数: {annotation['layer']}\n方向: {dirs}")

    def _clear_current_cell_directions(self):
        if self.selected_cell is None:
            return
        row, col = self.selected_cell
        old_layer, old_directions = self._capture_cell_state(row, col)
        if not old_directions:
            return
        self.history.append((row, col, old_layer, old_directions))
        self._apply_cell_state(row, col, old_layer, set())
        self._update_cell_status(self.selected_cell)
        self._refresh_canvas()

    def _undo(self):
        if not self.history:
            return
        row, col, old_layer, old_directions = self.history.pop()
        self._apply_cell_state(row, col, old_layer, old_directions)
        self.selected_cell = (row, col)
        self._update_cell_status(self.selected_cell)
        self._refresh_canvas()

    def _reset_annotations(self):
        if not self.cell_annotations:
            return
        if messagebox.askyesno("确认", "确定要将当前图片所有标注重置为默认值吗？"):
            self._init_cell_annotations()
            self._mark_current_image_dirty()
            self._refresh_canvas()

    def _zoom_in(self):
        if self.view_mode == "fit":
            self.zoom_factor = self._scale
        self._offset_x = getattr(self, "_offset_x", 0)
        self._offset_y = getattr(self, "_offset_y", 0)
        self.view_mode = "manual"
        self.zoom_factor = min(self.zoom_factor * 1.25, self.max_zoom)
        self._refresh_canvas()

    def _zoom_out(self):
        if self.view_mode == "fit":
            self.zoom_factor = self._scale
        self._offset_x = getattr(self, "_offset_x", 0)
        self._offset_y = getattr(self, "_offset_y", 0)
        self.view_mode = "manual"
        self.zoom_factor = max(self.zoom_factor / 1.25, self.min_zoom)
        self._refresh_canvas()

    def _fit_to_window(self):
        self.view_mode = "fit"
        self.zoom_factor = 1.0
        self.pan_x = 0.0
        self.pan_y = 0.0
        self._refresh_canvas(recompute_fit=True)

    def _show_actual_size(self):
        self.view_mode = "actual"
        self.zoom_factor = 1.0
        self.pan_x = 0.0
        self.pan_y = 0.0
        self._offset_x = 0.0
        self._offset_y = 0.0
        self._refresh_canvas()

    def _get_zoom_display_text(self):
        if self.view_mode == "fit":
            return f"缩放: 适配 ({self._scale * 100:.0f}%)"
        if self.view_mode == "actual":
            return f"缩放: 100% ({self._scale * 100:.0f}%)"
        return f"缩放: {self._scale * 100:.0f}%"

    def _preserve_view_scale_while(self, callback):
        previous_mode = self.view_mode
        previous_scale = self._scale
        previous_pan_x = self.pan_x
        previous_pan_y = self.pan_y
        success = callback()
        if not success:
            return
        if previous_mode in {"manual", "actual"} and previous_scale > 0:
            self.view_mode = previous_mode
            self.zoom_factor = max(self.min_zoom, min(self.max_zoom, previous_scale))
            self.pan_x = previous_pan_x
            self.pan_y = previous_pan_y
            self._refresh_canvas()
            self.pan_x = previous_pan_x
            self.pan_y = previous_pan_y
        else:
            self.view_mode = "fit"
            self.zoom_factor = 1.0
            self.pan_x = 0.0
            self.pan_y = 0.0
            self._refresh_canvas()

    def _apply_grid(self):
        self._preserve_view_scale_while(self._apply_grid_internal)

    def _apply_grid_internal(self):
        mode = self.grid_mode_var.get()
        self.grid_mode = mode
        if mode == "pixel":
            try:
                bw = int(self.block_w_var.get())
                bh = int(self.block_h_var.get())
                if bw < 1 or bh < 1:
                    raise ValueError
            except ValueError:
                messagebox.showerror("错误", "块宽和块高必须是正整数。")
                return False
            self.block_w = bw
            self.block_h = bh
            if self.original_image is not None:
                img_w, img_h = self.original_image.size
                if bw > img_w or bh > img_h:
                    messagebox.showerror("错误", f"块大小 ({bw}×{bh}) 不能超过图片尺寸 ({img_w}×{img_h})。")
                    return False
                rows, cols, row_pos, col_pos = self._calc_grid_from_pixels(img_w, img_h)
                self.grid_rows = rows
                self.grid_cols = cols
                self._row_positions = row_pos
                self._col_positions = col_pos
        else:
            try:
                rows = int(self.rows_var.get())
                cols = int(self.cols_var.get())
                if rows < 1 or cols < 1:
                    raise ValueError
            except ValueError:
                messagebox.showerror("错误", "行数和列数必须是正整数。")
                return False
            self.grid_rows = rows
            self.grid_cols = cols
        self._init_cell_annotations()
        if self.current_index >= 0 and self.image_list:
            self._apply_loaded_annotations_for_image(os.path.splitext(self.image_list[self.current_index])[0])
        self._update_grid_info()
        self._save_config()
        return True

    def _start_pan(self, event):
        self._pan_start = (event.x, event.y)

    def _pan_canvas(self, event):
        if self._pan_start is None:
            return
        dx = event.x - self._pan_start[0]
        dy = event.y - self._pan_start[1]
        self._pan_start = (event.x, event.y)
        self.pan_x += dx
        self.pan_y += dy
        self._refresh_canvas()

    def _end_pan(self, _event):
        self._pan_start = None

    def _on_mousewheel(self, event):
        if event.delta > 0:
            self._zoom_in()
        else:
            self._zoom_out()

    def _get_crop_positions(self, img_w, img_h, rows, cols):
        mode = self.grid_mode_var.get()
        positions = {}
        if mode == "pixel":
            col_pos = self._calc_block_positions(img_w, self.block_w)
            row_pos = self._calc_block_positions(img_h, self.block_h)
            for r in range(rows):
                for c in range(cols):
                    x1 = col_pos[c]
                    y1 = row_pos[r]
                    x2 = x1 + self.block_w
                    y2 = y1 + self.block_h
                    positions[(r, c)] = (x1, y1, x2, y2)
        else:
            cell_w = img_w / cols
            cell_h = img_h / rows
            for r in range(rows):
                for c in range(cols):
                    x1 = int(c * cell_w)
                    y1 = int(r * cell_h)
                    x2 = int((c + 1) * cell_w)
                    y2 = int((r + 1) * cell_h)
                    positions[(r, c)] = (x1, y1, min(x2, img_w), min(y2, img_h))
        return positions

    def _serialize_image_annotations(self, filename, rows, cols, cells):
        image_name = os.path.splitext(filename)[0]
        payload = {
            "grid_mode": self.grid_mode_var.get(),
            "block_w": self.block_w,
            "block_h": self.block_h,
            "rows": rows,
            "cols": cols,
            "layer_options": list(self.layer_options),
            "direction_options": list(self.direction_options),
            "layer_folder": self._get_layer_export_folder_name(),
            "drape_folder": "drape_8",
            "cells": {},
        }
        for (r, c), value in cells.items():
            payload["cells"][f"{r},{c}"] = {
                "layer": value["layer"],
                "directions": sorted(value["directions"]),
            }
        return image_name, payload

    def _write_annotations_json(self, images_payload):
        annotations_path = os.path.join(self.output_folder, "annotations.json")
        existing = {"images": {}}
        if os.path.exists(annotations_path):
            try:
                with open(annotations_path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict) and isinstance(loaded.get("images"), dict):
                    existing = loaded
            except (OSError, json.JSONDecodeError):
                pass
        existing.setdefault("images", {}).update(images_payload)
        with open(annotations_path, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)

    def _remove_old_exports_for_cell(self, image_name, out_name):
        for layer_root in self._get_all_layer_export_roots():
            if not os.path.isdir(layer_root):
                continue
            for layer in os.listdir(layer_root):
                layer_dir = os.path.join(layer_root, layer)
                if not os.path.isdir(layer_dir):
                    continue
                path = os.path.join(layer_dir, out_name)
                if os.path.exists(path):
                    try:
                        os.remove(path)
                    except OSError:
                        pass
        drape_root = self._get_drape_export_root()
        for direction in self.direction_options + ["未标"]:
            path = os.path.join(drape_root, direction, out_name)
            if os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass

    def _save_image_by_index(self, index):
        if not self.output_folder:
            messagebox.showerror("错误", "请先选择输出路径。")
            return False, None
        filename = self.image_list[index]
        image_name = os.path.splitext(filename)[0]
        img_path = os.path.join(self.image_folder, filename)
        img = Image.open(img_path)
        img_w, img_h = img.size

        if index == self.current_index:
            cells = self.cell_annotations
            rows = self.grid_rows
            cols = self.grid_cols
        elif index in self.annotations_cache:
            cached = self.annotations_cache[index]
            cells = cached["cells"]
            rows = cached["rows"]
            cols = cached["cols"]
        else:
            mode = self.grid_mode_var.get()
            if mode == "pixel":
                rows, cols, _, _ = self._calc_grid_from_pixels(img_w, img_h)
            else:
                rows = self.grid_rows
                cols = self.grid_cols
            cells = {}
            for r in range(rows):
                for c in range(cols):
                    cells[(r, c)] = self._make_default_cell()

        crop_pos = self._get_crop_positions(img_w, img_h, rows, cols)
        layer_root = self._get_layer_export_root()
        drape_root = self._get_drape_export_root()
        for layer in self.layer_options:
            os.makedirs(os.path.join(layer_root, str(layer)), exist_ok=True)
        for direction in self.direction_options + ["未标"]:
            os.makedirs(os.path.join(drape_root, str(direction)), exist_ok=True)
        for r in range(rows):
            for c in range(cols):
                annotation = cells.get((r, c), self._make_default_cell())
                layer = annotation["layer"] if annotation["layer"] in self.layer_options else self.layer_options[0]
                x1, y1, x2, y2 = crop_pos[(r, c)]
                cropped = img.crop((x1, y1, x2, y2))
                out_name = f"{image_name}_{r}_{c}.png"
                self._remove_old_exports_for_cell(image_name, out_name)
                layer_dir = os.path.join(layer_root, str(layer))
                os.makedirs(layer_dir, exist_ok=True)
                cropped.save(os.path.join(layer_dir, out_name))
                directions = sorted(annotation["directions"])
                if directions:
                    for direction in directions:
                        direction_dir = os.path.join(drape_root, str(direction))
                        os.makedirs(direction_dir, exist_ok=True)
                        cropped.save(os.path.join(direction_dir, out_name))
                else:
                    unmarked_dir = os.path.join(drape_root, "未标")
                    os.makedirs(unmarked_dir, exist_ok=True)
                    cropped.save(os.path.join(unmarked_dir, out_name))
        self.loaded_annotations[image_name] = {
            key: {"layer": value["layer"], "directions": set(value["directions"])}
            for key, value in cells.items()
        }
        payload_key, payload_value = self._serialize_image_annotations(filename, rows, cols, cells)
        return True, {payload_key: payload_value}

    def _save_current(self):
        if self.current_index < 0:
            messagebox.showwarning("警告", "没有加载任何图片。")
            return
        success, payload = self._save_image_by_index(self.current_index)
        if success and payload:
            self._write_annotations_json(payload)
            self.dirty_indices.discard(self.current_index)
            messagebox.showinfo("完成", f"当前图片已保存到: {self.output_folder}\n{self._get_layer_export_folder_name()}: 按层数分类\ndrape_8: 按方向分类")

    def _save_all(self):
        if not self.image_list:
            messagebox.showwarning("警告", "没有加载任何图片。")
            return
        if not self.output_folder:
            messagebox.showerror("错误", "请先选择输出路径。")
            return
        self._cache_current_annotations()
        if self.save_modified_only_var.get():
            indices_to_save = sorted(i for i in self.dirty_indices if 0 <= i < len(self.image_list))
            if not indices_to_save:
                messagebox.showinfo("完成", "当前没有已修改但未保存的图片。")
                return
        else:
            indices_to_save = list(range(len(self.image_list)))
        success_count = 0
        merged_payload = {}
        saved_indices = []
        for i in indices_to_save:
            success, payload = self._save_image_by_index(i)
            if success:
                success_count += 1
                saved_indices.append(i)
            if payload:
                merged_payload.update(payload)
        if merged_payload:
            self._write_annotations_json(merged_payload)
        for i in saved_indices:
            self.dirty_indices.discard(i)
        messagebox.showinfo("完成", f"已保存 {success_count}/{len(indices_to_save)} 张图片到: {self.output_folder}\n{self._get_layer_export_folder_name()}: 按层数分类\ndrape_8: 按方向分类")


def main():
    root = tk.Tk()
    app = EnhancedGridLabelTool(root)
    root.mainloop()


if __name__ == "__main__":
    main()
