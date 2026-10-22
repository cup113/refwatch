"""测试脚手架：本地 bare 仓当 remote，全程不联网。

`REFWATCH_TEST_TMP` 可以把临时目录指到别处（受限环境里系统临时目录可能不可写）。
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

GIT = shutil.which("git")
SLUG = "acme/widget"


def git(args: list[str], cwd: Path | None = None) -> str:
    proc = subprocess.run(
        [GIT or "git", *args],
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} -> {proc.returncode}: {proc.stderr.strip()[:400]}")
    return proc.stdout


def temp_root() -> Path:
    """建一个唯一的临时目录。

    用 os.makedirs + uuid 而不是 tempfile.mkdtemp：在某些 Windows 受限执行环境
    （ACL 受限令牌）里，mkdtemp 建的目录会丢掉可继承的写权限，子进程（git）一
    mkdir 就 Permission denied —— 报错点跟根因完全无关，排查很贵。
    `REFWATCH_TEST_TMP` 可以把临时目录指到别处。
    """
    base = os.environ.get("REFWATCH_TEST_TMP") or tempfile.gettempdir()
    path = Path(base) / f"refwatch-{uuid.uuid4().hex[:10]}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def rmtree(path: Path) -> None:
    """删目录树。git 的对象文件是只读的，Windows 上直接 rmtree 会 PermissionError。"""

    def retry(func, target, _exc):  # noqa: ANN001 - onerror/onexc 的参数形状相同
        try:
            os.chmod(target, stat.S_IWRITE)
        except OSError:
            pass
        func(target)

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=retry)
    else:
        shutil.rmtree(path, onerror=retry)  # type: ignore[call-arg]


class Remote:
    """一个本地 bare 仓 + 一个工作克隆，模拟 GitHub 上的 owner/name。"""

    def __init__(self, base: Path) -> None:
        self.bare = base / "origin.git"
        self.work = base / "work"
        git(["init", "--bare", "-q", str(self.bare)])
        git(["init", "-q", str(self.work)])
        git(["config", "user.name", "refwatch-test"], cwd=self.work)
        git(["config", "user.email", "test@example.invalid"], cwd=self.work)
        git(["config", "commit.gpgsign", "false"], cwd=self.work)

    def commit(self, message: str, filename: str = "f.txt", content: str | None = None) -> str:
        (self.work / filename).write_text(content if content is not None else f"{message}\n", encoding="utf-8")
        git(["add", "-A"], cwd=self.work)
        git(["commit", "-q", "-m", message], cwd=self.work)
        return git(["rev-parse", "HEAD"], cwd=self.work).strip()

    def branch(self, name: str) -> None:
        git(["branch", name], cwd=self.work)

    def tag(self, name: str, force: bool = False) -> None:
        args = ["tag", name] if not force else ["tag", "-f", name]
        git(args, cwd=self.work)

    def reset_to(self, sha: str) -> None:
        git(["reset", "--hard", "-q", sha], cwd=self.work)

    def push(self, *refspecs: str, force: bool = False) -> None:
        args = ["push", "-q", str(self.bare), *(refspecs or ("--all",))]
        if force:
            args.append("--force")
        git(args, cwd=self.work)

    def delete_remote_branch(self, name: str) -> None:
        git(["push", "-q", str(self.bare), "--delete", name], cwd=self.work)
