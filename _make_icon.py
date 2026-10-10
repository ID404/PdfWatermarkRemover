# -*- coding: utf-8 -*-
"""生成应用图标 app.ico"""
import os

from PIL import Image, ImageDraw, ImageFont

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.ico")
SIZE = 256


def load_font(px):
    for path in (
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/msyhbd.ttc",
        "C:/Windows/Fonts/simhei.ttf",
    ):
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, px)
            except Exception:
                pass
    return ImageFont.load_default()


def main():
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # 圆角底
    d.rounded_rectangle([6, 6, SIZE - 7, SIZE - 7], radius=48, fill=(32, 92, 168, 255))

    # 白色纸张
    d.polygon([(78, 46), (168, 46), (188, 74), (188, 210), (78, 210)], fill=(255, 255, 255, 255))
    d.polygon([(168, 46), (188, 74), (168, 74)], fill=(206, 218, 232, 255))

    # 页面上的"文字线"
    for y in (98, 120, 142, 164):
        d.line([(96, y), (168, y)], fill=(150, 168, 190, 255), width=6)

    # 斜向水印划掉
    d.line([(88, 186), (176, 92)], fill=(214, 66, 66, 255), width=10)

    # 右上"PDF"
    f = load_font(40)
    d.text((128, 196), "PDF", font=f, fill=(255, 255, 255, 255), anchor="mm")

    img.save(OUT, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("icon:", OUT, os.path.getsize(OUT))


if __name__ == "__main__":
    main()
