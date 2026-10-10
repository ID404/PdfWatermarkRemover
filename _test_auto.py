# -*- coding: utf-8 -*-
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pdf_wm_core import analyze_watermarks  # noqa: E402

SRCS = [
    r"E:/Downloads/RG-NBS1850GC V2_进网证书.pdf",
    r"E:/Downloads/RG-NBS1850GC V2_进网报告_Copy_unsigned.pdf",
]


def main():
    for src in SRCS:
        if not os.path.exists(src):
            print("skip", src)
            continue
        t0 = time.time()
        cands = analyze_watermarks(src, max_pages=6, progress=None)
        print("=== %s  用时 %.1fs" % (os.path.basename(src), time.time() - t0))
        for c in cands[:5]:
            print(
                "  score=%-4d pages=%-3d count=%-4d alpha=%-5s rot=%-5s | %s | %s"
                % (
                    c["score"],
                    c["pages"],
                    c["count"],
                    c["alpha"],
                    c["rotated"],
                    c["features"],
                    c["text"][:40],
                )
            )
        if not cands:
            print("  (未识别到候选)")


if __name__ == "__main__":
    main()
