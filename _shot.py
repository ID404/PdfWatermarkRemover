# -*- coding: utf-8 -*-
"""启动 GUI，截取窗口图片用于预览确认。"""
import ctypes
import os
import subprocess
import sys
import time

from PIL import ImageGrab

TITLE = "PDF 水印去除工具  v1.3"
PY = r"C:/Users/ljc89/.workbuddy/binaries/python/versions/3.11.9/python.exe"
APP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.py")
CMD = [PY, APP]
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_preview.png")

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass

user32 = ctypes.windll.user32
rect = ctypes.wintypes.RECT if hasattr(ctypes, "wintypes") else None


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


def main():
    subprocess.run(["taskkill", "/F", "/IM", "python.exe", "/FI", "WINDOWTITLE eq PDF*"], capture_output=True)
    p = subprocess.Popen(CMD)
    hwnd = None
    for _ in range(60):
        time.sleep(0.5)
        hwnd = user32.FindWindowW(None, TITLE)
        if hwnd:
            break
    if not hwnd:
        print("窗口未找到")
        subprocess.run(["taskkill", "/F", "/IM", "PDFWatermarkRemover.exe"], capture_output=True)
        return 1
    time.sleep(2.0)
    r = RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    bbox = (r.left, r.top, r.right, r.bottom)
    print("bbox", bbox)
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.6)
    img = ImageGrab.grab(bbox=bbox)
    img.save(OUT)
    print("saved", OUT, img.size)
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
