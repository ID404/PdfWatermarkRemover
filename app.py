# -*- coding: utf-8 -*-
"""PDF 水印去除工具 —— 图形界面（tkinter，现代风格，DPI 自适应）。

支持：自动识别水印 → 一键去除；识别不到时回退为手工输入水印文字。

命令行模式（批处理 / 自测）：
    app.exe --cli <源文件.pdf> <输出文件.pdf> <水印文字|auto> [--report 日志.txt]
"""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import traceback

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter import font as tkfont

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pdf_wm_core import analyze_watermarks, remove_watermark  # noqa: E402

APP_NAME = "PDF 水印去除工具"
APP_VERSION = "1.3"
WM_PLACEHOLDER = "自动识别不出时，在这里手工输入水印文字（如：成都市快猎聘…）"

# ---------------------------------- 主题 ----------------------------------
BG = "#F4F6FA"
CARD = "#FFFFFF"
BORDER = "#E4E8F0"
PRIMARY = "#2563EB"
PRIMARY_DARK = "#1D4ED8"
PRIMARY_SOFT = "#EFF6FF"
TEXT = "#111827"
MUTED = "#6B7280"
SUCCESS = "#16A34A"
DANGER = "#DC2626"
SOFT = "#F8FAFC"
FONT = "Microsoft YaHei UI"

# DPI 缩放（tk 字体随 DPI 自动放大，像素尺寸必须同步缩放，否则文字会被裁切）
_SCALE = 1.0


def _enable_dpi():
    """让进程按系统 DPI 渲染，避免位图拉伸模糊。"""
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            import ctypes

            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def set_scale(root: tk.Tk):
    global _SCALE
    try:
        dpi = root.winfo_fpixels("1i")
        _SCALE = max(1.0, float(dpi) / 96.0)
    except Exception:
        _SCALE = 1.0


def S(px: float) -> int:
    """像素尺寸按 DPI 缩放。"""
    return int(round(px * _SCALE))


def _measure(text: str, font) -> int:
    """按真实字体度量文本像素宽度（中文不能按字数估算）。"""
    try:
        f = tkfont.Font(font=font)
        return int(f.measure(text))
    except Exception:
        return int(len(text) * 14 * _SCALE)


# ---------------------------------- 自定义控件 ----------------------------------
class RoundedButton(tk.Canvas):
    """圆角按钮：primary / ghost / danger / success 四种风格。宽度可自适应文字。"""

    STYLES = {
        "primary": (PRIMARY, PRIMARY_DARK, "#FFFFFF"),
        "danger": (DANGER, "#B91C1C", "#FFFFFF"),
        "success": (SUCCESS, "#15803D", "#FFFFFF"),
        "ghost": (CARD, "#F1F5F9", TEXT),
    }

    def __init__(self, master, text, command=None, kind="primary", width=None, height=38,
                 radius=9, font_size=10, pad_x=26):
        super().__init__(master, width=10, height=S(height), highlightthickness=0, bd=0)
        self.configure(bg=getattr(master, "_bg", CARD))
        self.text = text
        self.command = command
        self.kind = kind
        self.font = (FONT, font_size, "bold")
        self.width = width or (_measure(text, self.font) + S(pad_x) * 2)
        self.height = S(height)
        self.radius = S(radius)
        self.state = "normal"
        self._hover = False
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.configure(width=self.width)
        self._draw()

    def _colors(self):
        base, hover, fg = self.STYLES.get(self.kind, self.STYLES["primary"])
        if self.state == "disabled":
            return ("#E5E7EB", "#9CA3AF") if self.kind != "ghost" else (CARD, "#9CA3AF")
        if self._hover:
            return (hover, fg)
        if self.kind == "ghost":
            return (CARD, TEXT)
        return (base, fg)

    def _round_rect(self, x1, y1, x2, y2, fill):
        r = self.radius
        self.create_rectangle(x1 + r, y1, x2 - r, y2, fill=fill, outline=fill)
        self.create_rectangle(x1, y1 + r, x2, y2 - r, fill=fill, outline=fill)
        self.create_oval(x1, y1, x1 + 2 * r, y1 + 2 * r, fill=fill, outline=fill)
        self.create_oval(x2 - 2 * r, y1, x2, y1 + 2 * r, fill=fill, outline=fill)
        self.create_oval(x1, y2 - 2 * r, x1 + 2 * r, y2, fill=fill, outline=fill)
        self.create_oval(x2 - 2 * r, y2 - 2 * r, x2, y2, fill=fill, outline=fill)

    def _draw(self):
        self.delete("all")
        fill, fg = self._colors()
        outline = BORDER if self.kind == "ghost" else fill
        w, h = self.width, self.height
        self._round_rect(0, 0, w, h, fill)
        if self.kind == "ghost":
            self.create_rectangle(self.radius, 0, w - self.radius, 1, fill=outline, outline=outline)
            self.create_rectangle(self.radius, h - 1, w - self.radius, h, fill=outline, outline=outline)
            self.create_rectangle(0, self.radius, 1, h - self.radius, fill=outline, outline=outline)
            self.create_rectangle(w - 1, self.radius, w, h - self.radius, fill=outline, outline=outline)
        self.create_text(w / 2, h / 2, text=self.text, fill=fg, font=self.font)

    def _on_enter(self, _e=None):
        if self.state == "disabled":
            return
        self._hover = True
        self._draw()

    def _on_leave(self, _e=None):
        self._hover = False
        self._draw()

    def _on_press(self, _e=None):
        if self.state == "disabled":
            return
        self._hover = False
        self._draw()

    def _on_release(self, _e=None):
        if self.state == "disabled":
            return
        self._hover = True
        self._draw()
        if self.command:
            self.command()

    def set_state(self, state):
        self.state = state
        self._hover = False
        self._draw()

    def set_text(self, text):
        self.text = text
        self._draw()


class ModernCheck(tk.Canvas):
    """方形勾选框。"""

    def __init__(self, master, text, variable: tk.BooleanVar, command=None):
        box = S(18)
        w = box + S(8) + _measure(text, (FONT, 10)) + S(8)
        super().__init__(master, width=w, height=S(24), highlightthickness=0, bd=0)
        self.configure(bg=getattr(master, "_bg", CARD))
        self.box = box
        self.text = text
        self.var = variable
        self.command = command
        self.bind("<ButtonRelease-1>", self._toggle)
        self.var.trace_add("write", lambda *_: self._draw())
        self._draw()

    def _toggle(self, _e=None):
        self.var.set(not self.var.get())
        if self.command:
            self.command()

    def _draw(self):
        self.delete("all")
        on = bool(self.var.get())
        b = self.box
        fill = PRIMARY if on else CARD
        outline = PRIMARY if on else "#CBD5E1"
        self.create_rectangle(1, S(2), 1 + b, S(2) + b, fill=fill, outline=outline, width=1)
        if on:
            self.create_line(S(5), S(11), S(8), S(14), fill="#FFFFFF", width=S(2))
            self.create_line(S(8), S(14), S(15), S(6), fill="#FFFFFF", width=S(2))
        self.create_text(b + S(8), S(2) + b / 2, text=self.text, anchor="w", fill=TEXT, font=(FONT, 10))


class Radio(tk.Canvas):
    """圆形单选按钮。"""

    def __init__(self, master, text, variable: tk.StringVar, value, command=None):
        d = S(18)
        w = d + S(8) + _measure(text, (FONT, 10)) + S(8)
        super().__init__(master, width=w, height=S(24), highlightthickness=0, bd=0)
        self.configure(bg=getattr(master, "_bg", CARD))
        self.d = d
        self.text = text
        self.var = variable
        self.value = value
        self.command = command
        self.bind("<ButtonRelease-1>", self._select)
        self.var.trace_add("write", lambda *_: self._draw())
        self._draw()

    def _select(self, _e=None):
        if self.var.get() != self.value:
            self.var.set(self.value)
            if self.command:
                self.command()

    def _draw(self):
        self.delete("all")
        on = self.var.get() == self.value
        d = self.d
        fill = PRIMARY if on else CARD
        outline = PRIMARY if on else "#CBD5E1"
        self.create_oval(1, S(2), 1 + d, S(2) + d, fill=fill, outline=outline, width=1)
        if on:
            self.create_oval(1 + d * 0.28, S(2) + d * 0.28, 1 + d * 0.72, S(2) + d * 0.72,
                             fill=PRIMARY, outline=PRIMARY)
        self.create_text(d + S(8), S(2) + d / 2, text=self.text, anchor="w", fill=TEXT, font=(FONT, 10))


class StepBadge(tk.Canvas):
    def __init__(self, master, number):
        d = S(22)
        super().__init__(master, width=d, height=d, highlightthickness=0, bd=0)
        self.configure(bg=getattr(master, "_bg", CARD))
        self.create_oval(1, 1, d - 1, d - 1, fill=PRIMARY, outline=PRIMARY)
        self.create_text(d / 2, d / 2 - S(1), text=str(number), fill="#FFFFFF", font=(FONT, 10, "bold"))


class Card(tk.Frame):
    def __init__(self, master, **kw):
        kw.setdefault("bg", CARD)
        kw.setdefault("highlightthickness", 1)
        kw.setdefault("highlightbackground", BORDER)
        kw.setdefault("highlightcolor", BORDER)
        super().__init__(master, **kw)
        self._bg = CARD


# ---------------------------------- 主应用 ----------------------------------
class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(f"{APP_NAME}  v{APP_VERSION}")
        self.root.configure(bg=BG)
        try:
            self.root.iconbitmap(default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.ico"))
        except Exception:
            pass

        self.files: list = []
        self.candidates: list = []
        self.q: "queue.Queue" = queue.Queue()
        self.worker: threading.Thread | None = None
        self.cancel_flag = False
        self.last_outputs: list = []

        set_scale(root)
        self._init_style()
        self._build_ui()
        self._auto_size()
        self.root.after(80, self._poll)

    # ---------------- 尺寸 ----------------
    def _auto_size(self):
        """打开时按内容自适应窗口大小，并在主屏工作区内居中。"""
        self.root.update_idletasks()
        w = max(self.root.winfo_reqwidth(), S(860)) + S(16)
        h = self.root.winfo_reqheight() + S(16)
        try:
            import ctypes

            u = ctypes.windll.user32

            class RC(ctypes.Structure):
                _fields_ = [("l", ctypes.c_long), ("t", ctypes.c_long),
                            ("r", ctypes.c_long), ("b", ctypes.c_long)]

            rc = RC()
            u.SystemParametersInfoW(0x0030, 0, ctypes.byref(rc), 0)  # SPI_GETWORKAREA
            sw, sh = rc.r - rc.l, rc.b - rc.t
        except Exception:
            sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        w = min(w, int(sw * 0.97))
        h = min(h, int(sh * 0.96))
        x = max((sw - w) // 2, 0)
        y = max((sh - h) // 2 - S(20), 0)
        self.root.geometry("%dx%d+%d+%d" % (w, h, x, y))
        self.root.minsize(min(S(780), w), min(S(600), h))

    # ---------------- 样式 ----------------
    def _init_style(self):
        st = ttk.Style()
        try:
            st.theme_use("clam")
        except Exception:
            pass
        st.configure("T.Treeview", background=CARD, fieldbackground=CARD, borderwidth=0,
                     rowheight=S(28), font=(FONT, 10))
        st.configure("T.Treeview.Heading", background=SOFT, borderwidth=0, relief="flat",
                     font=(FONT, 9, "bold"), foreground=MUTED)
        st.map("T.Treeview", background=[("selected", PRIMARY_SOFT)], foreground=[("selected", TEXT)])
        st.layout("T.Treeview", [("T.Treeview.treearea", {"sticky": "nswe"})])
        st.configure("P.Horizontal.TProgressbar", troughcolor="#E5E7EB", background=PRIMARY,
                     borderwidth=0, thickness=S(9))
        st.configure("TScrollbar", background="#E5E7EB", troughcolor=CARD, borderwidth=0,
                     arrowsize=S(12), relief="flat")

    # ---------------- 布局 ----------------
    def _build_ui(self):
        top = tk.Frame(self.root, bg=PRIMARY, height=S(58))
        top.pack(fill="x")
        top.pack_propagate(False)
        tk.Label(top, text=APP_NAME, bg=PRIMARY, fg="#FFFFFF", font=(FONT, 14, "bold")).pack(
            anchor="w", padx=S(22), pady=(S(7), 0)
        )
        tk.Label(top, text="自动识别水印 · 精确保留正文 · 支持批量处理", bg=PRIMARY,
                 fg="#DBEAFE", font=(FONT, 9)).pack(anchor="w", padx=S(22))

        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True, padx=S(18), pady=S(12))

        # ① 文件
        c1 = Card(body)
        c1.pack(fill="x", pady=(0, S(10)))
        h1 = tk.Frame(c1, bg=CARD)
        h1.pack(fill="x", padx=S(16), pady=(S(12), S(6)))
        StepBadge(h1, 1).pack(side="left")
        tk.Label(h1, text="选择 PDF 文件（可多选，批量处理）", bg=CARD, fg=TEXT,
                 font=(FONT, 11, "bold")).pack(side="left", padx=S(10))

        wrap1 = tk.Frame(c1, bg=CARD)
        wrap1.pack(fill="both", padx=S(16), pady=(0, S(8)))
        self.tree_files = ttk.Treeview(wrap1, columns=("file", "status"), show="headings",
                                       height=3, style="T.Treeview")
        self.tree_files.heading("file", text="文件")
        self.tree_files.heading("status", text="状态")
        self.tree_files.column("file", width=S(560), anchor="w")
        self.tree_files.column("status", width=S(110), anchor="center")
        self.tree_files.pack(side="left", fill="both", expand=True)
        sb1 = ttk.Scrollbar(wrap1, orient="vertical", command=self.tree_files.yview)
        self.tree_files.configure(yscrollcommand=sb1.set)
        sb1.pack(side="left", fill="y")

        bar1 = tk.Frame(c1, bg=CARD)
        bar1.pack(fill="x", padx=S(16), pady=(0, S(12)))
        RoundedButton(bar1, "添加文件", self.add_files, kind="primary", height=36).pack(side="left")
        RoundedButton(bar1, "移除选中", self.remove_selected, kind="ghost", height=36).pack(side="left", padx=S(10))
        RoundedButton(bar1, "清空", self.clear_files, kind="ghost", height=36).pack(side="left")

        # ② 水印
        c2 = Card(body)
        c2.pack(fill="x", pady=(0, S(10)))
        h2 = tk.Frame(c2, bg=CARD)
        h2.pack(fill="x", padx=S(16), pady=(S(12), S(6)))
        StepBadge(h2, 2).pack(side="left")
        tk.Label(h2, text="水印文字", bg=CARD, fg=TEXT, font=(FONT, 11, "bold")).pack(side="left", padx=S(10))
        self.btn_detect = RoundedButton(h2, "自动识别", self.do_detect, kind="primary", height=34)
        self.btn_detect.pack(side="right")
        self.lbl_detect = tk.Label(h2, text="建议先点“自动识别”", bg=CARD, fg=MUTED, font=(FONT, 9))
        self.lbl_detect.pack(side="right", padx=S(10))

        row2 = tk.Frame(c2, bg=CARD)
        row2.pack(fill="x", padx=S(16), pady=(2, S(6)))
        self.var_wm = tk.StringVar()
        self.entry_wm = tk.Entry(row2, textvariable=self.var_wm, font=(FONT, 11), relief="flat",
                                 bg=SOFT, fg=MUTED, highlightthickness=1,
                                 highlightbackground=BORDER, highlightcolor=PRIMARY,
                                 insertbackground=TEXT)
        self.entry_wm.pack(fill="x", ipady=S(8))
        self.entry_wm.insert(0, WM_PLACEHOLDER)
        self.entry_wm.bind("<FocusIn>", self._wm_in)
        self.entry_wm.bind("<FocusOut>", self._wm_out)

        wrap2 = tk.Frame(c2, bg=CARD)
        wrap2.pack(fill="both", padx=S(16), pady=(2, S(4)))
        self.tree_cand = ttk.Treeview(wrap2, columns=("text", "hit", "feat"), show="headings",
                                      height=2, style="T.Treeview")
        self.tree_cand.heading("text", text="识别到的候选水印")
        self.tree_cand.heading("hit", text="命中")
        self.tree_cand.heading("feat", text="特征")
        self.tree_cand.column("text", width=S(430), anchor="w")
        self.tree_cand.column("hit", width=S(100), anchor="center")
        self.tree_cand.column("feat", width=S(240), anchor="w")
        self.tree_cand.pack(side="left", fill="both", expand=True)
        sb2 = ttk.Scrollbar(wrap2, orient="vertical", command=self.tree_cand.yview)
        self.tree_cand.configure(yscrollcommand=sb2.set)
        sb2.pack(side="left", fill="y")
        self.tree_cand.bind("<<TreeviewSelect>>", self._on_pick_candidate)
        tk.Label(c2, text="点击候选行即可填入上方输入框；识别不准时可直接手工修改",
                 bg=CARD, fg=MUTED, font=(FONT, 9)).pack(anchor="w", padx=S(16), pady=(2, S(6)))

        opts = tk.Frame(c2, bg=CARD)
        opts.pack(fill="x", padx=S(16), pady=(0, S(12)))
        self.var_case = tk.BooleanVar(value=True)
        self.var_meta = tk.BooleanVar(value=False)
        ModernCheck(opts, "忽略大小写", self.var_case).pack(side="left")
        ModernCheck(opts, "同时清理文档属性中的该文字", self.var_meta).pack(side="left", padx=S(20))

        # ③ 输出
        c3 = Card(body)
        c3.pack(fill="x", pady=(0, S(10)))
        h3 = tk.Frame(c3, bg=CARD)
        h3.pack(fill="x", padx=S(16), pady=(S(12), S(4)))
        StepBadge(h3, 3).pack(side="left")
        tk.Label(h3, text="保存方式", bg=CARD, fg=TEXT, font=(FONT, 11, "bold")).pack(side="left", padx=S(10))

        self.var_mode = tk.StringVar(value="suffix")
        r1 = tk.Frame(c3, bg=CARD)
        r1.pack(fill="x", padx=S(16), pady=2)
        Radio(r1, "保存到原目录，文件名加 “_去水印”", self.var_mode, "suffix").pack(side="left")
        r2 = tk.Frame(c3, bg=CARD)
        r2.pack(fill="x", padx=S(16), pady=2)
        Radio(r2, "覆盖原文件（请先备份）", self.var_mode, "overwrite").pack(side="left")
        r3 = tk.Frame(c3, bg=CARD)
        r3.pack(fill="x", padx=S(16), pady=(2, S(12)))
        Radio(r3, "保存到指定目录：", self.var_mode, "dir").pack(side="left")
        self.var_dir = tk.StringVar()
        self.entry_dir = tk.Entry(r3, textvariable=self.var_dir, font=(FONT, 10), relief="flat",
                                  bg=SOFT, highlightthickness=1, highlightbackground=BORDER,
                                  highlightcolor=PRIMARY, width=38)
        self.entry_dir.pack(side="left", padx=S(10), ipady=S(5))
        RoundedButton(r3, "浏览", self.pick_dir, kind="ghost", height=34).pack(side="left")

        # 操作区
        act = tk.Frame(body, bg=BG)
        act.pack(fill="x", pady=(0, S(8)))
        self.btn_run = RoundedButton(act, "开始去除水印", self.do_run, kind="primary", height=44,
                                     font_size=11, pad_x=34)
        self.btn_run.pack(side="left")
        self.btn_cancel = RoundedButton(act, "停止", self.do_cancel, kind="ghost", height=44,
                                        font_size=11, pad_x=30)
        self.btn_cancel.pack(side="left", padx=S(12))
        self.btn_cancel.set_state("disabled")
        self.btn_open = RoundedButton(act, "打开输出目录", self.open_output_dir, kind="ghost", height=44,
                                      font_size=11, pad_x=30)
        self.btn_open.pack(side="right")
        self.btn_open.set_state("disabled")

        # 进度
        self.pbar = ttk.Progressbar(body, mode="determinate", maximum=100,
                                    style="P.Horizontal.TProgressbar")
        self.pbar.pack(fill="x", pady=(0, S(4)))
        self.var_prog = tk.StringVar(value="就绪")
        tk.Label(body, textvariable=self.var_prog, bg=BG, fg=MUTED, font=(FONT, 10)).pack(anchor="w")

        # 日志
        c4 = Card(body)
        c4.pack(fill="both", expand=True, pady=(S(8), 0))
        lh = tk.Frame(c4, bg=CARD)
        lh.pack(fill="x", padx=S(16), pady=(S(10), S(4)))
        tk.Label(lh, text="处理日志", bg=CARD, fg=TEXT, font=(FONT, 11, "bold")).pack(side="left")
        self.logbox = tk.Text(c4, height=4, wrap="word", font=(FONT, 9), bg=SOFT,
                              fg=TEXT, relief="flat", bd=0, highlightthickness=0)
        self.logbox.pack(fill="both", expand=True, padx=S(16), pady=(0, S(12)))
        self.logbox.configure(state="disabled")

    # ---------------- 文件列表 ----------------
    def add_files(self):
        paths = filedialog.askopenfilenames(title="选择 PDF 文件",
                                            filetypes=[("PDF 文件", "*.pdf"), ("所有文件", "*.*")])
        added = 0
        for p in paths:
            if p in self.files:
                continue
            self.files.append(p)
            self.tree_files.insert("", "end", iid=p, values=(os.path.basename(p), "待处理"))
            added += 1
        if added:
            self.log("已添加 %d 个文件" % added)

    def remove_selected(self):
        for iid in self.tree_files.selection():
            self.tree_files.delete(iid)
            if iid in self.files:
                self.files.remove(iid)

    def clear_files(self):
        for iid in self.tree_files.get_children():
            self.tree_files.delete(iid)
        self.files.clear()
        for iid in self.tree_cand.get_children():
            self.tree_cand.delete(iid)
        self.candidates = []

    def pick_dir(self):
        d = filedialog.askdirectory(title="选择输出目录")
        if d:
            self.var_dir.set(d)
            self.var_mode.set("dir")

    def _wm_in(self, _e=None):
        if self.entry_wm.get() == WM_PLACEHOLDER:
            self.entry_wm.delete(0, "end")
            self.entry_wm.configure(fg=TEXT)

    def _wm_out(self, _e=None):
        if not self.entry_wm.get().strip():
            self.entry_wm.insert(0, WM_PLACEHOLDER)
            self.entry_wm.configure(fg=MUTED)

    def get_wm(self) -> str:
        v = self.var_wm.get().strip()
        return "" if v == WM_PLACEHOLDER else v

    def set_wm(self, text: str):
        self.entry_wm.delete(0, "end")
        self.entry_wm.insert(0, text)
        self.entry_wm.configure(fg=TEXT)

    # ---------------- 日志 / 队列 ----------------
    def log(self, msg: str):
        self.logbox.configure(state="normal")
        self.logbox.insert("end", msg + "\n")
        self.logbox.see("end")
        self.logbox.configure(state="disabled")

    def _poll(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "log":
                    self.log(payload)
                elif kind == "prog":
                    value, text = payload
                    self.pbar["value"] = value
                    self.var_prog.set(text)
                elif kind == "cand":
                    self._fill_candidates(payload)
                elif kind == "status":
                    iid, text = payload
                    if self.tree_files.exists(iid):
                        vals = list(self.tree_files.item(iid, "values"))
                        vals[1] = text
                        self.tree_files.item(iid, values=tuple(vals))
                elif kind == "done":
                    self._on_done(payload)
        except queue.Empty:
            pass
        self.root.after(80, self._poll)

    def _fill_candidates(self, cands):
        for iid in self.tree_cand.get_children():
            self.tree_cand.delete(iid)
        self.candidates = cands
        for i, c in enumerate(cands):
            txt = c["text"]
            if len(txt) > 44:
                txt = txt[:42] + "…"
            self.tree_cand.insert(
                "", "end", iid=str(i),
                values=(txt, "%d页/%d次" % (c["pages"], c["count"]), c["features"]),
            )
        if cands:
            self.tree_cand.selection_set("0")
            self.set_wm(cands[0]["text"])

    def _on_pick_candidate(self, _e=None):
        sel = self.tree_cand.selection()
        if not sel or not self.candidates:
            return
        try:
            idx = int(sel[0])
        except ValueError:
            return
        if 0 <= idx < len(self.candidates):
            self.set_wm(self.candidates[idx]["text"])

    def _on_done(self, payload):
        self.worker = None
        self.btn_run.set_state("normal")
        self.btn_detect.set_state("normal")
        self.btn_cancel.set_state("disabled")
        self.pbar["value"] = 100
        if payload.get("prog_text"):
            self.var_prog.set(payload["prog_text"])
        if payload.get("outputs"):
            self.last_outputs = payload["outputs"]
            self.btn_open.set_state("normal")
        if payload.get("message"):
            messagebox.showinfo(APP_NAME, payload["message"])
        if payload.get("warn"):
            messagebox.showwarning(APP_NAME, payload["warn"])

    def open_output_dir(self):
        for p in self.last_outputs[:1]:
            d = os.path.dirname(os.path.abspath(p))
            try:
                os.startfile(d)  # type: ignore[attr-defined]
            except Exception:
                try:
                    subprocess.Popen(["explorer", d])
                except Exception:
                    pass

    # ---------------- 自动识别 ----------------
    def do_detect(self):
        if not self.files:
            messagebox.showwarning(APP_NAME, "请先添加至少一个 PDF 文件")
            return
        self.btn_detect.set_state("disabled")
        self.lbl_detect.configure(text="正在分析…")
        src = self.files[0]

        def work():
            try:
                self.q.put(("log", "自动识别：%s" % os.path.basename(src)))
                cands = analyze_watermarks(src, max_pages=6)
                self.q.put(("cand", cands))
                if cands:
                    self.q.put(("log", "  识别到 %d 个候选，已自动填入最可能的一个：%s"
                                % (len(cands), cands[0]["text"][:40])))
                    msg = None
                else:
                    self.q.put(("log", "  未能自动识别到水印，请手工输入水印文字"))
                    msg = "未能自动识别到水印。\n请在“水印文字”输入框中手工填写水印内容后重试。"
                self.q.put(("done", {"message": msg, "prog_text": "识别完成：%d 个候选" % len(cands)}))
            except Exception:
                self.q.put(("log", "识别失败：" + traceback.format_exc(limit=3)))
                self.q.put(("done", {"warn": "自动识别失败，请手工输入水印文字", "prog_text": "识别失败"}))
            finally:
                self.lbl_detect.configure(text="建议先点“自动识别”")

        threading.Thread(target=work, daemon=True).start()

    # ---------------- 执行 ----------------
    def validate(self):
        if not self.files:
            messagebox.showwarning(APP_NAME, "请先添加至少一个 PDF 文件")
            return False
        if self.var_mode.get() == "dir" and not self.var_dir.get().strip():
            messagebox.showwarning(APP_NAME, "请选择输出目录")
            return False
        return True

    def dest_for(self, src: str) -> str:
        mode = self.var_mode.get()
        if mode == "overwrite":
            return src
        if mode == "dir":
            return os.path.join(self.var_dir.get().strip(), os.path.basename(src))
        root, ext = os.path.splitext(src)
        return "%s_去水印%s" % (root, ext)

    def do_run(self):
        if not self.validate():
            return
        if self.var_mode.get() == "overwrite":
            if not messagebox.askyesno(APP_NAME, "将直接覆盖原文件，建议先备份。确定继续？"):
                return
        self.btn_run.set_state("disabled")
        self.btn_detect.set_state("disabled")
        self.btn_cancel.set_state("normal")
        self.last_outputs = []
        self.btn_open.set_state("disabled")
        self.cancel_flag = False

        files = list(self.files)
        wm = self.get_wm()
        case_sensitive = not self.var_case.get()
        meta = self.var_meta.get()

        def work():
            wm_use = wm
            if not wm_use:
                self.q.put(("log", "未填写水印文字，先尝试自动识别…"))
                try:
                    cands = analyze_watermarks(files[0], max_pages=6)
                except Exception:
                    cands = []
                self.q.put(("cand", cands))
                if not cands:
                    self.q.put(("done", {
                        "warn": "未能自动识别水印。\n请在“水印文字”输入框中手工填写后重试。",
                        "prog_text": "需要手工输入水印文字"}))
                    return
                wm_use = cands[0]["text"]
                self.q.put(("log", "  自动识别结果：%s" % wm_use[:40]))

            ok = fail = 0
            outputs = []
            for idx, src in enumerate(files):
                if self.cancel_flag:
                    self.q.put(("log", "已停止"))
                    break
                base = os.path.basename(src)
                self.q.put(("status", (src, "处理中")))
                self.q.put(("log", "[%d/%d] %s" % (idx + 1, len(files), base)))
                try:
                    dst = self.dest_for(src)

                    def cb(pno, total, msg):
                        pct = (idx + (pno + 1) / max(total, 1)) / len(files) * 100
                        self.q.put(("prog", (pct, "%s：%s" % (base, msg))))

                    res = remove_watermark(src, dst, wm_use, case_sensitive=case_sensitive,
                                           clean_metadata=meta, overwrite=True, progress=cb)
                    for line in res.log:
                        self.q.put(("log", "  " + line))
                    outputs.append(dst)
                    self.q.put(("log", "  输出：%s" % dst))
                    self.q.put(("status", (src, "完成" if res.residual == 0 else "残留 %d 页" % res.residual)))
                    ok += 1
                except Exception:
                    fail += 1
                    self.q.put(("status", (src, "失败")))
                    self.q.put(("log", "  失败：" + traceback.format_exc(limit=3)))
            msg = "处理完成：成功 %d 个，失败 %d 个" % (ok, fail)
            self.q.put(("done", {"ok": ok, "fail": fail, "outputs": outputs,
                                 "message": msg, "prog_text": msg}))

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def do_cancel(self):
        self.cancel_flag = True
        self.btn_cancel.set_state("disabled")


# ---------------------------------- 命令行 / 自测 ----------------------------------
def cli_main(argv):
    if len(argv) < 3:
        print("用法: app.exe --cli <源文件.pdf> <输出文件.pdf> <水印文字|auto> [--report 日志.txt]")
        return 2
    src, dst, wm = argv[0], argv[1], argv[2]
    report = None
    if "--report" in argv:
        i = argv.index("--report")
        if i + 1 < len(argv):
            report = argv[i + 1]
    lines = []

    def echo(s):
        lines.append(str(s))
        try:
            if sys.stdout:
                print(s)
        except Exception:
            pass

    try:
        echo("源: %s" % src)
        echo("目标: %s" % dst)
        if wm == "auto":
            cands = analyze_watermarks(src, max_pages=6)
            echo("自动识别候选: %d" % len(cands))
            for c in cands[:5]:
                echo("  score=%s pages=%s count=%s | %s | %s"
                     % (c["score"], c["pages"], c["count"], c["features"], c["text"][:40]))
            if not cands:
                echo("AUTO_NOT_FOUND")
                return 4
            wm = cands[0]["text"]
        echo("水印: %s" % wm)
        res = remove_watermark(src, dst, wm, overwrite=True)
        for line in res.log:
            echo(line)
        echo("residual=%d" % res.residual)
        code = 0 if res.residual == 0 else 1
    except Exception:
        echo(traceback.format_exc(limit=5))
        code = 3
    if report:
        try:
            with open(report, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
        except Exception:
            pass
    return code


def selftest(root: tk.Tk, app: App):
    src = os.environ.get("PDFWM_SELFTEST_SRC", "")
    out = os.environ.get("PDFWM_SELFTEST_OUT", "")
    if not (src and os.path.exists(src)):
        root.destroy()
        return
    app.files.append(src)
    app.tree_files.insert("", "end", iid=src, values=(os.path.basename(src), "待处理"))
    app.var_mode.set("dir")
    app.var_dir.set(out or os.path.dirname(os.path.abspath(src)))
    app.log("[selftest] 界面构建成功，开始自动流程")
    app.do_run()

    def watch():
        if app.worker is None:
            try:
                with open(os.path.join(out or ".", "_selftest.log"), "w", encoding="utf-8") as f:
                    f.write(app.logbox.get("1.0", "end"))
                    f.write("\nwindow=%dx%d scale=%.2f req=%dx%d"
                            % (root.winfo_width(), root.winfo_height(), _SCALE,
                               root.winfo_reqwidth(), root.winfo_reqheight()))
            except Exception:
                pass
            root.destroy()
            return
        root.after(300, watch)

    root.after(500, watch)


def main():
    argv = sys.argv[1:]
    if argv and argv[0] == "--cli":
        return cli_main(argv[1:])
    _enable_dpi()
    root = tk.Tk()
    app = App(root)
    if os.environ.get("PDFWM_SELFTEST"):
        root.after(200, lambda: selftest(root, app))
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
