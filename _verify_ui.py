# -*- coding: utf-8 -*-
"""校验：所有画布控件的宽度必须容得下其中文文字（防止裁切）。"""
import os
import sys

import tkinter as tk
from tkinter import font as tkfont

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import app as A  # noqa: E402


def main():
    root = tk.Tk()
    root.withdraw()
    A._enable_dpi()
    root.update_idletasks()
    app = A.App(root)
    root.update_idletasks()

    bad = []
    widgets = []

    def walk(w):
        widgets.append(w)
        for c in w.winfo_children():
            walk(c)

    walk(root)

    for w in widgets:
        if isinstance(w, A.RoundedButton):
            need = A._measure(w.text, w.font)
            if w.width < need + 8:
                bad.append("按钮 %r 宽 %d < 文字 %d" % (w.text, w.width, need))
        elif isinstance(w, A.ModernCheck):
            start = w.box + A.S(8)
            need = start + A._measure(w.text, (A.FONT, 10))
            if int(w.cget("width")) < need + 4:
                bad.append("复选 %r 宽 %s < 需要 %d" % (w.text, w.cget("width"), need))
        elif isinstance(w, A.Radio):
            start = w.d + A.S(8)
            need = start + A._measure(w.text, (A.FONT, 10))
            if int(w.cget("width")) < need + 4:
                bad.append("单选 %r 宽 %s < 需要 %d" % (w.text, w.cget("width"), need))

    scale = A._SCALE
    print("scale=%.2f  window=%dx%d  req=%dx%d" % (scale, root.winfo_width(), root.winfo_height(),
                                                   root.winfo_reqwidth(), root.winfo_reqheight()))
    if bad:
        print("FAIL:")
        for b in bad:
            print("  " + b)
        return 1
    print("OK: 所有控件尺寸足以容纳文字")
    return 0


if __name__ == "__main__":
    sys.exit(main())
