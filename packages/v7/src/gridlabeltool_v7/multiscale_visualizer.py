"""Standalone LabelTool v7 multi-scale reconstruction viewer."""

from __future__ import annotations

from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageDraw, ImageTk

from .visualization_core import (
    coverage_grid,
    fuse_ground_truth,
    fuse_predictions,
    group_records_by_image,
    load_predictions,
    load_scale_records,
    resolve_source_image,
)


LABEL_COLORS = {
    "0": (154, 160, 166),
    "1": (76, 175, 80),
    "2": (255, 0, 255),
    "3": (255, 0, 0),
}
LAYER_TITLES = {
    "ground_truth": "真值标签",
    "prediction": "模型预测",
    "difference": "预测差异",
    "coverage": "导出覆盖",
}
FUSION_TITLES = {
    "confidence_weighted": "置信度加权",
    "highest_risk": "最高风险",
}
LAYER_KEYS = {title: key for key, title in LAYER_TITLES.items()}
FUSION_KEYS = {title: key for key, title in FUSION_TITLES.items()}


class MultiscaleVisualizer:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("LabelTool v7 - 多尺度复原可视化")
        self.root.geometry("1450x900")
        self.root.minsize(1050, 680)

        self.run_root: Path | None = None
        self.source_root: Path | None = None
        self.scale_folders: dict[str, Path] = {}
        self.records_by_image: dict[str, list[dict]] = {}
        self.predictions: dict[str, dict] = {}
        self.image_keys: list[str] = []
        self.current_index = -1
        self.original_image: Image.Image | None = None
        self.rendered_image: Image.Image | None = None
        self.tk_image = None

        self._build_ui()
        self.root.after(100, self._refresh_canvas)

    def _build_ui(self) -> None:
        main = ttk.Frame(self.root)
        main.pack(fill=tk.BOTH, expand=True, padx=7, pady=7)

        panel = ttk.Frame(main, width=390)
        panel.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 7))
        panel.pack_propagate(False)

        source = ttk.LabelFrame(panel, text="1. 数据来源")
        source.pack(fill=tk.X, pady=(0, 6))
        ttk.Button(source, text="选择 v7 导出运行目录", command=self._select_run).pack(
            fill=tk.X, padx=6, pady=(6, 2)
        )
        self.run_label = ttk.Label(source, text="未选择", wraplength=360)
        self.run_label.pack(fill=tk.X, padx=6)

        ttk.Label(source, text="分支 / 聚合模式 / 尺度").pack(
            anchor=tk.W, padx=6, pady=(7, 2)
        )
        self.scale_var = tk.StringVar()
        self.scale_combo = ttk.Combobox(
            source, textvariable=self.scale_var, state="readonly"
        )
        self.scale_combo.pack(fill=tk.X, padx=6)
        self.scale_combo.bind("<<ComboboxSelected>>", lambda _e: self._load_scale())

        ttk.Button(source, text="选择预测 JSONL", command=self._select_predictions).pack(
            fill=tk.X, padx=6, pady=(7, 2)
        )
        self.prediction_label = ttk.Label(source, text="未加载预测", wraplength=360)
        self.prediction_label.pack(fill=tk.X, padx=6)
        ttk.Button(
            source, text="指定原图根目录", command=self._select_source_root
        ).pack(fill=tk.X, padx=6, pady=(7, 2))
        self.source_label = ttk.Label(source, text="优先使用 manifest 原路径", wraplength=360)
        self.source_label.pack(fill=tk.X, padx=6, pady=(0, 6))

        browse = ttk.LabelFrame(panel, text="2. 材质与原图")
        browse.pack(fill=tk.X, pady=6)
        self.image_var = tk.StringVar()
        self.image_combo = ttk.Combobox(
            browse, textvariable=self.image_var, state="readonly"
        )
        self.image_combo.pack(fill=tk.X, padx=6, pady=(6, 3))
        self.image_combo.bind("<<ComboboxSelected>>", self._image_selected)
        nav = ttk.Frame(browse)
        nav.pack(fill=tk.X, padx=6, pady=(2, 6))
        ttk.Button(nav, text="上一张", command=lambda: self._move_image(-1)).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 2)
        )
        ttk.Button(nav, text="下一张", command=lambda: self._move_image(1)).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(2, 0)
        )

        display = ttk.LabelFrame(panel, text="3. 显示")
        display.pack(fill=tk.X, pady=6)
        ttk.Label(display, text="图层").grid(
            row=0, column=0, sticky=tk.W, padx=6, pady=(6, 3)
        )
        self.layer_var = tk.StringVar(value=LAYER_TITLES["ground_truth"])
        layer_combo = ttk.Combobox(
            display,
            textvariable=self.layer_var,
            state="readonly",
            values=list(LAYER_KEYS),
            width=22,
        )
        layer_combo.grid(row=0, column=1, sticky=tk.EW, padx=6, pady=(6, 3))
        layer_combo.bind("<<ComboboxSelected>>", lambda _e: self._render_current())

        ttk.Label(display, text="重叠融合").grid(
            row=1, column=0, sticky=tk.W, padx=6, pady=3
        )
        self.fusion_var = tk.StringVar(value=FUSION_TITLES["confidence_weighted"])
        fusion_combo = ttk.Combobox(
            display,
            textvariable=self.fusion_var,
            state="readonly",
            values=list(FUSION_KEYS),
            width=22,
        )
        fusion_combo.grid(row=1, column=1, sticky=tk.EW, padx=6, pady=3)
        fusion_combo.bind("<<ComboboxSelected>>", lambda _e: self._render_current())

        ttk.Label(display, text="覆盖透明度").grid(
            row=2, column=0, sticky=tk.W, padx=6, pady=3
        )
        self.alpha_var = tk.IntVar(value=42)
        alpha = ttk.Scale(
            display,
            from_=0,
            to=100,
            variable=self.alpha_var,
            command=lambda _value: self._render_current(),
        )
        alpha.grid(row=2, column=1, sticky=tk.EW, padx=6, pady=3)
        self.show_grid_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            display,
            text="显示基础网格",
            variable=self.show_grid_var,
            command=self._render_current,
        ).grid(row=3, column=0, columnspan=2, sticky=tk.W, padx=6, pady=3)
        display.columnconfigure(1, weight=1)

        ttk.Button(
            panel, text="保存当前叠加图", command=self._save_current
        ).pack(fill=tk.X, pady=6)
        self.status_var = tk.StringVar(value="请选择 v7 导出运行目录")
        ttk.Label(
            panel, textvariable=self.status_var, wraplength=370, foreground="#555555"
        ).pack(fill=tk.X, pady=5)

        canvas_host = ttk.Frame(main)
        canvas_host.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.canvas = tk.Canvas(canvas_host, bg="#222222", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.canvas.bind("<Configure>", lambda _e: self._refresh_canvas())

    def _select_run(self) -> None:
        folder = filedialog.askdirectory(title="选择 v7_run_* 导出目录")
        if not folder:
            return
        root = Path(folder)
        candidates = sorted(
            {
                path.parent
                for path in root.rglob("manifest.jsonl")
                if "layer_4" not in {part.lower() for part in path.parts}
            }
        )
        if not candidates:
            messagebox.showerror("导出目录", "未找到 manifest.jsonl")
            return
        self.run_root = root
        self.scale_folders = {
            path.relative_to(root).as_posix(): path for path in candidates
        }
        values = list(self.scale_folders)
        self.scale_combo.configure(values=values)
        self.scale_var.set(values[0])
        self.run_label.config(text=f"{root}\n{len(values)} 个分支/模式/尺度")
        self._load_scale()

    def _load_scale(self) -> None:
        folder = self.scale_folders.get(self.scale_var.get())
        if folder is None:
            return
        try:
            records, _root = load_scale_records(folder)
        except (OSError, ValueError) as exc:
            messagebox.showerror("读取导出记录", str(exc))
            return
        self.records_by_image = group_records_by_image(records)
        self.image_keys = list(self.records_by_image)
        self.image_combo.configure(values=self.image_keys)
        self.current_index = 0 if self.image_keys else -1
        if self.current_index >= 0:
            self.image_var.set(self.image_keys[0])
        self._load_current_image()

    def _select_predictions(self) -> None:
        path = filedialog.askopenfilename(
            title="选择模型预测 JSONL",
            filetypes=[("JSON Lines", "*.jsonl"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            self.predictions = load_predictions(path)
        except (OSError, ValueError) as exc:
            messagebox.showerror("预测文件", str(exc))
            return
        self.prediction_label.config(
            text=f"{path}\n{len(self.predictions)} 条预测"
        )
        self._render_current()

    def _select_source_root(self) -> None:
        folder = filedialog.askdirectory(title="选择原图根目录")
        if not folder:
            return
        self.source_root = Path(folder)
        self.source_label.config(text=folder)
        self._load_current_image()

    def _image_selected(self, _event=None) -> None:
        try:
            self.current_index = self.image_keys.index(self.image_var.get())
        except ValueError:
            return
        self._load_current_image()

    def _move_image(self, delta: int) -> None:
        if not self.image_keys:
            return
        self.current_index = max(
            0, min(len(self.image_keys) - 1, self.current_index + delta)
        )
        self.image_var.set(self.image_keys[self.current_index])
        self._load_current_image()

    def _current_records(self) -> list[dict]:
        if not (0 <= self.current_index < len(self.image_keys)):
            return []
        return self.records_by_image[self.image_keys[self.current_index]]

    def _load_current_image(self) -> None:
        records = self._current_records()
        if not records:
            self.original_image = None
            self.rendered_image = None
            self._refresh_canvas()
            return
        path = resolve_source_image(records[0], self.source_root)
        if path is None:
            self.original_image = None
            self.rendered_image = None
            self.status_var.set("找不到原图，请指定原图根目录")
            self._refresh_canvas()
            return
        try:
            with Image.open(path) as image:
                self.original_image = image.convert("RGB")
        except OSError as exc:
            messagebox.showerror("原图读取失败", f"{path}\n{exc}")
            return
        self._render_current()

    def _render_current(self) -> None:
        records = self._current_records()
        if self.original_image is None or not records:
            self._refresh_canvas()
            return
        method = FUSION_KEYS.get(self.fusion_var.get(), "confidence_weighted")
        layer = LAYER_KEYS.get(self.layer_var.get(), "ground_truth")
        alpha = max(0, min(255, round(int(self.alpha_var.get()) * 2.55)))
        output = self.original_image.convert("RGBA")
        overlay = Image.new("RGBA", output.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        ground_truth = fuse_ground_truth(records, method)
        prediction = (
            fuse_predictions(records, self.predictions, method)
            if self.predictions
            else None
        )
        coverage = coverage_grid(records)
        base_width = ground_truth["base_width"]
        base_height = ground_truth["base_height"]

        for row in range(ground_truth["rows"]):
            for col in range(ground_truth["cols"]):
                x1 = col * base_width
                y1 = row * base_height
                x2 = min((col + 1) * base_width, output.width)
                y2 = min((row + 1) * base_height, output.height)
                if x1 >= output.width or y1 >= output.height:
                    continue
                color = None
                if layer == "ground_truth":
                    label = ground_truth["labels"][row][col]
                    color = LABEL_COLORS.get(label) if label is not None else (255, 193, 7)
                elif layer == "prediction":
                    label = prediction["labels"][row][col] if prediction else None
                    color = LABEL_COLORS.get(label) if label is not None else (255, 193, 7)
                elif layer == "difference":
                    truth = ground_truth["labels"][row][col]
                    predicted = prediction["labels"][row][col] if prediction else None
                    if predicted is None:
                        color = (255, 193, 7)
                    elif predicted == truth:
                        color = (46, 204, 113)
                    else:
                        color = (231, 76, 60)
                else:
                    state = coverage["states"][row][col]
                    color = {
                        "retained": (36, 116, 210),
                        "skipped": (128, 128, 128),
                        "uncovered": (0, 0, 0),
                    }[state]
                draw.rectangle((x1, y1, x2, y2), fill=(*color, alpha))
                if self.show_grid_var.get():
                    draw.rectangle((x1, y1, x2, y2), outline=(255, 255, 255, 145))

        self.rendered_image = Image.alpha_composite(output, overlay).convert("RGB")
        missing = prediction["missing_predictions"] if prediction else len(records)
        self.status_var.set(
            f"{self.current_index + 1}/{len(self.image_keys)}  "
            f"{self.image_keys[self.current_index]}\n"
            f"{len(records)} 个窗口；匹配预测 {len(records) - missing}；"
            f"缺失预测 {missing}；图层 {LAYER_TITLES[layer]}；"
            f"融合 {FUSION_TITLES[method]}"
        )
        self._refresh_canvas()

    def _refresh_canvas(self) -> None:
        self.canvas.delete("all")
        width = max(1, self.canvas.winfo_width())
        height = max(1, self.canvas.winfo_height())
        if self.rendered_image is None:
            self.canvas.create_text(
                width / 2,
                height / 2,
                text="请选择导出目录并确认原图路径",
                fill="#aaaaaa",
                font=("Microsoft YaHei UI", 16),
            )
            return
        scale = min(width / self.rendered_image.width, height / self.rendered_image.height)
        shown = self.rendered_image.resize(
            (
                max(1, round(self.rendered_image.width * scale)),
                max(1, round(self.rendered_image.height * scale)),
            ),
            Image.Resampling.LANCZOS,
        )
        self.tk_image = ImageTk.PhotoImage(shown)
        self.canvas.create_image(width / 2, height / 2, image=self.tk_image)

    def _save_current(self) -> None:
        if self.rendered_image is None:
            messagebox.showwarning("保存叠加图", "当前没有可保存的图像")
            return
        key = (
            self.image_keys[self.current_index]
            if 0 <= self.current_index < len(self.image_keys)
            else "overlay"
        )
        safe_name = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in key)
        path = filedialog.asksaveasfilename(
            title="保存当前叠加图",
            defaultextension=".png",
            initialfile=(
                f"{safe_name}_"
                f"{LAYER_KEYS.get(self.layer_var.get(), 'ground_truth')}.png"
            ),
            filetypes=[("PNG", "*.png")],
        )
        if not path:
            return
        try:
            self.rendered_image.save(path, format="PNG")
        except OSError as exc:
            messagebox.showerror("保存叠加图", str(exc))


def main() -> None:
    root = tk.Tk()
    MultiscaleVisualizer(root)
    root.mainloop()


if __name__ == "__main__":
    main()
