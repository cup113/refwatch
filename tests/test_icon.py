"""图标资产的形状守卫：只用 struct 解析，不依赖 Pillow —— 零依赖 CI 矩阵里也要能跑。

真正「画得好看」没法断言；能断言的是 Windows 实际会用到的那几件事：
ICO 头合法、7 个尺寸齐全（16 的托盘小图标到 256 的高 DPI）、每一帧都在文件内，
以及 PNG 是 256×256。
"""

from __future__ import annotations

import struct
import unittest
from pathlib import Path

ASSETS = Path(__file__).resolve().parent.parent / "assets"
ICO = ASSETS / "refwatch.ico"
PNG = ASSETS / "refwatch.png"

EXPECTED_SIZES = {16, 24, 32, 48, 64, 128, 256}
ICO_HEADER = 6
ICO_ENTRY = 16


def ico_entries(data: bytes) -> list[dict]:
    reserved, kind, count = struct.unpack_from("<HHH", data, 0)
    if reserved != 0 or kind != 1:
        raise AssertionError(f"不是 ICO：reserved={reserved} type={kind}")
    entries = []
    for index in range(count):
        offset = ICO_HEADER + index * ICO_ENTRY
        width, height, colors, _, planes, bits, length, data_offset = struct.unpack_from("<BBBBHHII", data, offset)
        entries.append(
            {
                "size": (width or 256, height or 256),
                "colors": colors,
                "planes": planes,
                "bits": bits,
                "length": length,
                "offset": data_offset,
            }
        )
    return entries


class IconAssetTest(unittest.TestCase):
    def test_ico_has_every_size_the_tray_needs(self) -> None:
        entries = ico_entries(ICO.read_bytes())
        self.assertEqual(len(entries), len(EXPECTED_SIZES))
        self.assertEqual({entry["size"][0] for entry in entries}, EXPECTED_SIZES)
        self.assertEqual({entry["size"][1] for entry in entries}, EXPECTED_SIZES, "图标必须是正方形")

    def test_every_ico_frame_lies_inside_the_file(self) -> None:
        data = ICO.read_bytes()
        for entry in ico_entries(data):
            self.assertGreater(entry["length"], 0)
            self.assertLessEqual(entry["offset"] + entry["length"], len(data), f"帧越界：{entry}")
        spans = sorted((entry["offset"], entry["offset"] + entry["length"]) for entry in ico_entries(data))
        for (_, first_end), (second_start, _) in zip(spans, spans[1:]):
            self.assertLessEqual(first_end, second_start, "帧之间不能重叠")

    def test_each_frame_is_a_png_or_bmp_not_garbage(self) -> None:
        data = ICO.read_bytes()
        for entry in ico_entries(data):
            frame = data[entry["offset"] : entry["offset"] + entry["length"]]
            self.assertIn(
                frame[:8],
                (b"\x89PNG\r\n\x1a\n", b"\x28\x00\x00\x00"),
                f"{entry['size']} 的帧既不是 PNG 也不是 DIB",
            )

    def test_png_is_256_square(self) -> None:
        data = PNG.read_bytes()
        self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n", "不是 PNG")
        width, height = struct.unpack_from(">II", data, 16)  # IHDR 紧跟在 8 字节签名 + 4 长度 + 4 类型之后
        self.assertEqual((width, height), (256, 256))

    def test_assets_are_committed_next_to_the_program(self) -> None:
        """install.ps1 与 Nuitka 都按相对路径取资源；目录结构变了要一起改。"""
        self.assertTrue(ICO.is_file(), f"缺少 {ICO}")
        self.assertTrue(PNG.is_file(), f"缺少 {PNG}")
        self.assertEqual(ICO.parent.name, "assets")


if __name__ == "__main__":
    unittest.main()
