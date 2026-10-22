#!/usr/bin/env python3
"""生成图标资产：assets/refwatch.png（256）与 assets/refwatch.ico（7 个尺寸）。

为什么是「脚本 + 产物入库」而不是构建时生成：图标是品牌资产，Nuitka 打包只需要文件本身；
同一次改动里画一次、提交结果，构建就不必再依赖 Pillow 的绘制行为。

    python tools/make_icon.py            # 重新生成（会覆盖 assets/ 下两个文件）
    python tools/make_icon.py --check    # 校验入库产物与脚本一致，不一致退出 1

形象：深色圆角方块里一个「分支」图形 —— 三个节点，一条竖线，一条斜出。
小尺寸（16/24）只保留这个粗轮廓，所以线和点都刻意画得厚。
"""

from __future__ import annotations

import sys
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
ASSETS = HERE.parent / "assets"
ICO_PATH = ASSETS / "refwatch.ico"
PNG_PATH = ASSETS / "refwatch.png"

SIZES = (16, 24, 32, 48, 64, 128, 256)
MASTER = 1024  # 先在大画布上画，再降采样：边缘干净，且各处比例一致

BG = (15, 23, 42, 255)  # slate-900
EDGE = (51, 65, 85, 255)  # slate-700
LINE = (226, 232, 240, 255)  # slate-200
NODE = (56, 189, 248, 255)  # sky-400


def render(size: int) -> Image.Image:
    """画一张 size×size 的 RGBA 图。坐标全部按比例给，任何尺寸都不会错位。"""
    width = float(size)
    radius = max(1, round(width * 0.22))
    stroke = max(1, round(width * 0.028))
    trunk = max(1, round(width * 0.085))
    node = max(2, round(width * 0.105))

    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(
        (0, 0, size - 1, size - 1), radius=radius, fill=BG, outline=EDGE, width=stroke
    )

    trunk_x = width * 0.32
    draw.line(((trunk_x, width * 0.30), (trunk_x, width * 0.70)), fill=LINE, width=trunk)
    draw.line(((trunk_x, width * 0.52), (width * 0.68, width * 0.40)), fill=LINE, width=trunk)

    for center_x, center_y in ((trunk_x, width * 0.30), (trunk_x, width * 0.70), (width * 0.68, width * 0.40)):
        draw.ellipse(
            (center_x - node, center_y - node, center_x + node, center_y + node),
            fill=NODE,
        )
    return image


def frames() -> dict[int, Image.Image]:
    """每个目标尺寸都用 LANCZOS 从大画布降采样，而不是先小后大。"""
    master = render(MASTER)
    return {size: master.resize((size, size), Image.LANCZOS) for size in SIZES}


def build_png() -> bytes:
    buffer = BytesIO()
    frames()[256].save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def build_ico() -> bytes:
    """ICO 里放的是同一批降采样结果（Pillow 按尺寸精确匹配 append_images，不会二次缩放）。"""
    scaled = frames()
    buffer = BytesIO()
    scaled[256].save(
        buffer,
        format="ICO",
        sizes=[(size, size) for size in SIZES],
        append_images=[scaled[size] for size in SIZES if size != 256],
    )
    return buffer.getvalue()


def write() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    PNG_PATH.write_bytes(build_png())
    ICO_PATH.write_bytes(build_ico())
    print(f"[icon] 已生成 {PNG_PATH.name} ({PNG_PATH.stat().st_size} B) 与 {ICO_PATH.name} ({len(SIZES)} 个尺寸)")


def check() -> int:
    problems: list[str] = []
    if not PNG_PATH.is_file() or PNG_PATH.read_bytes() != build_png():
        problems.append(f"{PNG_PATH.name} 与脚本不一致（跑一次 python tools/make_icon.py）")
    if not ICO_PATH.is_file() or ICO_PATH.read_bytes() != build_ico():
        problems.append(f"{ICO_PATH.name} 与脚本不一致（跑一次 python tools/make_icon.py）")
    for problem in problems:
        print(f"[icon] {problem}", file=sys.stderr)
    if problems:
        return 1
    print("[icon] 入库产物与脚本一致")
    return 0


def main(argv: list[str]) -> int:
    if "--check" in argv:
        return check()
    if "--help" in argv or "-h" in argv:
        print(__doc__)
        return 0
    write()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
