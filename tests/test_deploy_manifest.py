"""部署清单守卫：仓库根目录的运行时模块 ⟷ `install.ps1` 的白名单，两集合必须一致。

这个 bug 的形状很固定：新增一个顶层模块（`i18n.py`、`channels.py`……），CI 全绿 ——
因为测试与 ruff 都在源码树里跑 —— 但 `install.ps1` 只拷贝它自己列出的文件，于是
**部署出来的副本**一 import 就 `ModuleNotFoundError`。靠「记得改 install.ps1」不成立，
所以把两个集合的一致性变成断言。

三个方向都查：

1. 目录（**git 追踪的**根级 `.py`）− 黑名单 == `install.ps1` 白名单；
2. 白名单 ⊆ 追踪文件 —— 列了不存在的文件会让 `install.ps1` 在第 0 步 Fail；
3. 黑名单 ∩ 入口可达模块 == ∅ —— 黑名单不能把入口真正 import 的模块藏起来。

左边界用 `git ls-files` 而不是 `Path.glob`：部署清单跟着仓库走，**未追踪**的本地文件
（并行会话留下的 WIP、临时脚本）本来就不会被部署，用文件系统会把它们当成假阳性；
一旦提交，它就在 `git ls-files` 里，守卫照样生效。没有 git 时退回文件系统扫描。

exe 形态不受这条约束：Nuitka 的 dist 按 import 图生成，`installer.iss` 用
`build\\tray.dist\\*` 递归拷贝。只做源码级检查，不 import 任何 GUI 库。
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent

# 部署的两个入口：托盘应用，以及无头 / 计划任务用的 CLI。
ENTRIES = ("tray.py", "watch.py")

# 根级但**不**随源码部署的 .py（开发脚本之类）。目前没有。
# 往这里加一条 = 声明「它不是运行时模块」；若入口 import 了它，第 3 条会红。
BLACKLIST: frozenset[str] = frozenset()

_MANIFEST = re.compile(r"\$programFiles\s*=\s*@\((.*?)\)", re.S)
_QUOTED = re.compile(r"'([^']+)'")


def manifest() -> list[str]:
    """`install.ps1` 里 `$programFiles` 列出的白名单（源码部署的唯一清单）。"""
    text = (REPO / "install.ps1").read_text(encoding="utf-8")
    match = _MANIFEST.search(text)
    if match is None:
        raise AssertionError("install.ps1 里找不到 $programFiles 清单")
    return _QUOTED.findall(match.group(1))


def tracked_root_modules() -> set[str]:
    """git 追踪的根级 `.py` 文件名（不含 tests/、tools/、mockups/ 这些子目录）。"""
    try:
        proc = subprocess.run(
            ["git", "ls-files", "*.py"], cwd=REPO,
            capture_output=True, text=True, encoding="utf-8", check=True,
        )
    except (OSError, subprocess.CalledProcessError):  # 没有 git：退回文件系统
        return {path.name for path in REPO.glob("*.py")}
    return {
        line.strip()
        for line in proc.stdout.splitlines()
        if line.strip() and "/" not in line and "\\" not in line
    }


def _imported_roots(path: Path) -> set[str]:
    """一个文件里 import 的顶层名字（`import a.b` 与 `from a import x` 都算 a）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


def reachable_modules(entries: tuple[str, ...] = ENTRIES) -> set[str]:
    """从入口出发、沿「同目录 .py」走得到的本地模块（不含入口自身）。"""
    seen: set[str] = set()
    queue = list(entries)
    while queue:
        current = queue.pop()
        path = REPO / current
        if not path.is_file():
            continue
        for name in _imported_roots(path):
            module = f"{name}.py"
            if module in seen or module == current:
                continue
            if (REPO / module).is_file():
                seen.add(module)
                queue.append(module)
    return seen


class DeployManifestTest(unittest.TestCase):
    def test_directory_and_manifest_agree(self) -> None:
        """目录 − 黑名单 == 白名单；两边的差集都要报出来。"""
        expected = tracked_root_modules() - set(BLACKLIST)
        listed = set(manifest())
        missing = sorted(expected - listed)
        ghost = sorted(listed - expected)
        self.assertEqual(
            missing, [],
            f"这些模块没有进 install.ps1 的 $programFiles，部署后会 ImportError：{missing}",
        )
        self.assertEqual(
            ghost, [],
            f"$programFiles 列了不在根目录（git 追踪）里的文件，install.ps1 会当场 Fail：{ghost}",
        )

    def test_manifest_never_names_untracked_files(self) -> None:
        ghost = sorted(set(manifest()) - tracked_root_modules())
        self.assertEqual(ghost, [], f"$programFiles 里有不存在的文件：{ghost}")

    def test_blacklist_cannot_hide_an_imported_module(self) -> None:
        """黑名单只许写「没人 import 的」；否则部署会缺依赖而测试却是绿的。"""
        hidden = sorted(reachable_modules() & set(BLACKLIST))
        self.assertEqual(hidden, [], f"入口会 import 这些模块，它们不该在黑名单里：{hidden}")

    def test_both_entry_points_are_deployed(self) -> None:
        listed = manifest()
        for entry in ENTRIES:
            self.assertIn(entry, listed, f"{entry} 是入口，必须在清单里")


if __name__ == "__main__":
    sys.exit(unittest.main())
