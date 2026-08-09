"""
Grid-Based Image Annotation Tool
=================================
使用 Tkinter + Pillow 实现的网格标注工具。
支持两种网格模式：按像素大小自动计算（默认）和手动指定行列数。
像素模式下，最后一行/列网格块向左/上偏移以覆盖全图，保持所有块尺寸统一。

依赖：pip install Pillow
"""

import os
import math
import colorsys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from PIL import Image, ImageTk


class GridLabelTool:
    def __init__(self, root):
        self.root = root
        self.root.title("网格标注工具 - Grid Label Tool")
        self.root.geometry("1280x800")
        self.root.minsize(1000, 600)

        # ---- 数据状态 ----
        self.image_folder = ""
        self.output_folder = ""
        self.image_list = []
        self.current_index = -1
        self.original_image = None
        self.display_image = None
        self.tk_image = None

        self.grid_rows = 3
        self.grid_cols = 4
        self.block_w = 128
        self.block_h = 128
        self.grid_mode = "pixel"
        self.labels_list = ["1", "2"]
        self.current_paint_label = "1"
        self.grid_labels = {}
        self.history = []

        # 像素模式下每个块的原图起始坐标
        self._col_positions = []  # 各列的 x 起点
        self._row_positions = []  # 各行的 y 起点

        self.annotations_cache = {}
        self.label_colors = {}

        self._build_ui()
        self._update_label_colors()

    # ================================================================
    #  UI 构建
    # ================================================================
    def _build_ui(self):
        main_frame = ttk.Frame(self.root)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        left_panel = ttk.Frame(main_frame, width=300)
        left_panel.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 5))
        left_panel.pack_propagate(False)

        # -- 图片文件夹 --
        sec1 = ttk.LabelFrame(left_panel, text="图片文件夹")
        sec1.pack(fill=tk.X, pady=(0, 5))
        ttk.Button(sec1, text="选择文件夹...", command=self._select_image_folder).pack(fill=tk.X, padx=5, pady=2)
        self.folder_label = ttk.Label(sec1, text="未选择", wraplength=270)
        self.folder_label.pack(fill=tk.X, padx=5)
        self.image_info_label = ttk.Label(sec1, text="")
        self.image_info_label.pack(fill=tk.X, padx=5, pady=(0, 5))

        nav_frame = ttk.Frame(sec1)
        nav_frame.pack(fill=tk.X, padx=5, pady=(0, 5))
        ttk.Button(nav_frame, text="◀ 上一张", command=self._prev_image).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 2))
        ttk.Button(nav_frame, text="下一张 ▶", command=self._next_image).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(2, 0))

        # -- 网格设置 --
        sec2 = ttk.LabelFrame(left_panel, text="网格设置")
        sec2.pack(fill=tk.X, pady=(0, 5))

        self.grid_mode_var = tk.StringVar(value="pixel")
        mode_frame = ttk.Frame(sec2)
        mode_frame.pack(fill=tk.X, padx=5, pady=2)
        ttk.Radiobutton(mode_frame, text="按像素大小", variable=self.grid_mode_var,
                        value="pixel", command=self._on_grid_mode_change).pack(side=tk.LEFT, padx=(0, 10))
        ttk.Radiobutton(mode_frame, text="按行列数", variable=self.grid_mode_var,
                        value="rowcol", command=self._on_grid_mode_change).pack(side=tk.LEFT)

        self.pixel_frame = ttk.Frame(sec2)
        self.pixel_frame.pack(fill=tk.X, padx=5, pady=2)
        ttk.Label(self.pixel_frame, text="块宽:").pack(side=tk.LEFT)
        self.block_w_var = tk.StringVar(value="128")
        ttk.Entry(self.pixel_frame, textvariable=self.block_w_var, width=6).pack(side=tk.LEFT, padx=3)
        ttk.Label(self.pixel_frame, text="块高:").pack(side=tk.LEFT)
        self.block_h_var = tk.StringVar(value="128")
        ttk.Entry(self.pixel_frame, textvariable=self.block_h_var, width=6).pack(side=tk.LEFT, padx=3)
        ttk.Label(self.pixel_frame, text="px").pack(side=tk.LEFT)

        self.rowcol_frame = ttk.Frame(sec2)
        self.rowcol_frame.pack(fill=tk.X, padx=5, pady=2)
        ttk.Label(self.rowcol_frame, text="行数:").pack(side=tk.LEFT)
        self.rows_var = tk.StringVar(value="3")
        ttk.Entry(self.rowcol_frame, textvariable=self.rows_var, width=6).pack(side=tk.LEFT, padx=3)
        ttk.Label(self.rowcol_frame, text="列数:").pack(side=tk.LEFT)
        self.cols_var = tk.StringVar(value="4")
        ttk.Entry(self.rowcol_frame, textvariable=self.cols_var, width=6).pack(side=tk.LEFT, padx=3)
        self.rowcol_frame.pack_forget()

        ttk.Button(sec2, text="应用网格", command=self._apply_grid).pack(fill=tk.X, padx=5, pady=(2, 2))

        self.grid_info_label = ttk.Label(sec2, text="", foreground="blue", wraplength=270)
        self.grid_info_label.pack(fill=tk.X, padx=5, pady=(0, 5))

        # -- 标签设置 --
        sec3 = ttk.LabelFrame(left_panel, text="标签设置")
        sec3.pack(fill=tk.X, pady=(0, 5))
        ttk.Label(sec3, text="标签列表（逗号分隔）:").pack(fill=tk.X, padx=5, pady=(2, 0))
        self.labels_var = tk.StringVar(value="1,2")
        ttk.Entry(sec3, textvariable=self.labels_var).pack(fill=tk.X, padx=5, pady=2)
        ttk.Button(sec3, text="应用标签", command=self._apply_labels).pack(fill=tk.X, padx=5, pady=(2, 5))

        # -- 当前画笔标签选择 --
        sec4 = ttk.LabelFrame(left_panel, text="当前画笔标签")
        sec4.pack(fill=tk.X, pady=(0, 5))
        self.paint_label_frame = ttk.Frame(sec4)
        self.paint_label_frame.pack(fill=tk.X, padx=5, pady=5)
        self.paint_label_var = tk.StringVar(value="1")
        self._rebuild_paint_label_selector()

        # -- 撤销 / 重置 --
        sec5 = ttk.LabelFrame(left_panel, text="操作")
        sec5.pack(fill=tk.X, pady=(0, 5))
        op_frame = ttk.Frame(sec5)
        op_frame.pack(fill=tk.X, padx=5, pady=5)
        ttk.Button(op_frame, text="↩ 撤销", command=self._undo).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 2))
        ttk.Button(op_frame, text="⟲ 重置", command=self._reset_labels).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(2, 0))

        # -- 输出路径 --
        sec6 = ttk.LabelFrame(left_panel, text="输出路径")
        sec6.pack(fill=tk.X, pady=(0, 5))
        ttk.Button(sec6, text="选择输出路径...", command=self._select_output_folder).pack(fill=tk.X, padx=5, pady=2)
        self.output_label = ttk.Label(sec6, text="未选择", wraplength=270)
        self.output_label.pack(fill=tk.X, padx=5, pady=(0, 5))

        # -- 保存 --
        sec7 = ttk.LabelFrame(left_panel, text="保存")
        sec7.pack(fill=tk.X, pady=(0, 5))
        ttk.Button(sec7, text="💾 保存当前图片", command=self._save_current).pack(fill=tk.X, padx=5, pady=2)
        ttk.Button(sec7, text="💾 保存全部图片", command=self._save_all).pack(fill=tk.X, padx=5, pady=(2, 5))

        # -- 图例 --
        sec8 = ttk.LabelFrame(left_panel, text="标签图例")
        sec8.pack(fill=tk.X, pady=(0, 5))
        self.legend_frame = ttk.Frame(sec8)
        self.legend_frame.pack(fill=tk.X, padx=5, pady=5)
        self._rebuild_legend()

        # ---------- 右侧画布 ----------
        canvas_frame = ttk.Frame(main_frame)
        canvas_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.canvas = tk.Canvas(canvas_frame, bg="#2b2b2b", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.canvas.bind("<Button-1>", self._on_canvas_click)
        self.canvas.bind("<Configure>", self._on_canvas_resize)
        self._canvas_w = 0
        self._canvas_h = 0

    # ================================================================
    #  网格模式切换
    # ================================================================
    def _on_grid_mode_change(self):
        mode = self.grid_mode_var.get()
        if mode == "pixel":
            self.rowcol_frame.pack_forget()
            self.pixel_frame.pack(fill=tk.X, padx=5, pady=2,
                                  before=self.pixel_frame.master.winfo_children()[-2])
        else:
            self.pixel_frame.pack_forget()
            self.rowcol_frame.pack(fill=tk.X, padx=5, pady=2,
                                   before=self.rowcol_frame.master.winfo_children()[-2])

    # ================================================================
    #  标签颜色管理
    # ================================================================
    def _generate_colors(self, n):
        colors = []
        for i in range(n):
            hue = i / max(n, 1)
            r, g, b = colorsys.hsv_to_rgb(hue, 0.45, 0.95)
            colors.append(f"#{int(r*255):02x}{int(g*255):02x}{int(b*255):02x}")
        return colors

    def _update_label_colors(self):
        colors = self._generate_colors(len(self.labels_list))
        self.label_colors = {label: color for label, color in zip(self.labels_list, colors)}

    # ================================================================
    #  画笔标签选择器 / 图例
    # ================================================================
    def _rebuild_paint_label_selector(self):
        for w in self.paint_label_frame.winfo_children():
            w.destroy()
        for label in self.labels_list:
            color = self.label_colors.get(label, "#cccccc")
            rb = tk.Radiobutton(
                self.paint_label_frame, text=label,
                variable=self.paint_label_var, value=label,
                indicatoron=True, bg=color, activebackground=color,
                selectcolor=color, padx=8, pady=2,
            )
            rb.pack(side=tk.LEFT, padx=2)
        if self.labels_list:
            self.paint_label_var.set(self.labels_list[0])
            self.current_paint_label = self.labels_list[0]

    def _rebuild_legend(self):
        for w in self.legend_frame.winfo_children():
            w.destroy()
        for label in self.labels_list:
            color = self.label_colors.get(label, "#cccccc")
            f = tk.Frame(self.legend_frame)
            f.pack(side=tk.LEFT, padx=4)
            tk.Label(f, bg=color, width=2, height=1, relief="solid", borderwidth=1).pack(side=tk.LEFT, padx=(0, 2))
            tk.Label(f, text=label).pack(side=tk.LEFT)

    # ================================================================
    #  像素模式：网格位置计算
    # ================================================================
    @staticmethod
    def _calc_block_positions(img_size, block_size):
        """
        计算一个维度上各网格块的起始像素坐标。
        最后一块紧贴边缘（向左/上偏移），保证所有块大小统一。
        返回: list[int]
        """
        n = math.ceil(img_size / block_size)
        n = max(n, 1)
        positions = []
        for i in range(n):
            pos = i * block_size
            # 最后一块：紧贴图片右/下边缘
            if pos + block_size > img_size:
                pos = img_size - block_size
            positions.append(max(pos, 0))
        return positions

    def _calc_grid_from_pixels(self, img_w, img_h):
        """根据图片尺寸和块大小计算网格行列数和位置"""
        col_pos = self._calc_block_positions(img_w, self.block_w)
        row_pos = self._calc_block_positions(img_h, self.block_h)
        return len(row_pos), len(col_pos), row_pos, col_pos

    def _update_grid_info(self):
        if self.original_image is None:
            self.grid_info_label.config(text="")
            return
        img_w, img_h = self.original_image.size
        mode = self.grid_mode_var.get()

        if mode == "pixel":
            remainder_w = img_w % self.block_w
            remainder_h = img_h % self.block_h
            info = (f"图片: {img_w}×{img_h} px | "
                    f"网格: {self.grid_rows}行×{self.grid_cols}列\n"
                    f"块大小: {self.block_w}×{self.block_h} px")
            if remainder_w > 0 or remainder_h > 0:
                overlap_w = self.block_w - remainder_w if remainder_w > 0 else 0
                overlap_h = self.block_h - remainder_h if remainder_h > 0 else 0
                info += f"\n末行/列重叠: 水平 {overlap_w} px, 垂直 {overlap_h} px"
            self.grid_info_label.config(text=info)
        else:
            cell_w = img_w / self.grid_cols
            cell_h = img_h / self.grid_rows
            info = (f"图片: {img_w}×{img_h} px | "
                    f"网格: {self.grid_rows}行×{self.grid_cols}列\n"
                    f"块大小: {cell_w:.1f}×{cell_h:.1f} px")
            self.grid_info_label.config(text=info)

    # ================================================================
    #  图片文件夹 & 导航
    # ================================================================
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

    def _load_current_image(self):
        if self.current_index < 0 or self.current_index >= len(self.image_list):
            return

        filename = self.image_list[self.current_index]
        path = os.path.join(self.image_folder, filename)
        self.original_image = Image.open(path)
        self.image_info_label.config(
            text=f"{filename}  ({self.current_index+1}/{len(self.image_list)})"
        )

        img_w, img_h = self.original_image.size
        mode = self.grid_mode_var.get()

        if mode == "pixel":
            new_rows, new_cols, row_pos, col_pos = self._calc_grid_from_pixels(img_w, img_h)
            self._row_positions = row_pos
            self._col_positions = col_pos
        else:
            new_rows = self.grid_rows
            new_cols = self.grid_cols

        # 检查缓存
        if self.current_index in self.annotations_cache:
            cached = self.annotations_cache[self.current_index]
            if cached["rows"] == new_rows and cached["cols"] == new_cols:
                self.grid_rows = cached["rows"]
                self.grid_cols = cached["cols"]
                self.grid_labels = dict(cached["labels"])
            else:
                del self.annotations_cache[self.current_index]
                self.grid_rows = new_rows
                self.grid_cols = new_cols
                self._init_grid_labels()
        else:
            self.grid_rows = new_rows
            self.grid_cols = new_cols
            self._init_grid_labels()

        self.history.clear()
        self._update_grid_info()
        self._refresh_canvas()

    def _cache_current_annotations(self):
        if self.current_index >= 0 and self.grid_labels:
            self.annotations_cache[self.current_index] = {
                "rows": self.grid_rows,
                "cols": self.grid_cols,
                "labels": dict(self.grid_labels),
            }

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

    # ================================================================
    #  网格 & 标签配置
    # ================================================================
    def _init_grid_labels(self):
        default_label = self.labels_list[0] if self.labels_list else "1"
        self.grid_labels = {}
        for r in range(self.grid_rows):
            for c in range(self.grid_cols):
                self.grid_labels[(r, c)] = default_label
        self.history.clear()

    def _apply_grid(self):
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
                return
            self.block_w = bw
            self.block_h = bh

            if self.original_image is not None:
                img_w, img_h = self.original_image.size
                if bw > img_w or bh > img_h:
                    messagebox.showerror("错误",
                        f"块大小 ({bw}×{bh}) 不能超过图片尺寸 ({img_w}×{img_h})。")
                    return
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
                return
            self.grid_rows = rows
            self.grid_cols = cols

        self._init_grid_labels()
        self._update_grid_info()
        self._refresh_canvas()

    def _apply_labels(self):
        raw = self.labels_var.get().strip()
        if not raw:
            messagebox.showerror("错误", "标签列表不能为空。")
            return
        new_labels = [s.strip() for s in raw.split(",") if s.strip()]
        if not new_labels:
            messagebox.showerror("错误", "标签列表不能为空。")
            return

        self.labels_list = new_labels
        new_default = self.labels_list[0]
        self._update_label_colors()
        self._rebuild_paint_label_selector()
        self._rebuild_legend()

        for key in self.grid_labels:
            if self.grid_labels[key] not in self.labels_list:
                self.grid_labels[key] = new_default
        self.history.clear()
        self._refresh_canvas()

    # ================================================================
    #  画布绘制
    # ================================================================
    def _on_canvas_resize(self, event):
        if event.width != self._canvas_w or event.height != self._canvas_h:
            self._canvas_w = event.width
            self._canvas_h = event.height
            self._refresh_canvas()

    def _get_cell_display_rects(self, scale):
        """
        返回每个网格块在画布上的显示矩形 (x1, y1, x2, y2)。
        像素模式下使用实际像素位置（最后一块可能有偏移）。
        行列模式下均匀划分。
        """
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

    def _refresh_canvas(self):
        self.canvas.delete("all")
        if self.original_image is None:
            self.canvas.create_text(
                self._canvas_w // 2, self._canvas_h // 2,
                text="请先选择图片文件夹", fill="#888888", font=("微软雅黑", 16)
            )
            return

        cw = self._canvas_w or self.canvas.winfo_width()
        ch = self._canvas_h or self.canvas.winfo_height()
        if cw <= 1 or ch <= 1:
            return

        img_w, img_h = self.original_image.size
        scale = min(cw / img_w, ch / img_h)
        new_w = int(img_w * scale)
        new_h = int(img_h * scale)

        self.display_image = self.original_image.resize((new_w, new_h), Image.LANCZOS)
        self.tk_image = ImageTk.PhotoImage(self.display_image)

        self._offset_x = (cw - new_w) // 2
        self._offset_y = (ch - new_h) // 2
        self._display_w = new_w
        self._display_h = new_h
        self._scale = scale

        # 绘制图片
        self.canvas.create_image(self._offset_x, self._offset_y, anchor=tk.NW, image=self.tk_image)

        # 获取各网格块的显示矩形
        rects = self._get_cell_display_rects(scale)
        self._cell_rects = rects  # 缓存用于点击检测

        # 绘制标签颜色覆盖层
        for (r, c), (x1, y1, x2, y2) in rects.items():
            label = self.grid_labels.get((r, c), self.labels_list[0])
            color = self.label_colors.get(label, "#cccccc")
            self.canvas.create_rectangle(
                x1, y1, x2, y2,
                fill=color, outline="", stipple="gray25", tags="overlay"
            )

        # 绘制网格线
        for (r, c), (x1, y1, x2, y2) in rects.items():
            # 左边线和上边线
            self.canvas.create_line(x1, y1, x2, y1, fill="#ffffff", width=1, tags="grid")
            self.canvas.create_line(x1, y1, x1, y2, fill="#ffffff", width=1, tags="grid")
        # 绘制最后一列的右边线和最后一行的底边线
        for r in range(self.grid_rows):
            _, _, x2, y2 = rects[(r, self.grid_cols - 1)]
            _, y1_start, _, _ = rects[(r, self.grid_cols - 1)]
            self.canvas.create_line(x2, y1_start, x2, y2, fill="#ffffff", width=1, tags="grid")
        for c in range(self.grid_cols):
            _, _, x2, y2 = rects[(self.grid_rows - 1, c)]
            x1_start, _, _, _ = rects[(self.grid_rows - 1, c)]
            self.canvas.create_line(x1_start, y2, x2, y2, fill="#ffffff", width=1, tags="grid")

        # 绘制网格坐标和标签文字
        for (r, c), (x1, y1, x2, y2) in rects.items():
            cx = (x1 + x2) / 2
            cy = (y1 + y2) / 2
            label = self.grid_labels.get((r, c), "")
            cell_w_px = x2 - x1
            cell_h_px = y2 - y1
            font_size = max(7, min(int(cell_w_px / 4), int(cell_h_px / 4), 14))
            self.canvas.create_text(
                cx, cy, text=f"{r},{c}\n[{label}]",
                fill="#000000", font=("Consolas", font_size, "bold"), tags="text"
            )

    # ================================================================
    #  网格点击 & 标注
    # ================================================================
    def _on_canvas_click(self, event):
        if self.original_image is None or not hasattr(self, '_cell_rects'):
            return

        mx, my = event.x, event.y

        # 检测点击落在哪个网格块内
        clicked = None
        for (r, c), (x1, y1, x2, y2) in self._cell_rects.items():
            if x1 <= mx < x2 and y1 <= my < y2:
                clicked = (r, c)
                break  # 不break，后绘制的块（如有重叠）优先

        if clicked is None:
            return

        row, col = clicked
        current_label = self.paint_label_var.get()
        old_label = self.grid_labels.get((row, col), self.labels_list[0])
        default_label = self.labels_list[0] if self.labels_list else "1"

        if old_label == current_label:
            if old_label != default_label:
                self.history.append((row, col, old_label))
                self.grid_labels[(row, col)] = default_label
                self._refresh_canvas()
        else:
            self.history.append((row, col, old_label))
            self.grid_labels[(row, col)] = current_label
            self._refresh_canvas()

    # ================================================================
    #  撤销 & 重置
    # ================================================================
    def _undo(self):
        if not self.history:
            return
        row, col, old_label = self.history.pop()
        self.grid_labels[(row, col)] = old_label
        self._refresh_canvas()

    def _reset_labels(self):
        if not self.grid_labels:
            return
        if messagebox.askyesno("确认", "确定要将当前图片所有网格标签重置为默认值吗？"):
            self._init_grid_labels()
            self._refresh_canvas()

    # ================================================================
    #  输出路径
    # ================================================================
    def _select_output_folder(self):
        folder = filedialog.askdirectory(title="选择输出文件夹")
        if folder:
            self.output_folder = folder
            self.output_label.config(text=folder)

    # ================================================================
    #  保存
    # ================================================================
    def _get_crop_positions(self, img_w, img_h, rows, cols):
        """获取裁切时每个网格块的原图像素坐标"""
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
                    x2 = min(x2, img_w)
                    y2 = min(y2, img_h)
                    positions[(r, c)] = (x1, y1, x2, y2)

        return positions

    def _save_image_by_index(self, index):
        if not self.output_folder:
            messagebox.showerror("错误", "请先选择输出路径。")
            return False

        filename = self.image_list[index]
        img_name = os.path.splitext(filename)[0]
        img_path = os.path.join(self.image_folder, filename)
        img = Image.open(img_path)
        img_w, img_h = img.size

        if index == self.current_index:
            labels = self.grid_labels
            rows = self.grid_rows
            cols = self.grid_cols
        elif index in self.annotations_cache:
            cached = self.annotations_cache[index]
            labels = cached["labels"]
            rows = cached["rows"]
            cols = cached["cols"]
        else:
            mode = self.grid_mode_var.get()
            if mode == "pixel":
                rows, cols, _, _ = self._calc_grid_from_pixels(img_w, img_h)
            else:
                rows = self.grid_rows
                cols = self.grid_cols
            default_label = self.labels_list[0] if self.labels_list else "1"
            labels = {}
            for r in range(rows):
                for c in range(cols):
                    labels[(r, c)] = default_label

        crop_pos = self._get_crop_positions(img_w, img_h, rows, cols)

        for r in range(rows):
            for c in range(cols):
                label = labels.get((r, c), self.labels_list[0])
                label_dir = os.path.join(self.output_folder, str(label))
                os.makedirs(label_dir, exist_ok=True)

                x1, y1, x2, y2 = crop_pos[(r, c)]
                cropped = img.crop((x1, y1, x2, y2))
                out_name = f"{img_name}_{r}_{c}.png"
                cropped.save(os.path.join(label_dir, out_name))

        return True

    def _save_current(self):
        if self.current_index < 0:
            messagebox.showwarning("警告", "没有加载任何图片。")
            return
        if self._save_image_by_index(self.current_index):
            messagebox.showinfo("完成", f"当前图片已保存到: {self.output_folder}")

    def _save_all(self):
        if not self.image_list:
            messagebox.showwarning("警告", "没有加载任何图片。")
            return
        if not self.output_folder:
            messagebox.showerror("错误", "请先选择输出路径。")
            return

        self._cache_current_annotations()
        success_count = 0
        for i in range(len(self.image_list)):
            if self._save_image_by_index(i):
                success_count += 1

        messagebox.showinfo("完成", f"已保存 {success_count}/{len(self.image_list)} 张图片到: {self.output_folder}")


# ================================================================
#  入口
# ================================================================
def main():
    root = tk.Tk()
    app = GridLabelTool(root)
    root.mainloop()


if __name__ == "__main__":
    main()
