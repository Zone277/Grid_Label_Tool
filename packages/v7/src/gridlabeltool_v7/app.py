from __future__ import annotations

import json
import math
import os
import sys
from copy import deepcopy
from pathlib import Path
import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox, ttk

from PIL import Image, ImageTk

from .collaboration import (
    append_annotation_event,
    compact_worker_log,
    create_project_manifest,
    natural_path_sort_key,
    path_based_image_id,
    sha256_file,
)
from .dataset_export import (
    collect_export_records,
    collect_history_store,
    has_annotation_data,
    is_source_image_path,
)
from .multiscale_core import (
    LABELS,
    MODE_THRESHOLDS,
    RISK_PRIORITY_MODE,
    ScaleSpec,
    make_padding_plan,
    parse_scale_specs,
)
from .metadata_repair import (
    audit_annotation_tree,
    repair_annotation_tree,
    resolve_export_output_folder,
    write_metadata_audit_report,
)
from .v7_export import (
    DEFAULT_EXPORT_OPTIONS,
    estimate_v7_export,
    export_multiscale_v7,
)


BASE_DIR = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
CONFIG_PATH = BASE_DIR / "config.json"
PERSONAL_DIR = Path(os.environ.get("APPDATA", Path.home())) / "LabelTool" / "v7"
PERSONAL_CONFIG_PATH = PERSONAL_DIR / "personal_config.json"
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}

LAYER_DESCRIPTIONS = {
    "0": "背景信息",
    "1": "单层布料",
    "2": "多层布料-无褶皱",
    "3": "多层布料-有褶皱",
}
DEFAULT_COLORS = {
    "0": "#9aa0a6",
    "1": "#4caf50",
    "2": "#ff00ff",
    "3": "#ff0000",
}

SHORTCUT_ACTIONS = [
    ("previous_image", "上一张", "Q"),
    ("next_image", "下一张", "E"),
    ("save_and_next", "保存当前并下一张", "Space"),
    ("select_label_0", "选择标签 0", "0"),
    ("select_label_1", "选择标签 1", "1"),
    ("select_label_2", "选择标签 2", "2"),
    ("select_label_3", "选择标签 3", "3"),
    ("undo", "撤销", "Ctrl+Z"),
    ("redo", "重做", "Ctrl+Y"),
    ("brush_smaller", "减小画笔", "["),
    ("brush_larger", "增大画笔", "]"),
    ("copy_previous", "复制上一帧标签预览", "C"),
    ("confirm_preview", "确认预览", "Enter"),
    ("cancel_operation", "取消复制/矩形", "Esc"),
    ("fit_window", "适应窗口", "F"),
    ("zoom_in", "放大", "Ctrl++"),
    ("zoom_out", "缩小", "Ctrl+-"),
    ("save_current", "保存当前但不切换", "Ctrl+S"),
]
DEFAULT_SHORTCUTS = {action: default for action, _label, default in SHORTCUT_ACTIONS}

DEFAULT_CONFIG = {
    "schema_version": 7,
    "layer_options": list(LABELS),
    "layer_descriptions": dict(LAYER_DESCRIPTIONS),
    "layer_colors": dict(DEFAULT_COLORS),
    "transparent_layers": ["0"],
    "show_overlay": True,
    "overlay_stipple": "gray25",
    "default_base_width": 16,
    "default_base_height": 16,
    "link_base_dimensions": True,
    "default_scale_specs": "1:1,2:2,3:3,4:4",
    "aggregation_modes": {
        name: {"enabled": False, **thresholds}
        for name, thresholds in MODE_THRESHOLDS.items()
    },
    "v7_export": dict(DEFAULT_EXPORT_OPTIONS),
}
DEFAULT_CONFIG["aggregation_modes"][RISK_PRIORITY_MODE] = {"enabled": True}
DEFAULT_PERSONAL = {
    "annotator_id": "",
    "shortcuts": dict(DEFAULT_SHORTCUTS),
}


def load_json(path: Path, fallback: dict) -> dict:
    result = deepcopy(fallback)
    if not path.exists():
        return result
    try:
        with path.open("r", encoding="utf-8") as handle:
            loaded = json.load(handle)
        if isinstance(loaded, dict):
            for key, value in loaded.items():
                if isinstance(result.get(key), dict) and isinstance(value, dict):
                    result[key].update(value)
                else:
                    result[key] = value
    except (OSError, json.JSONDecodeError):
        pass
    return result


def atomic_json_write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
    os.replace(temp, path)


def shortcut_to_sequences(value: str) -> list[str]:
    raw = str(value).strip()
    if not raw:
        return []
    normalized = raw.replace("Control", "Ctrl").replace("Return", "Enter")
    lower = normalized.lower()
    special = {
        "space": ["<KeyPress-space>"],
        "enter": ["<KeyPress-Return>", "<KeyPress-KP_Enter>"],
        "esc": ["<KeyPress-Escape>"],
        "escape": ["<KeyPress-Escape>"],
        "[": ["<KeyPress-bracketleft>"],
        "]": ["<KeyPress-bracketright>"],
        "ctrl++": ["<Control-KeyPress-plus>", "<Control-KeyPress-equal>"],
        "ctrl+-": ["<Control-KeyPress-minus>"],
    }
    if lower in special:
        return special[lower]

    parts = [part for part in normalized.split("+") if part]
    if not parts:
        return []
    key = parts[-1]
    modifiers = []
    for part in parts[:-1]:
        item = part.lower()
        if item in {"ctrl", "control"}:
            modifiers.append("Control")
        elif item == "shift":
            modifiers.append("Shift")
        elif item in {"alt", "option"}:
            modifiers.append("Alt")
    key_aliases = {
        "left": "Left",
        "right": "Right",
        "up": "Up",
        "down": "Down",
        "delete": "Delete",
        "backspace": "BackSpace",
        "tab": "Tab",
    }
    key_name = key_aliases.get(key.lower(), key.lower() if len(key) == 1 else key)
    pieces = modifiers + ["KeyPress", key_name]
    return ["<" + "-".join(pieces) + ">"]


def event_to_shortcut(event: tk.Event) -> str | None:
    keysym = str(event.keysym)
    if keysym in {"Control_L", "Control_R", "Shift_L", "Shift_R", "Alt_L", "Alt_R"}:
        return None
    if keysym == "BackSpace":
        return ""
    modifiers = []
    if event.state & 0x0004:
        modifiers.append("Ctrl")
    if event.state & 0x0001:
        modifiers.append("Shift")
    if event.state & 0x0008 or event.state & 0x20000:
        modifiers.append("Alt")
    aliases = {
        "space": "Space",
        "Return": "Enter",
        "KP_Enter": "Enter",
        "Escape": "Esc",
        "bracketleft": "[",
        "bracketright": "]",
        "plus": "+",
        "equal": "+",
        "minus": "-",
    }
    key = aliases.get(keysym, keysym.upper() if len(keysym) == 1 else keysym)
    if key == "+" and "Shift" in modifiers:
        modifiers.remove("Shift")
    if not modifiers:
        return key
    return "+".join(modifiers + [key])


class LabelToolV7:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("LabelTool v7 - 多尺度采样与布料层数标注")
        self.root.geometry("1580x920")
        self.root.minsize(1200, 720)

        self.config = load_json(CONFIG_PATH, DEFAULT_CONFIG)
        self.personal = load_json(PERSONAL_CONFIG_PATH, DEFAULT_PERSONAL)
        self.shortcuts = dict(DEFAULT_SHORTCUTS)
        self.shortcuts.update(self.personal.get("shortcuts", {}))
        self.layer_colors = dict(DEFAULT_COLORS)
        self.layer_colors.update(self.config.get("layer_colors", {}))
        self.transparent_layers = set(self.config.get("transparent_layers", ["0"]))

        self.image_folder: Path | None = None
        self.output_folder: Path | None = None
        self.image_paths: list[Path] = []
        self.current_index = -1
        self.original_image: Image.Image | None = None
        self.tk_image = None
        self.annotation_cache: dict[str, dict] = {}
        self.annotation_store: dict = {"schema_version": 6, "images": {}}
        self.current_cells: dict[tuple[int, int], str] = {}
        self.grid_rows = 0
        self.grid_cols = 0
        self.dirty_images: set[str] = set()
        self.sha_cache: dict[str, str] = {}
        self.history: list[list[tuple[int, int, str, str]]] = []
        self.redo_history: list[list[tuple[int, int, str, str]]] = []
        self.selected_cell: tuple[int, int] | None = None
        self.copy_preview: dict[tuple[int, int], str] | None = None
        self.rectangle_preview: tuple[tuple[int, int], tuple[int, int], str] | None = None
        self.stroke_originals: dict[tuple[int, int], str] | None = None
        self.stroke_last_cell: tuple[int, int] | None = None
        self.stroke_label = "0"
        self.stroke_rectangle = False
        self.rectangle_start: tuple[int, int] | None = None
        self.export_cancelled = False

        self.view_mode = "fit"
        self.zoom_factor = 1.0
        self.min_zoom = 0.15
        self.max_zoom = 12.0
        self.scale = 1.0
        self.offset_x = 0.0
        self.offset_y = 0.0
        self.pan_x = 0.0
        self.pan_y = 0.0
        self.pan_start = None
        self.cell_rects: dict[tuple[int, int], tuple[float, float, float, float]] = {}
        self.refresh_pending = False
        self.bound_sequences: list[str] = []

        self._build_ui()
        self._bind_shortcuts()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(80, self._finish_initial_layout)

    def _finish_initial_layout(self) -> None:
        self.canvas.focus_set()
        self._schedule_refresh(True)

    def _build_ui(self) -> None:
        main = ttk.Frame(self.root)
        main.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)
        left_host = ttk.Frame(main, width=490)
        left_host.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 6))
        left_host.pack_propagate(False)

        self.left_canvas = tk.Canvas(left_host, highlightthickness=0, width=470)
        scroll = ttk.Scrollbar(left_host, orient=tk.VERTICAL, command=self.left_canvas.yview)
        self.left_canvas.configure(yscrollcommand=scroll.set)
        self.left_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.left_panel = ttk.Frame(self.left_canvas)
        self.left_window = self.left_canvas.create_window(
            (0, 0), window=self.left_panel, anchor=tk.NW
        )
        self.left_panel.bind(
            "<Configure>",
            lambda _event: self.left_canvas.configure(
                scrollregion=self.left_canvas.bbox("all")
            ),
        )
        self.left_canvas.bind(
            "<Configure>",
            lambda event: self.left_canvas.itemconfigure(self.left_window, width=event.width),
        )
        self.left_canvas.bind(
            "<MouseWheel>",
            lambda event: self.left_canvas.yview_scroll(
                -1 if event.delta > 0 else 1, "units"
            ),
        )

        io = ttk.LabelFrame(self.left_panel, text="1. 输入与输出")
        io.pack(fill=tk.X, padx=4, pady=(3, 5))
        self._button(io, "选择图片文件夹", self._select_image_folder).pack(
            fill=tk.X, padx=6, pady=(6, 2)
        )
        self.input_label = ttk.Label(io, text="未选择", wraplength=440)
        self.input_label.pack(fill=tk.X, padx=6)
        self._button(io, "选择输出文件夹", self._select_output_folder).pack(
            fill=tk.X, padx=6, pady=(7, 2)
        )
        self.output_label = ttk.Label(io, text="未选择", wraplength=440)
        self.output_label.pack(fill=tk.X, padx=6)
        id_row = ttk.Frame(io)
        id_row.pack(fill=tk.X, padx=6, pady=6)
        ttk.Label(id_row, text="标注员 ID").pack(side=tk.LEFT)
        self.annotator_var = tk.StringVar(value=self.personal.get("annotator_id", ""))
        self.annotator_entry = ttk.Entry(id_row, textvariable=self.annotator_var)
        self.annotator_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 0))
        self.annotator_entry.bind("<FocusOut>", lambda _e: self._save_personal_config())

        grid = ttk.LabelFrame(self.left_panel, text="2. 基础网格")
        grid.pack(fill=tk.X, padx=4, pady=5)
        dims = ttk.Frame(grid)
        dims.pack(fill=tk.X, padx=6, pady=(6, 3))
        ttk.Label(dims, text="基础块").pack(side=tk.LEFT)
        self.base_w_var = tk.StringVar(
            value=str(self.config.get("default_base_width", 16))
        )
        self.base_h_var = tk.StringVar(
            value=str(self.config.get("default_base_height", 16))
        )
        ttk.Entry(dims, width=7, textvariable=self.base_w_var).pack(
            side=tk.LEFT, padx=(8, 3)
        )
        ttk.Label(dims, text="×").pack(side=tk.LEFT)
        ttk.Entry(dims, width=7, textvariable=self.base_h_var).pack(
            side=tk.LEFT, padx=3
        )
        ttk.Label(dims, text="px").pack(side=tk.LEFT)
        self.link_base_var = tk.BooleanVar(
            value=bool(self.config.get("link_base_dimensions", True))
        )
        ttk.Checkbutton(grid, text="宽高联动（默认正方形）", variable=self.link_base_var).pack(
            anchor=tk.W, padx=6
        )
        self._button(grid, "应用基础网格", self._apply_base_grid).pack(
            fill=tk.X, padx=6, pady=5
        )
        self.grid_info_label = ttk.Label(
            grid,
            text="默认 16×16；首次产生标注后基础尺寸锁定",
            wraplength=440,
            foreground="#555555",
        )
        self.grid_info_label.pack(fill=tk.X, padx=6, pady=(0, 5))

        labels = ttk.LabelFrame(self.left_panel, text="3. 分类标签")
        labels.pack(fill=tk.X, padx=4, pady=5)
        self.layer_var = tk.StringVar(value="0")
        self.label_buttons: dict[str, ttk.Radiobutton] = {}
        for label in LABELS:
            row = ttk.Frame(labels)
            row.pack(fill=tk.X, padx=6, pady=2)
            swatch = tk.Canvas(row, width=16, height=16, highlightthickness=1)
            swatch.create_rectangle(0, 0, 16, 16, fill=self.layer_colors[label], outline="")
            swatch.pack(side=tk.LEFT, padx=(0, 6))
            radio = ttk.Radiobutton(
                row,
                text=f"{label}  {LAYER_DESCRIPTIONS[label]}",
                value=label,
                variable=self.layer_var,
                command=self._label_changed,
            )
            radio.pack(side=tk.LEFT, fill=tk.X, expand=True)
            self.label_buttons[label] = radio
            self._button(
                row,
                "颜色",
                lambda value=label: self._choose_color(value),
                width=5,
            ).pack(side=tk.RIGHT)

        tools = ttk.LabelFrame(self.left_panel, text="4. 区域标注")
        tools.pack(fill=tk.X, padx=4, pady=5)
        brush_row = ttk.Frame(tools)
        brush_row.pack(fill=tk.X, padx=6, pady=(6, 3))
        ttk.Label(brush_row, text="画笔").pack(side=tk.LEFT)
        self.brush_size_var = tk.StringVar(value="1")
        self.brush_combo = ttk.Combobox(
            brush_row,
            textvariable=self.brush_size_var,
            values=("1", "3", "5", "7"),
            state="readonly",
            width=6,
        )
        self.brush_combo.pack(side=tk.LEFT, padx=8)
        ttk.Label(brush_row, text="格 × 格").pack(side=tk.LEFT)
        ttk.Label(
            tools,
            text="左键拖动连续涂抹；Shift+左拖矩形填充；右键拖动擦为背景；中键平移；滚轮缩放。",
            wraplength=440,
            foreground="#555555",
        ).pack(fill=tk.X, padx=6, pady=(0, 5))
        self._button(tools, "快捷键设置", self._open_shortcut_dialog).pack(
            fill=tk.X, padx=6, pady=(0, 6)
        )

        multi = ttk.LabelFrame(self.left_panel, text="5. 多尺度导出")
        multi.pack(fill=tk.X, padx=4, pady=5)
        scale_row = ttk.Frame(multi)
        scale_row.pack(fill=tk.X, padx=6, pady=(6, 3))
        ttk.Label(scale_row, text="核:步长").pack(side=tk.LEFT)
        self.scale_specs_var = tk.StringVar(
            value=str(self.config.get("default_scale_specs", "1:1,2:2,3:3,4:4"))
        )
        ttk.Entry(scale_row, textvariable=self.scale_specs_var).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 0)
        )
        mode_titles = {
            "safety_first": "安全优先",
            "balanced": "平衡",
            "precision_first": "精度优先",
            RISK_PRIORITY_MODE: "最高风险优先",
        }
        self.mode_enabled_vars = {}
        self.threshold_vars = {}
        heading = ttk.Frame(multi)
        heading.pack(fill=tk.X, padx=6)
        ttk.Label(heading, text="模式", width=12).grid(row=0, column=0, sticky=tk.W)
        for col, text in enumerate(("布料", "多层", "褶皱"), start=1):
            ttk.Label(heading, text=text, width=7).grid(row=0, column=col)
        for name in ("safety_first", "balanced", "precision_first", RISK_PRIORITY_MODE):
            config_mode = self.config.get("aggregation_modes", {}).get(
                name, MODE_THRESHOLDS.get(name, {"enabled": False})
            )
            row = ttk.Frame(multi)
            row.pack(fill=tk.X, padx=6, pady=1)
            enabled = tk.BooleanVar(value=bool(config_mode.get("enabled", name == "balanced")))
            self.mode_enabled_vars[name] = enabled
            ttk.Checkbutton(
                row, text=mode_titles[name], variable=enabled, width=12
            ).grid(row=0, column=0, sticky=tk.W)
            self.threshold_vars[name] = {}
            if name == RISK_PRIORITY_MODE:
                ttk.Label(row, text="无需阈值", foreground="#666666").grid(
                    row=0, column=1, columnspan=3, sticky=tk.W, padx=2
                )
                continue
            for col, key in enumerate(("cloth", "multi", "wrinkle"), start=1):
                var = tk.StringVar(
                    value=str(config_mode.get(key, MODE_THRESHOLDS[name][key]))
                )
                self.threshold_vars[name][key] = var
                ttk.Entry(row, textvariable=var, width=7).grid(
                    row=0, column=col, padx=2
                )

        sampling_config = self.config.get("v7_export", DEFAULT_EXPORT_OPTIONS)
        sampling = ttk.LabelFrame(multi, text="训练采样策略")
        sampling.pack(fill=tk.X, padx=6, pady=(6, 2))
        variant_row = ttk.Frame(sampling)
        variant_row.pack(fill=tk.X, padx=5, pady=(4, 2))
        self.export_full_var = tk.BooleanVar(
            value=bool(sampling_config.get("export_full", True))
        )
        self.export_sampled_var = tk.BooleanVar(
            value=True
        )
        self.export_full_var.set(True)
        ttk.Label(
            variant_row,
            text="固定同时导出：Full 全量评估版 + Sampled 训练版",
        ).pack(side=tk.LEFT)

        parameter_row = ttk.Frame(sampling)
        parameter_row.pack(fill=tk.X, padx=5, pady=2)
        ttk.Label(parameter_row, text="纯背景保留").pack(side=tk.LEFT)
        self.background_keep_percent_var = tk.StringVar(
            value=str(
                float(sampling_config.get("pure_background_keep_ratio", 0.0))
                * 100
            )
        )
        ttk.Entry(
            parameter_row,
            textvariable=self.background_keep_percent_var,
            width=7,
        ).pack(side=tk.LEFT, padx=(5, 2))
        ttk.Label(parameter_row, text="%").pack(side=tk.LEFT)
        ttk.Label(parameter_row, text="种子").pack(side=tk.LEFT, padx=(12, 0))
        self.sampling_seed_var = tk.StringVar(
            value=str(sampling_config.get("sampling_seed", 0))
        )
        ttk.Entry(
            parameter_row, textvariable=self.sampling_seed_var, width=8
        ).pack(side=tk.LEFT, padx=(5, 0))

        weight_row = ttk.Frame(sampling)
        weight_row.pack(fill=tk.X, padx=5, pady=(2, 4))
        ttk.Label(weight_row, text="平方根采样最大权重").pack(side=tk.LEFT)
        self.max_sampling_weight_var = tk.StringVar(
            value=str(sampling_config.get("max_sampling_weight", 5.0))
        )
        ttk.Entry(
            weight_row, textvariable=self.max_sampling_weight_var, width=8
        ).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Label(
            sampling,
            text="纯背景只在 Sampled 中过滤；Full 始终保留全部窗口。",
            foreground="#555555",
        ).pack(fill=tk.X, padx=5, pady=(0, 4))

        export_buttons = ttk.Frame(multi)
        export_buttons.pack(fill=tk.X, padx=6, pady=5)
        self._button(export_buttons, "估算数量", self._estimate_export).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 2)
        )
        self._button(export_buttons, "开始导出", self._start_export).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=2
        )
        self.cancel_export_button = self._button(
            export_buttons, "取消", self._cancel_export, state=tk.DISABLED
        )
        self.cancel_export_button.pack(side=tk.LEFT, padx=(2, 0))
        metadata_buttons = ttk.Frame(multi)
        metadata_buttons.pack(fill=tk.X, padx=6, pady=(0, 4))
        self._button(
            metadata_buttons,
            "检查并修复标注元数据",
            self._check_and_repair_metadata,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.metadata_status_var = tk.StringVar(value="元数据尚未检查")
        ttk.Label(
            multi,
            textvariable=self.metadata_status_var,
            wraplength=440,
            foreground="#555555",
        ).pack(fill=tk.X, padx=6, pady=(0, 4))
        self.export_progress = ttk.Progressbar(multi, mode="determinate")
        self.export_progress.pack(fill=tk.X, padx=6)
        self.export_info_label = ttk.Label(
            multi,
            text="输出到 exports/{模式}/kernel_K_stride_S_size_WxH",
            wraplength=440,
            foreground="#555555",
        )
        self.export_info_label.pack(fill=tk.X, padx=6, pady=(3, 6))

        display = ttk.LabelFrame(self.left_panel, text="6. 显示与协作")
        display.pack(fill=tk.X, padx=4, pady=5)
        self.show_overlay_var = tk.BooleanVar(
            value=bool(self.config.get("show_overlay", True))
        )
        ttk.Checkbutton(
            display,
            text="显示类别颜色覆盖（背景 0 始终透明）",
            variable=self.show_overlay_var,
            command=self._schedule_refresh,
        ).pack(anchor=tk.W, padx=6, pady=(5, 2))
        collab_row = ttk.Frame(display)
        collab_row.pack(fill=tk.X, padx=6, pady=3)
        self._button(collab_row, "生成项目清单", self._create_project_manifest).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 2)
        )
        self._button(collab_row, "导出回传包", self._export_worker_bundle).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(2, 0)
        )

        actions = ttk.LabelFrame(self.left_panel, text="7. 操作")
        actions.pack(fill=tk.X, padx=4, pady=5)
        nav = ttk.Frame(actions)
        nav.pack(fill=tk.X, padx=6, pady=(6, 3))
        self._button(nav, "上一张 Q", self._previous_image).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 2)
        )
        self._button(nav, "下一张 E", self._next_image).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(2, 0)
        )
        save = ttk.Frame(actions)
        save.pack(fill=tk.X, padx=6, pady=3)
        self._button(save, "保存当前 Ctrl+S", self._save_current).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 2)
        )
        self._button(save, "保存并下一张 Space", self._save_current_and_next).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(2, 0)
        )
        edit = ttk.Frame(actions)
        edit.pack(fill=tk.X, padx=6, pady=(3, 6))
        self._button(edit, "撤销", self._undo).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 2)
        )
        self._button(edit, "重做", self._redo).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=2
        )
        self._button(edit, "适应窗口", self._fit_window).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(2, 0)
        )

        canvas_host = ttk.Frame(main)
        canvas_host.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.canvas = tk.Canvas(canvas_host, bg="#242424", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.status_var = tk.StringVar(value="请先选择图片文件夹和输出文件夹")
        status = ttk.Label(canvas_host, textvariable=self.status_var, anchor=tk.W)
        status.pack(fill=tk.X, pady=(4, 0))

        self.canvas.bind("<Configure>", lambda _e: self._schedule_refresh(True))
        self.canvas.bind("<ButtonPress-1>", self._start_left_stroke)
        self.canvas.bind("<B1-Motion>", self._move_stroke)
        self.canvas.bind("<ButtonRelease-1>", self._end_stroke)
        self.canvas.bind("<ButtonPress-3>", self._start_right_stroke)
        self.canvas.bind("<B3-Motion>", self._move_stroke)
        self.canvas.bind("<ButtonRelease-3>", self._end_stroke)
        self.canvas.bind("<ButtonPress-2>", self._start_pan)
        self.canvas.bind("<B2-Motion>", self._move_pan)
        self.canvas.bind("<ButtonRelease-2>", lambda _e: setattr(self, "pan_start", None))
        self.canvas.bind("<MouseWheel>", self._mousewheel)

    def _button(self, parent, text, command, **kwargs):
        button = ttk.Button(parent, text=text, command=command, takefocus=False, **kwargs)
        button.bind("<KeyPress-space>", self._save_current_and_next)
        button.bind("<KeyRelease-space>", lambda _event: "break")
        return button

    def _relative_name(self, path: Path | None = None) -> str:
        if path is None:
            if self.current_index < 0:
                return ""
            path = self.image_paths[self.current_index]
        try:
            return path.relative_to(self.image_folder).as_posix()
        except (ValueError, TypeError):
            return path.name

    def _current_key(self) -> str:
        return self._relative_name()

    def _base_dimensions(self) -> tuple[int, int]:
        try:
            width = int(self.base_w_var.get())
            height = int(self.base_h_var.get())
        except ValueError as exc:
            raise ValueError("基础块宽高必须是正整数") from exc
        if self.link_base_var.get():
            height = width
            self.base_h_var.set(str(height))
        if width < 1 or height < 1:
            raise ValueError("基础块宽高必须是正整数")
        return width, height

    def _grid_is_locked(self) -> bool:
        if self.annotation_store.get("images"):
            return True
        if self.dirty_images:
            return True
        return any(label != "0" for item in self.annotation_cache.values() for label in item["cells"].values())

    def _select_image_folder(self) -> None:
        if not self._confirm_project_change():
            return
        folder = filedialog.askdirectory(title="选择图片文件夹")
        if not folder:
            return
        self.image_folder = Path(folder)
        self.image_paths = sorted(
            (
                path
                for path in self.image_folder.rglob("*")
                if is_source_image_path(self.image_folder, path)
            ),
            key=lambda path: natural_path_sort_key(path.relative_to(self.image_folder)),
        )
        self.input_label.config(text=f"{folder}\n{len(self.image_paths)} 张图片")
        self.annotation_cache.clear()
        self.dirty_images.clear()
        self.current_index = 0 if self.image_paths else -1
        if self.output_folder:
            self._load_annotation_store()
        self._load_current_image()
        self.canvas.focus_set()

    def _select_output_folder(self) -> None:
        if self.dirty_images and not messagebox.askyesno(
            "未保存标注", "更换输出目录不会写入当前未保存标注。确定继续吗？"
        ):
            return
        folder = filedialog.askdirectory(title="选择输出文件夹")
        if not folder:
            return
        self._compact_annotation_store()
        self.output_folder = Path(folder)
        self.output_folder.mkdir(parents=True, exist_ok=True)
        self.output_label.config(text=folder)
        self._load_annotation_store()
        if self.current_index >= 0:
            self._load_current_image()
        self.canvas.focus_set()

    def _confirm_project_change(self) -> bool:
        if not self.dirty_images:
            return True
        return messagebox.askyesno(
            "未保存标注",
            f"仍有 {len(self.dirty_images)} 张图片只保存在内存中。放弃并切换项目吗？",
        )

    def _load_annotation_store(self) -> None:
        self.annotation_store = {"schema_version": 6, "images": {}}
        if not self.output_folder:
            return
        self.annotation_store = collect_history_store(self.image_folder, self.output_folder)
        warnings = self.annotation_store.pop("_history_warnings", [])
        self.annotation_store.pop("_history_sources", None)
        if warnings:
            shown = "\n".join(str(item) for item in warnings[:5])
            if len(warnings) > 5:
                shown += f"\n... 还有 {len(warnings) - 5} 个警告"
            messagebox.showwarning("历史标注", shown)
        stored_width = self.annotation_store.get("base_width")
        stored_height = self.annotation_store.get("base_height")
        if (not stored_width or not stored_height) and self.annotation_store.get("images"):
            first = next(iter(self.annotation_store["images"].values()))
            stored_width = first.get("base_width")
            stored_height = first.get("base_height")
            if stored_width and stored_height:
                self.annotation_store["base_width"] = stored_width
                self.annotation_store["base_height"] = stored_height
        if stored_width and stored_height:
            self.base_w_var.set(str(stored_width))
            self.base_h_var.set(str(stored_height))
        self.annotation_cache.clear()
        self.dirty_images.clear()

    def _compact_annotation_store(self) -> None:
        if not self.output_folder or not self.annotation_store.get("images"):
            return
        atomic_json_write(self.output_folder / "annotations.json", self.annotation_store)

    def _load_current_image(self) -> None:
        if self.current_index < 0 or self.current_index >= len(self.image_paths):
            self.original_image = None
            self.current_cells = {}
            self._schedule_refresh(True)
            return
        path = self.image_paths[self.current_index]
        try:
            with Image.open(path) as image:
                self.original_image = image.convert("RGB")
        except OSError as exc:
            messagebox.showerror("图片读取失败", f"{path}\n{exc}")
            return
        try:
            base_w, base_h = self._base_dimensions()
            if base_w > self.original_image.width or base_h > self.original_image.height:
                raise ValueError("基础块不能大于图片尺寸")
        except ValueError as exc:
            messagebox.showerror("基础网格", str(exc))
            return
        plan = make_padding_plan(
            self.original_image.width, self.original_image.height, base_w, base_h
        )
        self.grid_rows, self.grid_cols = plan.rows, plan.cols
        key = self._current_key()
        cached = self.annotation_cache.get(key)
        if cached and cached["rows"] == plan.rows and cached["cols"] == plan.cols:
            self.current_cells = dict(cached["cells"])
        else:
            stored = self.annotation_store.get("images", {}).get(key)
            if (
                isinstance(stored, dict)
                and stored.get("base_width") == base_w
                and stored.get("base_height") == base_h
                and stored.get("rows") == plan.rows
                and stored.get("cols") == plan.cols
            ):
                labels = stored.get("labels", [])
                self.current_cells = {
                    (row, col): str(labels[row][col])
                    for row in range(plan.rows)
                    for col in range(plan.cols)
                    if row < len(labels) and col < len(labels[row])
                }
            else:
                self.current_cells = {}
            for row in range(plan.rows):
                for col in range(plan.cols):
                    self.current_cells.setdefault((row, col), "0")
        self.history.clear()
        self.redo_history.clear()
        self.selected_cell = None
        self.copy_preview = None
        self.view_mode = "fit"
        self.pan_x = self.pan_y = 0.0
        self.grid_info_label.config(
            text=(
                f"图片 {self.original_image.width}×{self.original_image.height}；"
                f"网格 {plan.cols}×{plan.rows}；Padding 右 {plan.pad_right}、下 {plan.pad_bottom} px"
            )
        )
        self._update_status()
        self._schedule_refresh(True)

    def _cache_current(self) -> None:
        if self.current_index < 0 or not self.current_cells:
            return
        self.annotation_cache[self._current_key()] = {
            "rows": self.grid_rows,
            "cols": self.grid_cols,
            "cells": dict(self.current_cells),
        }

    def _previous_image(self, event=None):
        if self.current_index > 0:
            self._cache_current()
            self.current_index -= 1
            self._load_current_image()
        return "break" if event is not None else None

    def _next_image(self, event=None):
        if self.current_index >= 0 and self.current_index < len(self.image_paths) - 1:
            self._cache_current()
            self.current_index += 1
            self._load_current_image()
        return "break" if event is not None else None

    def _apply_base_grid(self) -> None:
        try:
            width, height = self._base_dimensions()
        except ValueError as exc:
            messagebox.showerror("基础网格", str(exc))
            return
        old_width = int(self.config.get("default_base_width", 16))
        old_height = int(self.config.get("default_base_height", 16))
        if self._grid_is_locked() and (width, height) != (old_width, old_height):
            messagebox.showerror(
                "基础网格已锁定",
                "项目已存在人工标注，不能改变基础块尺寸。请新建输出目录后再修改。",
            )
            self.base_w_var.set(str(old_width))
            self.base_h_var.set(str(old_height))
            return
        self.config["default_base_width"] = width
        self.config["default_base_height"] = height
        self.config["link_base_dimensions"] = bool(self.link_base_var.get())
        self._save_app_config()
        self.annotation_cache.clear()
        self._load_current_image()

    def _label_changed(self) -> None:
        self._update_status()
        self.canvas.focus_set()

    def _select_label(self, label: str, event=None):
        if label in LABELS:
            self.layer_var.set(label)
            self._label_changed()
        return "break" if event is not None else None

    def _choose_color(self, label: str) -> None:
        chosen = colorchooser.askcolor(self.layer_colors[label], title=f"标签 {label} 颜色")[1]
        if chosen:
            self.layer_colors[label] = chosen
            self.config["layer_colors"] = dict(self.layer_colors)
            self._save_app_config()
            self._schedule_refresh()

    def _cell_at(self, x: float, y: float) -> tuple[int, int] | None:
        if self.original_image is None or self.scale <= 0:
            return None
        image_x = (x - self.offset_x) / self.scale
        image_y = (y - self.offset_y) / self.scale
        if image_x < 0 or image_y < 0:
            return None
        if image_x >= self.original_image.width or image_y >= self.original_image.height:
            return None
        try:
            base_w, base_h = self._base_dimensions()
        except ValueError:
            return None
        col = min(int(image_x // base_w), self.grid_cols - 1)
        row = min(int(image_y // base_h), self.grid_rows - 1)
        return row, col

    @staticmethod
    def _line_cells(start: tuple[int, int], end: tuple[int, int]):
        r0, c0 = start
        r1, c1 = end
        dr = abs(r1 - r0)
        dc = abs(c1 - c0)
        sr = 1 if r0 < r1 else -1
        sc = 1 if c0 < c1 else -1
        err = dc - dr
        while True:
            yield r0, c0
            if (r0, c0) == (r1, c1):
                break
            twice = 2 * err
            if twice > -dr:
                err -= dr
                c0 += sc
            if twice < dc:
                err += dc
                r0 += sr

    def _start_left_stroke(self, event) -> None:
        self._start_stroke(event, self.layer_var.get(), bool(event.state & 0x0001))

    def _start_right_stroke(self, event) -> None:
        self._start_stroke(event, "0", False)

    def _start_stroke(self, event, label: str, rectangle: bool) -> None:
        if not self._ready_to_annotate():
            return
        cell = self._cell_at(event.x, event.y)
        if cell is None:
            return
        self.copy_preview = None
        self.stroke_originals = {}
        self.stroke_last_cell = cell
        self.stroke_label = label
        self.stroke_rectangle = rectangle
        self.rectangle_start = cell if rectangle else None
        if rectangle:
            self.rectangle_preview = (cell, cell, label)
        else:
            self._paint_brush(cell, label)
        self.selected_cell = cell
        self._schedule_refresh()

    def _move_stroke(self, event) -> None:
        if self.stroke_originals is None:
            return
        cell = self._cell_at(event.x, event.y)
        if cell is None:
            return
        if self.stroke_rectangle:
            self.rectangle_preview = (self.rectangle_start, cell, self.stroke_label)
        else:
            for line_cell in self._line_cells(self.stroke_last_cell, cell):
                self._paint_brush(line_cell, self.stroke_label)
            self.stroke_last_cell = cell
        self.selected_cell = cell
        self._schedule_refresh()

    def _paint_brush(self, center: tuple[int, int], label: str) -> None:
        size = int(self.brush_size_var.get())
        radius = size // 2
        for row in range(center[0] - radius, center[0] + radius + 1):
            for col in range(center[1] - radius, center[1] + radius + 1):
                if 0 <= row < self.grid_rows and 0 <= col < self.grid_cols:
                    key = (row, col)
                    old = self.current_cells.get(key, "0")
                    self.stroke_originals.setdefault(key, old)
                    self.current_cells[key] = label

    def _end_stroke(self, event) -> None:
        if self.stroke_originals is None:
            return
        if self.stroke_rectangle and self.rectangle_preview:
            start, end, label = self.rectangle_preview
            r1, r2 = sorted((start[0], end[0]))
            c1, c2 = sorted((start[1], end[1]))
            for row in range(r1, r2 + 1):
                for col in range(c1, c2 + 1):
                    key = (row, col)
                    old = self.current_cells.get(key, "0")
                    self.stroke_originals.setdefault(key, old)
                    self.current_cells[key] = label
        changes = []
        for (row, col), old in self.stroke_originals.items():
            new = self.current_cells[(row, col)]
            if old != new:
                changes.append((row, col, old, new))
        if changes:
            self.history.append(changes)
            self.redo_history.clear()
            self.dirty_images.add(self._current_key())
        self.stroke_originals = None
        self.stroke_last_cell = None
        self.stroke_rectangle = False
        self.rectangle_start = None
        self.rectangle_preview = None
        self._cache_current()
        self._update_status()
        self._schedule_refresh()

    def _undo(self, event=None):
        if self.history:
            changes = self.history.pop()
            for row, col, old, _new in changes:
                self.current_cells[(row, col)] = old
            self.redo_history.append(changes)
            self.dirty_images.add(self._current_key())
            self._cache_current()
            self._update_status()
            self._schedule_refresh()
        return "break" if event is not None else None

    def _redo(self, event=None):
        if self.redo_history:
            changes = self.redo_history.pop()
            for row, col, _old, new in changes:
                self.current_cells[(row, col)] = new
            self.history.append(changes)
            self.dirty_images.add(self._current_key())
            self._cache_current()
            self._update_status()
            self._schedule_refresh()
        return "break" if event is not None else None

    def _copy_previous_preview(self, event=None):
        if self.current_index <= 0:
            self.status_var.set("没有可复制的上一张图片")
            return "break" if event is not None else None
        self._cache_current()
        previous_path = self.image_paths[self.current_index - 1]
        previous_key = self._relative_name(previous_path)
        previous = self.annotation_cache.get(previous_key)
        if previous is None:
            stored = self.annotation_store.get("images", {}).get(previous_key)
            if stored:
                labels = stored.get("labels", [])
                previous = {
                    "rows": stored.get("rows"),
                    "cols": stored.get("cols"),
                    "cells": {
                        (row, col): str(labels[row][col])
                        for row in range(len(labels))
                        for col in range(len(labels[row]))
                    },
                }
        if not previous or previous["rows"] != self.grid_rows or previous["cols"] != self.grid_cols:
            self.status_var.set("上一张图片没有同尺寸网格标签，无法复制")
        else:
            self.copy_preview = dict(previous["cells"])
            self.status_var.set("上一帧标签预览中：Enter 确认，Esc 取消")
            self._schedule_refresh()
        return "break" if event is not None else None

    def _confirm_preview(self, event=None):
        if self.copy_preview is not None:
            changes = []
            for key, new in self.copy_preview.items():
                old = self.current_cells.get(key, "0")
                if old != new:
                    changes.append((key[0], key[1], old, new))
                    self.current_cells[key] = new
            if changes:
                self.history.append(changes)
                self.redo_history.clear()
                self.dirty_images.add(self._current_key())
            self.copy_preview = None
            self._cache_current()
            self._update_status()
            self._schedule_refresh()
        return "break" if event is not None else None

    def _cancel_operation(self, event=None):
        self.copy_preview = None
        self.rectangle_preview = None
        self.stroke_originals = None
        self.stroke_last_cell = None
        self._update_status()
        self._schedule_refresh()
        return "break" if event is not None else None

    def _change_brush(self, delta: int, event=None):
        values = [1, 3, 5, 7]
        current = int(self.brush_size_var.get())
        index = max(0, min(len(values) - 1, values.index(current) + delta))
        self.brush_size_var.set(str(values[index]))
        self._update_status()
        return "break" if event is not None else None

    def _ready_to_annotate(self) -> bool:
        if not self.image_folder or not self.output_folder:
            messagebox.showwarning("开始标注", "请先选择图片文件夹和输出文件夹。")
            return False
        return self.original_image is not None

    def _schedule_refresh(self, recompute_fit=False) -> None:
        if recompute_fit:
            self.view_mode = "fit"
        if self.refresh_pending:
            return
        self.refresh_pending = True
        self.root.after_idle(self._refresh_canvas)

    def _refresh_canvas(self) -> None:
        self.refresh_pending = False
        self.canvas.delete("all")
        width = max(1, self.canvas.winfo_width())
        height = max(1, self.canvas.winfo_height())
        if self.original_image is None:
            self.canvas.create_text(
                width / 2,
                height / 2,
                text="请先选择图片文件夹和输出文件夹",
                fill="#aaaaaa",
                font=("Microsoft YaHei UI", 16),
            )
            return
        if self.view_mode == "fit":
            self.scale = min(width / self.original_image.width, height / self.original_image.height)
            self.offset_x = (width - self.original_image.width * self.scale) / 2 + self.pan_x
            self.offset_y = (height - self.original_image.height * self.scale) / 2 + self.pan_y
        else:
            self.scale = self.zoom_factor
        shown_w = max(1, round(self.original_image.width * self.scale))
        shown_h = max(1, round(self.original_image.height * self.scale))
        shown = self.original_image.resize((shown_w, shown_h), Image.Resampling.LANCZOS)
        self.tk_image = ImageTk.PhotoImage(shown)
        self.canvas.create_image(
            self.offset_x, self.offset_y, anchor=tk.NW, image=self.tk_image
        )
        base_w, base_h = self._base_dimensions()
        self.cell_rects = {}
        for row in range(self.grid_rows):
            y1 = self.offset_y + row * base_h * self.scale
            y2 = self.offset_y + min(
                (row + 1) * base_h, self.original_image.height
            ) * self.scale
            for col in range(self.grid_cols):
                x1 = self.offset_x + col * base_w * self.scale
                x2 = self.offset_x + min(
                    (col + 1) * base_w, self.original_image.width
                ) * self.scale
                self.cell_rects[(row, col)] = (x1, y1, x2, y2)
                label = self.current_cells.get((row, col), "0")
                if (
                    self.show_overlay_var.get()
                    and label not in self.transparent_layers
                ):
                    self.canvas.create_rectangle(
                        x1,
                        y1,
                        x2,
                        y2,
                        fill=self.layer_colors[label],
                        outline="",
                        stipple=self.config.get("overlay_stipple", "gray25"),
                    )
        if self.copy_preview is not None:
            for key, label in self.copy_preview.items():
                rect = self.cell_rects.get(key)
                if not rect or label == "0":
                    continue
                self.canvas.create_rectangle(
                    *rect,
                    fill=self.layer_colors[label],
                    outline="#ffd54f",
                    stipple="gray50",
                    width=1,
                )
        for row in range(self.grid_rows + 1):
            y = self.offset_y + min(row * base_h, self.original_image.height) * self.scale
            self.canvas.create_line(
                self.offset_x,
                y,
                self.offset_x + self.original_image.width * self.scale,
                y,
                fill="#ffffff",
                width=1,
            )
        for col in range(self.grid_cols + 1):
            x = self.offset_x + min(col * base_w, self.original_image.width) * self.scale
            self.canvas.create_line(
                x,
                self.offset_y,
                x,
                self.offset_y + self.original_image.height * self.scale,
                fill="#ffffff",
                width=1,
            )
        if self.rectangle_preview:
            start, end, _label = self.rectangle_preview
            r1, r2 = sorted((start[0], end[0]))
            c1, c2 = sorted((start[1], end[1]))
            first = self.cell_rects[(r1, c1)]
            last = self.cell_rects[(r2, c2)]
            self.canvas.create_rectangle(
                first[0], first[1], last[2], last[3],
                outline="#00ffff", width=3, dash=(6, 3)
            )
        if self.selected_cell in self.cell_rects:
            self.canvas.create_rectangle(
                *self.cell_rects[self.selected_cell],
                outline="#00ffff",
                width=2,
            )

    def _start_pan(self, event) -> None:
        self.pan_start = (event.x, event.y)

    def _move_pan(self, event) -> None:
        if self.pan_start is None:
            return
        dx, dy = event.x - self.pan_start[0], event.y - self.pan_start[1]
        self.pan_start = (event.x, event.y)
        self.offset_x += dx
        self.offset_y += dy
        self.pan_x += dx
        self.pan_y += dy
        self.view_mode = "zoom"
        self._schedule_refresh()

    def _mousewheel(self, event) -> None:
        self._zoom(1.2 if event.delta > 0 else 1 / 1.2)

    def _zoom(self, factor: float, event=None):
        if self.original_image is not None:
            if self.view_mode == "fit":
                self.zoom_factor = self.scale
            self.view_mode = "zoom"
            self.zoom_factor = max(
                self.min_zoom, min(self.max_zoom, self.zoom_factor * factor)
            )
            self.scale = self.zoom_factor
            self._schedule_refresh()
        return "break" if event is not None else None

    def _fit_window(self, event=None):
        self.view_mode = "fit"
        self.pan_x = self.pan_y = 0.0
        self._schedule_refresh(True)
        return "break" if event is not None else None

    def _labels_matrix(self) -> list[list[str]]:
        return [
            [self.current_cells.get((row, col), "0") for col in range(self.grid_cols)]
            for row in range(self.grid_rows)
        ]

    def _image_sha(self, path: Path) -> str:
        key = str(path)
        if key not in self.sha_cache:
            self.sha_cache[key] = sha256_file(path)
        return self.sha_cache[key]

    def _save_current(self, event=None, quiet=False):
        if not self._ready_to_annotate():
            return "break" if event is not None else False
        self._cache_current()
        path = self.image_paths[self.current_index]
        relative = self._current_key()
        base_w, base_h = self._base_dimensions()
        digest = self._image_sha(path)
        image_id = path_based_image_id(relative)
        plan = make_padding_plan(
            self.original_image.width, self.original_image.height, base_w, base_h
        )
        labels = self._labels_matrix()
        dataset_fields = {}
        relative_parts = relative.replace("\\", "/").split("/", 1)
        if (
            self.image_folder
            and len(relative_parts) == 2
            and has_annotation_data(self.image_folder / relative_parts[0])
        ):
            dataset_fields = {
                "dataset": relative_parts[0],
                "source_relative_path": relative_parts[1],
            }
        payload = {
            "tool_version": 7,
            "image_id": image_id,
            "relative_path": relative,
            **dataset_fields,
            "sha256": digest,
            "original_width": plan.original_width,
            "original_height": plan.original_height,
            "padded_width": plan.padded_width,
            "padded_height": plan.padded_height,
            "pad_right": plan.pad_right,
            "pad_bottom": plan.pad_bottom,
            "padding_mode": "reflect",
            "padding_counted_as_valid_area": False,
            "base_width": base_w,
            "base_height": base_h,
            "rows": plan.rows,
            "cols": plan.cols,
            "labels": labels,
        }
        self.annotation_store.setdefault("images", {})[relative] = payload
        self.annotation_store.update(
            {
                "schema_version": 6,
                "tool_version": 7,
                "label_descriptions": dict(LAYER_DESCRIPTIONS),
                "base_width": base_w,
                "base_height": base_h,
            }
        )
        append_annotation_event(
            self.output_folder,
            self.annotator_var.get(),
            {
                **payload,
                "labels": labels,
            },
        )
        self.dirty_images.discard(relative)
        self.status_var.set(f"已保存：{relative} → {self.output_folder}")
        if not quiet:
            self.canvas.focus_set()
        return "break" if event is not None else True

    def _save_current_and_next(self, event=None):
        result = self._save_current(event=None, quiet=True)
        if result is True and self.current_index < len(self.image_paths) - 1:
            self._next_image()
        elif result is True:
            self.status_var.set("当前图片已保存，已到最后一张")
        self.canvas.focus_set()
        return "break" if event is not None else None

    def _save_all_dirty(self) -> bool:
        self._cache_current()
        original_index = self.current_index
        keys = list(self.dirty_images)
        key_to_index = {self._relative_name(path): index for index, path in enumerate(self.image_paths)}
        for key in keys:
            if key not in key_to_index:
                continue
            self.current_index = key_to_index[key]
            self._load_current_image()
            if self._save_current(quiet=True) is not True:
                self.current_index = original_index
                self._load_current_image()
                return False
        self.current_index = original_index
        self._load_current_image()
        return True

    def _selected_profiles(self) -> list[dict]:
        profiles = []
        for name in ("safety_first", "balanced", "precision_first", RISK_PRIORITY_MODE):
            if not self.mode_enabled_vars[name].get():
                continue
            if name == RISK_PRIORITY_MODE:
                profiles.append({"source_mode": name, "thresholds": None})
                continue
            thresholds = {}
            for key, var in self.threshold_vars[name].items():
                try:
                    value = float(var.get())
                except ValueError as exc:
                    raise ValueError(f"{name} 的 {key} 阈值不是数字") from exc
                if not 0 <= value <= 1:
                    raise ValueError(f"{name} 的阈值必须在 0 到 1 之间")
                thresholds[key] = value
            profiles.append({"source_mode": name, "thresholds": thresholds})
        if not profiles:
            raise ValueError("至少选择一种聚合模式")
        return profiles

    def _collect_export_records(self) -> dict:
        if not self.image_folder:
            return {"records": [], "sources": [], "missing": [], "warnings": []}
        return collect_export_records(
            self.image_folder,
            self.output_folder,
            self.annotation_store,
        )

    def _effective_export_output_folder(self) -> Path:
        target = resolve_export_output_folder(self.image_folder, self.output_folder)
        return target if target is not None else self.output_folder

    def _metadata_precheck(self, show_success=False) -> bool:
        if not self.image_folder or not self.output_folder:
            return True
        if not has_annotation_data(self.output_folder):
            return True
        try:
            base_w, base_h = self._base_dimensions()
            audit = audit_annotation_tree(
                self.image_folder,
                self.output_folder,
                base_w,
                base_h,
                compute_sha=False,
            )
            audit_path = write_metadata_audit_report(audit, self.output_folder)
        except (OSError, ValueError) as exc:
            messagebox.showerror("元数据检查失败", str(exc))
            return False

        status = (
            f"元数据检查：{audit['record_count']} 条记录，"
            f"{audit['repairable_count']} 个可修复项，"
            f"{audit['blocking_count']} 个阻塞项"
        )
        if hasattr(self, "metadata_status_var"):
            self.metadata_status_var.set(f"{status}；报告：{audit_path}")

        if audit["can_repair"]:
            if not messagebox.askyesno(
                "修复标注元数据",
                (
                    f"发现 {audit['repairable_count']} 个可自动修复的元数据问题。\n"
                    "修复前会备份 annotations.json，workers 历史日志不会被改写。\n"
                    "是否现在修复并重新检查？"
                ),
            ):
                return False
            try:
                repair = repair_annotation_tree(
                    self.image_folder,
                    self.output_folder,
                    base_w,
                    base_h,
                    compute_sha=False,
                    audit=audit,
                )
                audit = repair["after_audit"]
                write_metadata_audit_report(audit, self.output_folder)
                self._load_annotation_store()
            except (OSError, ValueError) as exc:
                messagebox.showerror("元数据修复失败", str(exc))
                return False
            status = (
                f"元数据修复完成：重写 {len(repair['rewritten_files'])} 个文件，"
                f"剩余阻塞项 {audit['blocking_count']} 个"
            )
            if hasattr(self, "metadata_status_var"):
                self.metadata_status_var.set(status)

        if audit["blocking_count"]:
            blockers = [
                item for item in audit["issues"] if item.get("severity") == "blocking"
            ][:8]
            detail = "\n".join(
                f"- {item.get('type')}: {item.get('root_relative_path') or item.get('annotation_file')}"
                for item in blockers
            )
            if audit["blocking_count"] > len(blockers):
                detail += f"\n... 还有 {audit['blocking_count'] - len(blockers)} 个阻塞项"
            messagebox.showerror(
                "元数据仍有阻塞项",
                "存在会导致导出不可靠的问题，已停止估算/导出。\n" + detail,
            )
            if hasattr(self, "metadata_status_var"):
                self.metadata_status_var.set(
                    f"元数据阻塞：{audit['blocking_count']} 个问题，已停止"
                )
            return False

        if show_success:
            messagebox.showinfo("元数据检查", "元数据检查通过，可以继续估算或导出。")
        return True

    def _check_and_repair_metadata(self) -> None:
        if not self.image_folder or not self.output_folder:
            messagebox.showwarning("元数据检查", "请先选择图片文件夹和输出文件夹。")
            return
        if self.dirty_images:
            if not messagebox.askyesno(
                "保存未保存标注",
                f"当前有 {len(self.dirty_images)} 张未保存标注。检查前先保存吗？",
            ):
                return
            if not self._save_all_dirty():
                return
        self._metadata_precheck(show_success=True)

    def _export_options(self) -> dict:
        try:
            keep_percent = float(self.background_keep_percent_var.get())
            seed = int(self.sampling_seed_var.get())
            max_weight = float(self.max_sampling_weight_var.get())
        except ValueError as exc:
            raise ValueError("采样比例、种子和最大权重必须是有效数字") from exc
        if not 0.0 <= keep_percent <= 100.0:
            raise ValueError("纯背景保留比例必须在 0% 到 100% 之间")
        if max_weight < 1.0:
            raise ValueError("平方根采样最大权重必须大于或等于 1")
        return {
            "export_full": True,
            "export_sampled": True,
            "pure_background_keep_ratio": keep_percent / 100.0,
            "sampling_seed": seed,
            "max_sampling_weight": max_weight,
        }

    def _estimate_export(self, show_dialog=True):
        try:
            specs = parse_scale_specs(self.scale_specs_var.get())
            profiles = self._selected_profiles()
            base_w, base_h = self._base_dimensions()
            options = self._export_options()
        except ValueError as exc:
            messagebox.showerror("导出配置", str(exc))
            return None
        if not self._metadata_precheck(show_success=False):
            return None
        export_info = self._collect_export_records()
        records = export_info["records"]
        if not records:
            estimate = {
                "candidate_count": 0,
                "pure_background_count": 0,
                "sampled_window_count": 0,
                "skipped_window_count": 0,
                "expected_output_samples": 0,
            }
        else:
            try:
                estimate = estimate_v7_export(
                    records,
                    base_w,
                    base_h,
                    specs,
                    profiles,
                    options,
                )
            except (OSError, ValueError) as exc:
                messagebox.showerror("导出配置", str(exc))
                return None
        dataset_names = sorted(
            {
                str(record.get("dataset") or "")
                for record in records
                if str(record.get("dataset") or "")
            }
        )
        text = (
            f"{len(records)} 张已保存标注原图；{len(specs)} 种尺度；"
            f"{len(profiles)} 种模式；{estimate['candidate_count']:,} 个候选窗口。\n"
            f"纯背景 {estimate['pure_background_count']:,} 个；"
            f"Sampled 保留 {estimate['sampled_window_count']:,} 个、"
            f"跳过 {estimate['skipped_window_count']:,} 个；"
            f"预计 {estimate['expected_output_samples']:,} 个模式样本。"
        )
        if dataset_names:
            text += f"\n{len(dataset_names)} 个材质目录：" + ", ".join(dataset_names)
        mode_lines = []
        for mode, counts in estimate.get("mode_class_counts", {}).items():
            full_counts = "/".join(str(counts["full"][label]) for label in LABELS)
            sampled_counts = "/".join(
                str(counts["sampled"][label]) for label in LABELS
            )
            mode_lines.append(
                f"{mode}: Full[0/1/2/3]={full_counts}，"
                f"Sampled[0/1/2/3]={sampled_counts}"
            )
        if mode_lines:
            text += "\n" + "\n".join(mode_lines)
        dataset_lines = []
        for dataset, counts in estimate.get("dataset_sampling_counts", {}).items():
            dataset_lines.append(
                f"{dataset}: 候选 {counts['candidate_count']}，"
                f"纯背景 {counts['pure_background_count']}，"
                f"Sampled 保留 {counts['sampled_window_count']}"
            )
        if dataset_lines:
            text += "\n按材质：\n" + "\n".join(dataset_lines)
        if export_info["missing"]:
            text += f"\n{len(export_info['missing'])} 个已标注图片找不到原图，已忽略。"
        if export_info["warnings"]:
            text += f"\n{len(export_info['warnings'])} 个标注文件读取警告。"
        if self.dirty_images:
            text += f"\n{len(self.dirty_images)} 张未保存标注未计入估算。"
        self.export_info_label.config(text=text)
        if show_dialog:
            messagebox.showinfo("导出数量", text)
        return estimate, specs, profiles, records, export_info, options

    def _start_export(self) -> None:
        if not self.image_folder or not self.output_folder:
            messagebox.showwarning("多尺度导出", "请先选择图片和输出文件夹。")
            return
        if self.dirty_images:
            if not messagebox.askyesno(
                "保存标注", f"有 {len(self.dirty_images)} 张未保存。导出前先保存这些标注吗？"
            ):
                return
            if not self._save_all_dirty():
                return
        estimate = self._estimate_export(show_dialog=False)
        if estimate is None:
            return
        estimate_info, specs, profiles, records, export_info, options = estimate
        total = estimate_info["expected_output_samples"]
        if not records:
            messagebox.showwarning("多尺度导出", "当前没有已保存的基础标注。")
            return
        dataset_names = sorted(
            {
                str(record.get("dataset") or "")
                for record in records
                if str(record.get("dataset") or "")
            }
        )
        source_text = (
            f"\n将保留 {len(dataset_names)} 个材质目录：{', '.join(dataset_names)}"
            if dataset_names
            else ""
        )
        missing_text = (
            f"\n有 {len(export_info['missing'])} 个已标注图片找不到原图，已忽略。"
            if export_info["missing"]
            else ""
        )
        export_output_folder = self._effective_export_output_folder()
        redirected_text = (
            f"\n输入输出目录相同，导出结果将写入同级目录：{export_output_folder}"
            if export_output_folder != self.output_folder
            else ""
        )
        if redirected_text:
            self.export_info_label.config(text=redirected_text.strip())
        if not messagebox.askyesno(
            "确认导出",
            f"将从 {len(records)} 张已保存原图导出 {total:,} 个模式样本。\n"
            f"Full 用于评估，Sampled 仅用于训练；本次结果写入新的签名目录。"
            f"{source_text}{missing_text}\n是否继续？",
        ):
            return
        self.export_cancelled = False
        self.cancel_export_button.config(state=tk.NORMAL)
        self.export_progress.configure(maximum=max(total, 1), value=0)

        def progress(done, expected, text):
            self.export_progress.configure(maximum=max(expected, 1), value=done)
            self.export_info_label.config(text=f"{done:,}/{expected:,}  {text}")
            self.root.update()

        try:
            result = export_multiscale_v7(
                records,
                export_output_folder,
                *self._base_dimensions(),
                specs,
                profiles,
                options,
                progress=progress,
                cancelled=lambda: self.export_cancelled,
            )
        except (OSError, ValueError) as exc:
            messagebox.showerror("导出失败", str(exc))
        else:
            state = "已取消" if result["cancelled"] else "完成"
            self.export_info_label.config(
                text=f"{state}：处理 {result['processed']:,}/{result['expected']:,} 个模式样本"
            )
            messagebox.showinfo(
                "多尺度导出",
                f"{state}。结果位于：\n{result['run_root']}",
            )
        finally:
            self.cancel_export_button.config(state=tk.DISABLED)
            self.canvas.focus_set()

    def _cancel_export(self) -> None:
        self.export_cancelled = True
        self.export_info_label.config(text="正在取消，完成当前裁剪后停止...")

    def _create_project_manifest(self) -> None:
        if not self.image_folder or not self.output_folder:
            messagebox.showwarning("协作项目", "请先选择图片和输出文件夹。")
            return
        target = self.output_folder / "project_manifest.json"
        self.status_var.set("正在计算图片 SHA256...")
        self.root.update_idletasks()

        def progress(done, total, name):
            self.status_var.set(f"项目清单 {done}/{total}: {name}")
            self.root.update_idletasks()

        try:
            result = create_project_manifest(self.image_folder, target, progress)
        except OSError as exc:
            messagebox.showerror("项目清单", str(exc))
            return
        self.status_var.set(f"项目清单已生成：{result['image_count']} 张图片")
        messagebox.showinfo("项目清单", f"已生成：\n{target}")

    def _export_worker_bundle(self) -> None:
        if not self.output_folder:
            messagebox.showwarning("协作回传", "请先选择输出文件夹。")
            return
        worker = self.annotator_var.get().strip() or "anonymous"
        destination = filedialog.asksaveasfilename(
            title="保存标注员回传包",
            defaultextension=".zip",
            initialfile=f"labeltool_v7_{worker}.zip",
            filetypes=[("ZIP", "*.zip")],
        )
        if not destination:
            return
        try:
            result = compact_worker_log(self.output_folder, worker, destination)
        except OSError as exc:
            messagebox.showerror("协作回传", str(exc))
            return
        messagebox.showinfo(
            "协作回传",
            f"已打包 {result['image_count']} 张图片的最新标注：\n{destination}",
        )

    def _open_shortcut_dialog(self) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title("个人快捷键设置")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.geometry("520x680")
        ttk.Label(
            dialog,
            text="点击输入框后直接按键。Backspace 清空；个人配置不会写入项目数据。",
            wraplength=480,
        ).pack(fill=tk.X, padx=12, pady=10)
        host = ttk.Frame(dialog)
        host.pack(fill=tk.BOTH, expand=True, padx=12)
        canvas = tk.Canvas(host, highlightthickness=0)
        scrollbar = ttk.Scrollbar(host, orient=tk.VERTICAL, command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        panel = ttk.Frame(canvas)
        window = canvas.create_window((0, 0), window=panel, anchor=tk.NW)
        panel.bind(
            "<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas.bind(
            "<Configure>", lambda e: canvas.itemconfigure(window, width=e.width)
        )
        variables = {}
        for row, (action, label, _default) in enumerate(SHORTCUT_ACTIONS):
            ttk.Label(panel, text=label).grid(row=row, column=0, sticky=tk.W, pady=3)
            var = tk.StringVar(value=self.shortcuts.get(action, ""))
            variables[action] = var
            entry = ttk.Entry(panel, textvariable=var, width=20)
            entry.grid(row=row, column=1, sticky=tk.EW, padx=(12, 0), pady=3)
            entry.bind(
                "<KeyPress>",
                lambda event, target=var: self._capture_shortcut(event, target),
            )
        panel.columnconfigure(1, weight=1)
        buttons = ttk.Frame(dialog)
        buttons.pack(fill=tk.X, padx=12, pady=10)
        ttk.Button(
            buttons,
            text="导入",
            command=lambda: self._import_shortcuts_into(variables),
        ).pack(side=tk.LEFT)
        ttk.Button(
            buttons,
            text="导出",
            command=lambda: self._export_shortcut_vars(variables),
        ).pack(side=tk.LEFT, padx=5)
        ttk.Button(
            buttons,
            text="恢复默认",
            command=lambda: [
                variables[action].set(default)
                for action, _label, default in SHORTCUT_ACTIONS
            ],
        ).pack(side=tk.LEFT)
        ttk.Button(
            buttons,
            text="应用",
            command=lambda: self._apply_shortcut_dialog(variables, dialog),
        ).pack(side=tk.RIGHT)
        ttk.Button(buttons, text="取消", command=dialog.destroy).pack(
            side=tk.RIGHT, padx=5
        )

    @staticmethod
    def _capture_shortcut(event, variable):
        value = event_to_shortcut(event)
        if value is not None:
            variable.set(value)
        return "break"

    def _validate_shortcuts(self, values: dict[str, str]) -> None:
        used = {}
        for action, value in values.items():
            sequences = shortcut_to_sequences(value)
            if not sequences:
                raise ValueError(f"{dict((a, l) for a, l, _d in SHORTCUT_ACTIONS)[action]} 未设置")
            for sequence in sequences:
                if sequence in used:
                    labels = {a: l for a, l, _d in SHORTCUT_ACTIONS}
                    raise ValueError(
                        f"{labels[action]} 与 {labels[used[sequence]]} 使用了重复快捷键 {value}"
                    )
                used[sequence] = action

    def _apply_shortcut_dialog(self, variables, dialog) -> None:
        values = {action: var.get().strip() for action, var in variables.items()}
        try:
            self._validate_shortcuts(values)
        except ValueError as exc:
            messagebox.showerror("快捷键冲突", str(exc), parent=dialog)
            return
        self.shortcuts = values
        self._save_personal_config()
        self._bind_shortcuts()
        dialog.destroy()
        self.canvas.focus_set()

    def _import_shortcuts_into(self, variables) -> None:
        path = filedialog.askopenfilename(
            title="导入个人快捷键", filetypes=[("JSON", "*.json")]
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as handle:
                loaded = json.load(handle)
            source = loaded.get("shortcuts", loaded)
            values = {action: str(source[action]) for action in variables}
            self._validate_shortcuts(values)
        except (OSError, KeyError, ValueError, json.JSONDecodeError) as exc:
            messagebox.showerror("导入快捷键", str(exc))
            return
        for action, value in values.items():
            variables[action].set(value)

    def _export_shortcut_vars(self, variables) -> None:
        values = {action: var.get().strip() for action, var in variables.items()}
        try:
            self._validate_shortcuts(values)
        except ValueError as exc:
            messagebox.showerror("导出快捷键", str(exc))
            return
        path = filedialog.asksaveasfilename(
            title="导出个人快捷键",
            defaultextension=".json",
            initialfile="labeltool_v7_shortcuts.json",
            filetypes=[("JSON", "*.json")],
        )
        if path:
            atomic_json_write(Path(path), {"schema_version": 1, "shortcuts": values})

    def _shortcut_callbacks(self):
        return {
            "previous_image": self._previous_image,
            "next_image": self._next_image,
            "save_and_next": self._save_current_and_next,
            "select_label_0": lambda event=None: self._select_label("0", event),
            "select_label_1": lambda event=None: self._select_label("1", event),
            "select_label_2": lambda event=None: self._select_label("2", event),
            "select_label_3": lambda event=None: self._select_label("3", event),
            "undo": self._undo,
            "redo": self._redo,
            "brush_smaller": lambda event=None: self._change_brush(-1, event),
            "brush_larger": lambda event=None: self._change_brush(1, event),
            "copy_previous": self._copy_previous_preview,
            "confirm_preview": self._confirm_preview,
            "cancel_operation": self._cancel_operation,
            "fit_window": self._fit_window,
            "zoom_in": lambda event=None: self._zoom(1.2, event),
            "zoom_out": lambda event=None: self._zoom(1 / 1.2, event),
            "save_current": self._save_current,
        }

    def _bind_shortcuts(self) -> None:
        for sequence in self.bound_sequences:
            self.root.unbind_all(sequence)
        self.bound_sequences.clear()
        callbacks = self._shortcut_callbacks()
        for action, value in self.shortcuts.items():
            callback = callbacks.get(action)
            if callback is None:
                continue
            for sequence in shortcut_to_sequences(value):
                self.root.bind_all(
                    sequence,
                    lambda event, fn=callback: self._dispatch_shortcut(event, fn),
                )
                self.bound_sequences.append(sequence)
        self.root.bind_all("<KeyRelease-space>", lambda _event: "break")
        self.bound_sequences.append("<KeyRelease-space>")

    def _dispatch_shortcut(self, event, callback):
        widget = getattr(event, "widget", None)
        if widget is not None:
            try:
                widget_class = widget.winfo_class()
            except tk.TclError:
                widget_class = ""
            if widget_class in {
                "Entry", "TEntry", "Spinbox", "TSpinbox",
                "Combobox", "TCombobox", "Text"
            }:
                return None
        result = callback(event)
        return "break" if result is None else result

    def _save_personal_config(self) -> None:
        self.personal["annotator_id"] = self.annotator_var.get().strip()
        self.personal["shortcuts"] = dict(self.shortcuts)
        atomic_json_write(PERSONAL_CONFIG_PATH, self.personal)

    def _save_app_config(self) -> None:
        self.config["schema_version"] = 7
        self.config["layer_colors"] = dict(self.layer_colors)
        self.config["show_overlay"] = bool(self.show_overlay_var.get())
        self.config["default_scale_specs"] = self.scale_specs_var.get()
        for name in self.mode_enabled_vars:
            self.config.setdefault("aggregation_modes", {}).setdefault(name, {})
            self.config["aggregation_modes"][name]["enabled"] = self.mode_enabled_vars[name].get()
            for key, var in self.threshold_vars[name].items():
                try:
                    self.config["aggregation_modes"][name][key] = float(var.get())
                except ValueError:
                    pass
        try:
            self.config["v7_export"] = self._export_options()
        except ValueError:
            pass
        atomic_json_write(CONFIG_PATH, self.config)

    def _update_status(self) -> None:
        if self.current_index < 0:
            self.status_var.set("未加载图片")
            return
        label = self.layer_var.get()
        dirty = "未保存" if self._current_key() in self.dirty_images else "已保存/未修改"
        self.status_var.set(
            f"{self.current_index + 1}/{len(self.image_paths)}  {self._current_key()}  |  "
            f"标签 {label} {LAYER_DESCRIPTIONS[label]}  |  画笔 {self.brush_size_var.get()}×"
            f"{self.brush_size_var.get()}  |  {dirty}"
        )

    def _on_close(self) -> None:
        if self.dirty_images and not messagebox.askyesno(
            "未保存标注",
            f"仍有 {len(self.dirty_images)} 张图片未写入输出目录。确定退出吗？",
        ):
            return
        self._save_personal_config()
        self._save_app_config()
        self._compact_annotation_store()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    LabelToolV7(root)
    root.mainloop()


if __name__ == "__main__":
    main()
