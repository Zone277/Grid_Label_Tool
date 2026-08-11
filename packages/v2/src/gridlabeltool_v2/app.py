"""
Edit-Based Image Annotation Tool
=================================
基于 GridLabelTool 修改而来的增强版标注工具。
除了继承原有的网格自由标注切割外，最重要的功能是支持：
- 选择原图目录。
- 选择已经切割好并分类存放完毕的“已标注数据集”。
- 自动解析已有的文件夹名称作为标签名，并将它们加载还原到网格视图上。
- 在页面上修改错误的切片标签后，点击保存即可物理迁移（重新裁切并覆盖、清除旧错标文件）至正确的分类目录中。

依赖：pip install Pillow
"""

import os
import math
import colorsys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from PIL import Image, ImageTk


class EditLabelTool:
    def __init__(self, root):
        self.root = root
        self.root.title("标注修改/继续工具 - Edit Label Tool")
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
        self.block_w = 64
        self.block_h = 64
        self.grid_mode = "pixel"
        self.labels_list = ["1", "2"]
        self.current_paint_label = "1"
        self.grid_labels = {}
        self.history = []
        
        # 新增：保存从输出数据集里解析出来的所有打标情况
        self.loaded_annotations = {}

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
        sec1 = ttk.LabelFrame(left_panel, text="原生(未剪裁)图片文件夹")
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
        self.block_w_var = tk.StringVar(value="64")
        ttk.Entry(self.pixel_frame, textvariable=self.block_w_var, width=6).pack(side=tk.LEFT, padx=3)
        ttk.Label(self.pixel_frame, text="块高:").pack(side=tk.LEFT)
        self.block_h_var = tk.StringVar(value="64")
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

        # -- 输出/数据集路径 --
        sec6 = ttk.LabelFrame(left_panel, text="标注数据集及存入路径")
        sec6.pack(fill=tk.X, pady=(0, 5))
        ttk.Button(sec6, text="选择已标注数据集...", command=self._select_output_folder).pack(fill=tk.X, padx=5, pady=2)
        self.output_label = ttk.Label(sec6, text="未选择", wraplength=270)
        self.output_label.pack(fill=tk.X, padx=5, pady=(0, 5))

        # -- 标签设置 --
        sec3 = ttk.LabelFrame(left_panel, text="标签分类设置")
        sec3.pack(fill=tk.X, pady=(0, 5))
        ttk.Label(sec3, text="系统已检出列表(可手动补全逗号分隔):").pack(fill=tk.X, padx=5, pady=(2, 0))
        self.labels_var = tk.StringVar(value="1,2")
        ttk.Entry(sec3, textvariable=self.labels_var).pack(fill=tk.X, padx=5, pady=2)
        ttk.Button(sec3, text="强行应用手动标签", command=self._apply_labels).pack(fill=tk.X, padx=5, pady=(2, 5))

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
        ttk.Button(op_frame, text="⟲ 纯净重置", command=self._reset_labels).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(2, 0))

        # -- 保存 --
        sec7 = ttk.LabelFrame(left_panel, text="保存修复结果")
        sec7.pack(fill=tk.X, pady=(0, 5))
        ttk.Button(sec7, text="💾 修正保存当前图片", command=self._save_current).pack(fill=tk.X, padx=5, pady=2)
        ttk.Button(sec7, text="💾 保存全部图片 (请慎用)", command=self._save_all).pack(fill=tk.X, padx=5, pady=(2, 5))

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
        if self.labels_list and self.current_paint_label not in self.labels_list:
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
        folder = filedialog.askdirectory(title="选择原生未裁剪的图片文件夹")
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
            
    # ================================================================
    #  输出路径与解析已有数据集
    # ================================================================
    def _select_output_folder(self):
        folder = filedialog.askdirectory(title="选择已标注的数据集文件夹(将被直接覆盖修改)")
        if folder:
            self.output_folder = folder
            self.output_label.config(text=folder)
            self._scan_existing_dataset()
            
    def _scan_existing_dataset(self):
        self.loaded_annotations = {}
        if not self.output_folder or not os.path.exists(self.output_folder):
            return
            
        count = 0
        # 使用 os.walk 全局搜索，兼容 output/1/2/xxx.png 这样带有图片ID嵌套层的目录结构
        for root_dir, dirs, files in os.walk(self.output_folder):
            for filename in files:
                if not filename.lower().endswith(('.png', '.jpg', '.jpeg', '.bmp', '.tif', '.webp')):
                    continue
                    
                # 不管嵌套多少层，直接取包含此文件的最底层级作为 label
                label_folder = os.path.basename(root_dir)
                
                basename = os.path.splitext(filename)[0]
                parts = basename.rsplit('_', 2)
                if len(parts) == 3:
                    img_name, r_str, c_str = parts
                    try:
                        r, c = int(r_str), int(c_str)
                        if img_name not in self.loaded_annotations:
                            self.loaded_annotations[img_name] = {}
                        self.loaded_annotations[img_name][(r, c)] = label_folder
                        count += 1
                        
                        # 同步加入系统配置的标签类别里
                        if label_folder not in self.labels_list:
                            self.labels_list.append(label_folder)
                    except ValueError:
                        pass
                        
        if count > 0:
            self.labels_var.set(",".join(self.labels_list))
            self._update_label_colors()
            self._rebuild_paint_label_selector()
            self._rebuild_legend()
            messagebox.showinfo("解析完成", f"成功解析了 {count} 个历史网格！如果存在不匹配将重新创建。")
        else:
            messagebox.showinfo("解析结果", "在该目录下并未找到符合格式的历史图片 (图片名_行_列.png)。")
            
        # 刷新UI使得新增的画笔标签可以在当前图片上生效
        if self.image_list and self.current_index >= 0:
            self.annotations_cache.clear()
            self._load_current_image()

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

        # 检查缓存以避免来回切换时丢失
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
            
        # ======== 历史标注数据集的自动覆盖 ========
        img_name = os.path.splitext(filename)[0]
        if hasattr(self, 'loaded_annotations') and img_name in self.loaded_annotations:
            for (r, c), lbl in self.loaded_annotations[img_name].items():
                if r < self.grid_rows and c < self.grid_cols:
                    # 将该标签打入画板对应网格
                    if lbl in self.labels_list:
                        self.grid_labels[(r, c)] = lbl
        # ==========================================

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
        
        # 再次尝试覆盖加载的标注（如果仅仅是改变了网格尺寸而不想清除数据集标注）
        if self.current_index >= 0:
            filename = self.image_list[self.current_index]
            img_name = os.path.splitext(filename)[0]
            if hasattr(self, 'loaded_annotations') and img_name in self.loaded_annotations:
                for (r, c), lbl in self.loaded_annotations[img_name].items():
                    if r < self.grid_rows and c < self.grid_cols:
                        if lbl in self.labels_list:
                            self.grid_labels[(r, c)] = lbl
                            
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
                text="请先选择需要编辑图片所在的原图源文件夹", fill="#888888", font=("微软雅黑", 16)
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

        self.canvas.create_image(self._offset_x, self._offset_y, anchor=tk.NW, image=self.tk_image)

        rects = self._get_cell_display_rects(scale)
        self._cell_rects = rects

        for (r, c), (x1, y1, x2, y2) in rects.items():
            label = self.grid_labels.get((r, c), self.labels_list[0])
            color = self.label_colors.get(label, "#cccccc")
            self.canvas.create_rectangle(
                x1, y1, x2, y2,
                fill=color, outline="", stipple="gray25", tags="overlay"
            )

        for (r, c), (x1, y1, x2, y2) in rects.items():
            self.canvas.create_line(x1, y1, x2, y1, fill="#ffffff", width=1, tags="grid")
            self.canvas.create_line(x1, y1, x1, y2, fill="#ffffff", width=1, tags="grid")
        for r in range(self.grid_rows):
            _, _, x2, y2 = rects[(r, self.grid_cols - 1)]
            _, y1_start, _, _ = rects[(r, self.grid_cols - 1)]
            self.canvas.create_line(x2, y1_start, x2, y2, fill="#ffffff", width=1, tags="grid")
        for c in range(self.grid_cols):
            _, _, x2, y2 = rects[(self.grid_rows - 1, c)]
            x1_start, _, _, _ = rects[(self.grid_rows - 1, c)]
            self.canvas.create_line(x1_start, y2, x2, y2, fill="#ffffff", width=1, tags="grid")

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

        clicked = None
        for (r, c), (x1, y1, x2, y2) in self._cell_rects.items():
            if x1 <= mx < x2 and y1 <= my < y2:
                clicked = (r, c)
                break

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
        if messagebox.askyesno("确认", "确定要清除画板标注状态并将其重置为默认值(未更改的原数据集文件将被丢失)吗？"):
            self._init_grid_labels()
            self._refresh_canvas()

    # ================================================================
    #  保存与删除清理机制
    # ================================================================
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
                    x2 = min(x2, img_w)
                    y2 = min(y2, img_h)
                    positions[(r, c)] = (x1, y1, x2, y2)

        return positions

    def _save_image_by_index(self, index):
        if not self.output_folder:
            messagebox.showerror("错误", "请先选择需要保存并覆盖对应切片的数据集输出路径。")
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
            # 当该图片甚至没有在缓存中打开时，跳过保存以防误覆写
            return False

        try:
            folder_name = str(int(img_name))
        except ValueError:
            folder_name = img_name
            
        # 针对嵌套需求，输出到 output_folder / image_id_folder 的子目录下
        img_out_root = os.path.join(self.output_folder, folder_name)

        crop_pos = self._get_crop_positions(img_w, img_h, rows, cols)
        
        has_changed_something = False

        for r in range(rows):
            for c in range(cols):
                label = labels.get((r, c), self.labels_list[0])
                label_dir = os.path.join(img_out_root, str(label))
                os.makedirs(label_dir, exist_ok=True)

                out_name = f"{img_name}_{r}_{c}.png"
                out_path = os.path.join(label_dir, out_name)
                
                # ====== 高效防冗余纠错机制 ====== 
                # 这里遍历可能会存在该切片旧错误位置的地方，并自动删除
                possible_dirs = []
                for exist_lbl in self.labels_list + list(set(self.loaded_annotations.get(img_name, {}).values())):
                    possible_dirs.append(os.path.join(self.output_folder, str(exist_lbl))) # 兼容扁平结构
                    possible_dirs.append(os.path.join(img_out_root, str(exist_lbl)))      # 兼容分层结构
                    
                for p_dir in set(possible_dirs):
                    if os.path.isdir(p_dir):
                        old_file = os.path.join(p_dir, out_name)
                        if os.path.normpath(old_file) != os.path.normpath(out_path) and os.path.exists(old_file):
                            try:
                                os.remove(old_file)
                                has_changed_something = True
                            except OSError:
                                pass
                # ==================================
                
                x1, y1, x2, y2 = crop_pos[(r, c)]
                cropped = img.crop((x1, y1, x2, y2))
                
                if out_path.lower().endswith(('.jpg', '.jpeg')) and cropped.mode == 'RGBA':
                    cropped = cropped.convert('RGB')
                cropped.save(out_path)
                
                # 更新本地解析缓存
                if not hasattr(self, 'loaded_annotations'):
                    self.loaded_annotations = {}
                if img_name not in self.loaded_annotations:
                    self.loaded_annotations[img_name] = {}
                self.loaded_annotations[img_name][(r, c)] = str(label)

        return True

    def _save_current(self):
        if self.current_index < 0:
            messagebox.showwarning("警告", "没有加载任何图片。")
            return
        if self._save_image_by_index(self.current_index):
            messagebox.showinfo("完成", f"当前图片的修正已生效！其对应的错误分类切片(若有)已被清除。")

    def _save_all(self):
        if not self.image_list:
            messagebox.showwarning("警告", "没有加载任何图片。")
            return
        if not self.output_folder:
            messagebox.showerror("错误", "请先选择需要保存并覆盖对应切片的数据集输出路径。")
            return

        self._cache_current_annotations()
        success_count = 0
        for i in list(self.annotations_cache.keys()) + ([self.current_index] if self.current_index not in self.annotations_cache else []):
            if self._save_image_by_index(i):
                success_count += 1

        messagebox.showinfo("完成", f"已重新保存 {success_count} 张打开过的图片到数据集中。")


# ================================================================
#  入口
# ================================================================
def main():
    root = tk.Tk()
    app = EditLabelTool(root)
    root.mainloop()


if __name__ == "__main__":
    main()
