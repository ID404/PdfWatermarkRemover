# -*- coding: utf-8 -*-
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pdf_wm_core import remove_watermark, scan_document  # noqa: E402

WM = "成都市快猎聘管理咨询有限公司办公网扩容升级项目722155"
DL = "E:/Downloads"
OUT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def find_pdfs():
    out = []
    for name in sorted(os.listdir(DL)):
        if name.lower().endswith(".pdf") and "RG-NBS1850GC" in name:
            out.append(os.path.join(DL, name))
    return out


def main():
    ok = True
    for i, src in enumerate(find_pdfs()):
        print("=== " + os.path.basename(src), os.path.getsize(src))
        try:
            info = scan_document(src, WM)
        except Exception as e:
            print("  scan err:", e)
            continue
        print("  scan:", info)
        if not info["hit_pages"]:
            print("  (无水印，跳过)")
            continue
        dst = os.path.join(OUT, "_t_%d.pdf" % i)
        r = remove_watermark(src, dst, WM)
        print("  " + r.as_text().replace("\n", "\n  "))
        if r.residual != 0:
            ok = False
        print()
    print("ALL_OK" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
