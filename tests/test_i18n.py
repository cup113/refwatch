"""语言表本身的守卫：两张表键严格一致、占位符一致、回退规则、_one 不变量。

「全中文或全英文」（issue #2）的机制在 i18n.py：如果某个键只在一语言里存在，
另一种语言会在运行时 AssertionError —— 那是程序错误，不是可回退的状态。
"""

from __future__ import annotations

import string
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import i18n  # noqa: E402


def placeholders(template: str) -> set[str]:
    return {field_name for _literal, field_name, _spec, _conv in string.Formatter().parse(template) if field_name}


class ParityTest(unittest.TestCase):
    def test_both_tables_have_exactly_the_same_keys(self) -> None:
        self.assertEqual(set(i18n.STRINGS["zh"]), set(i18n.STRINGS["en"]))

    def test_placeholders_match_across_languages(self) -> None:
        for key in i18n.STRINGS["zh"]:
            self.assertEqual(
                placeholders(i18n.STRINGS["zh"][key]),
                placeholders(i18n.STRINGS["en"][key]),
                f"{key} 的占位符在两语言里不一致",
            )

    def test_zh_singular_variants_equal_their_plural_forms(self) -> None:
        """代码在计数为 1 时选 `_one`，不按语言分叉 —— 前提是 zh 两式同文。"""
        for key in i18n.STRINGS["zh"]:
            if key.endswith("_one"):
                self.assertEqual(
                    i18n.STRINGS["zh"][key],
                    i18n.STRINGS["zh"][key[:-4]],
                    f"zh 没有单复数变化，{key} 应与 {key[:-4]} 同文",
                )


class NormalizeTest(unittest.TestCase):
    def test_known_values_pass_through_case_insensitively(self) -> None:
        self.assertEqual(i18n.normalize_language("zh"), "zh")
        self.assertEqual(i18n.normalize_language("en"), "en")
        self.assertEqual(i18n.normalize_language(" EN "), "en")

    def test_anything_else_falls_back_to_zh(self) -> None:
        for value in ("fr", "", None, 1, "中文", []):
            self.assertEqual(i18n.normalize_language(value), "zh", value)


class TranslateTest(unittest.TestCase):
    def test_fills_named_placeholders(self) -> None:
        self.assertEqual(i18n.t("zh", "digest.branch_deleted", ref="gone"), "分支已删除: gone")
        self.assertEqual(i18n.t("en", "digest.branch_deleted", ref="gone"), "branch deleted: gone")

    def test_plain_template_needs_no_kwargs(self) -> None:
        self.assertEqual(i18n.t("en", "tray.menu_quit"), "Quit")

    def test_unknown_lang_falls_back_to_zh(self) -> None:
        self.assertEqual(i18n.t("klingon", "tray.menu_quit"), "退出")

    def test_missing_key_is_a_program_error_not_a_fallback(self) -> None:
        with self.assertRaises(AssertionError):
            i18n.t("zh", "digest.does_not_exist")


if __name__ == "__main__":
    unittest.main()
