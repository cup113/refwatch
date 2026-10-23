#!/usr/bin/env python3
"""config.json / .env / 开机自启的读写 —— 设置面板的落盘层。

分工刻意这样切：
  * **读**只有一份真相 —— `watch.load_config()`；这里只负责决定「读哪个文件」。
  * **写**在这里：合并写（未知键绝不丢）、原子写、`.env` 只动两行。
  * 不 import 任何 GUI 库，所以整套逻辑可以在没有 tkinter/pystray 的环境里测。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
# 与 watch.py 同样的发布形态：模块平铺在程序目录里。
sys.path.insert(0, str(HERE))

import qq_notify  # noqa: E402
import watch  # noqa: E402
from i18n import normalize_language, t  # noqa: E402

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "refwatch"
ENV_KEYS = ("QQBOT_APPID", "QQBOT_SECRET")

# GitHub 的 owner/repo 只允许这几类字符（不含中文，别让 \w 蒙混过关）。
REPO_PATTERN = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")

# 从别处粘贴进来时会带上的前缀，按长到短匹配。
_REPO_PREFIXES = (
    "https://github.com/",
    "http://github.com/",
    "https://www.github.com/",
    "git@github.com:",
    "ssh://git@github.com/",
    "github.com/",
)


# ---------------------------------------------------------------- 位置


def default_root() -> Path:
    """数据目录：与 install.ps1 的默认部署位置一致。"""
    local = os.environ.get("LOCALAPPDATA", "")
    if os.name == "nt" and local:
        return Path(local) / "refwatch"
    return Path.home() / ".refwatch"


def app_dir() -> Path:
    """程序自身所在目录。

    Nuitka 编译后 `sys.executable` 可能指向解释器而不是 exe，所以用 `argv[0]`。
    """
    return Path(sys.argv[0]).resolve().parent


def is_compiled() -> bool:
    """是不是打包后的可执行文件（Nuitka 注入 `__compiled__`）。"""
    if globals().get("__compiled__"):
        return True
    return bool(getattr(sys, "frozen", False))  # PyInstaller 的标记，留作兼容


def resolve_config_path(root: Path, config_path: Path | None = None) -> Path:
    """配置文件落点：显式指定 > <root>/config.json > <root>/watch.json(旧名) > 脚本目录 > <root>。

    托盘应用的数据目录通常不是程序目录，所以不能直接用 watch.default_config_path()。
    """
    if config_path is not None:
        return Path(config_path)
    root = Path(root)
    for candidate in (root / "config.json", root / "watch.json"):
        if candidate.is_file():
            return candidate
    legacy = watch.default_config_path()
    if legacy.is_file():
        return legacy
    return root / "config.json"


def load(root: Path, config_path: Path | None = None) -> dict:
    """读配置（默认值 + 磁盘内容 + 环境变量回填），与 CLI 走同一条路径。"""
    return watch.load_config(Path(root), resolve_config_path(Path(root), config_path))


def env_path(root: Path) -> Path:
    return Path(root) / ".env"


# ---------------------------------------------------------------- config.json


def read_raw(path: Path) -> dict:
    """磁盘上的原样内容（可能残缺）。坏 JSON 抛 ValueError，由调用方决定怎么处置。"""
    path = Path(path)
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        # 英文中性诊断串（同 qq_notify 的约定）：它只出现在异常/日志里，人话前缀由调用方给。
        raise ValueError(f"{path} is not a JSON object")
    return data


def backup(path: Path) -> Path | None:
    """把当前文件另存一份（时间戳后缀）。没有原文件返回 None。"""
    path = Path(path)
    if not path.is_file():
        return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = path.with_name(f"{path.name}.bak.{stamp}")
    shutil.copy2(path, target)
    return target


def save_config(path: Path, values: dict) -> Path:
    """合并写：只覆盖 values 里的键，文件里其它键（包括界面没展示的）原样保留。

    原文件不是合法 JSON 时先备份再写 —— 不静默丢掉用户手写的内容。
    原子替换（tmp + os.replace），避免计划任务/托盘读到半个文件。
    """
    path = Path(path)
    raw: dict = {}
    if path.is_file():
        try:
            raw = read_raw(path)
        except (OSError, ValueError):
            backup(path)
            raw = {}
    for key, value in values.items():
        if key.startswith("_"):  # _root / _config_path 之类的运行时字段不落盘
            continue
        raw[key] = value
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


# ---------------------------------------------------------------- 校验


def normalize_repo(text: str) -> str:
    """把 `https://github.com/a/b.git` 之类的输入压成 `a/b`。"""
    value = text.strip()
    if not value:
        return ""
    lowered = value.lower()
    for prefix in _REPO_PREFIXES:
        if lowered.startswith(prefix):
            value = value[len(prefix) :]
            break
    value = value.strip().strip("/")
    if value.lower().endswith(".git"):
        value = value[: -len(".git")]
    return value.strip("/")


def normalize_repos(values: object) -> list[str]:
    """规范化 + 保序去重（GitHub 的 slug 大小写不敏感，所以按小写判重）。"""
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, (list, tuple)):
        return []
    seen: set[str] = set()
    out: list[str] = []
    for item in values:
        slug = normalize_repo(str(item))
        if not slug:
            continue
        key = slug.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(slug)
    return out


def _as_int(values: dict, key: str, low: int, high: int, errors: list[str], lang: str) -> None:
    if key not in values:  # 缺省 = 沿用默认值，不是错误
        return
    raw = values.get(key)
    try:
        number = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        errors.append(t(lang, "validate.int", key=key))
        return
    if not low <= number <= high:
        errors.append(t(lang, "validate.int_range", key=key, low=low, high=high, value=number))
    else:
        values[key] = number


def validate(values: dict, lang: str = "zh") -> list[str]:
    """返回错误清单（按 lang 给文案）；空清单表示可以落盘。顺手把能规范化的值就地改好。

    只检查「传进来的键」：缺省的键沿用 DEFAULT_CONFIG，不该因此报错。
    lang 由调用方给：面板传表单里新选的语言 —— 正在切语言的人用目标语言看报错。
    """
    lang = normalize_language(lang)
    errors: list[str] = []

    if "repos" in values:
        repos = normalize_repos(values.get("repos", []))
        for slug in repos:
            if not REPO_PATTERN.match(slug):
                errors.append(t(lang, "validate.bad_slug", slug=slug))
        values["repos"] = repos

    if "exclude_ref_patterns" in values:
        patterns = values.get("exclude_ref_patterns", [])
        if isinstance(patterns, str):
            patterns = patterns.splitlines()
        if not isinstance(patterns, (list, tuple)):
            errors.append(t(lang, "validate.patterns_type"))
            patterns = []
        values["exclude_ref_patterns"] = [str(item).strip() for item in patterns if str(item).strip()]

    _as_int(values, "interval_minutes", 1, 1440, errors, lang)
    _as_int(values, "max_commits_per_ref", 1, 1000, errors, lang)
    _as_int(values, "max_chunks_per_repo", 1, 100, errors, lang)
    _as_int(values, "chunk_limit", 1, 100000, errors, lang)
    _as_int(values, "fetch_timeout_sec", 1, 86400, errors, lang)
    _as_int(values, "fail_alert_threshold", 1, 1000, errors, lang)
    _as_int(values, "lock_stale_sec", 1, 86400, errors, lang)

    if "git_ssl_backend" in values:
        backend = str(values.get("git_ssl_backend", "") or "")
        if backend not in ("", "openssl", "schannel"):
            errors.append(t(lang, "validate.backend"))
        else:
            values["git_ssl_backend"] = backend

    if "language" in values:
        language = values.get("language")
        if not isinstance(language, str) or language not in ("zh", "en"):
            errors.append(t(lang, "validate.language"))
        else:
            values["language"] = language

    for key in ("notify", "autostart", "task_prompt_done"):
        if key in values and not isinstance(values[key], bool):
            errors.append(t(lang, "validate.bool", key=key))

    if "openid" in values:
        openid = values.get("openid", "")
        if not isinstance(openid, str):
            errors.append(t(lang, "validate.openid_type"))
        else:
            values["openid"] = openid.strip()

    for key in ENV_KEYS:
        if key in values:  # 常见误操作：把密钥写进 config.json
            errors.append(t(lang, "validate.secret_conflict", key=key))
    return errors


# ---------------------------------------------------------------- .env


def save_secrets(root: Path, appid: str, secret: str) -> Path:
    """只改写 QQBOT_APPID / QQBOT_SECRET 两行，其余行、注释、顺序、行尾风格全部保留。"""
    path = env_path(root)
    raw = path.read_bytes() if path.is_file() else b""
    text = raw.decode("utf-8-sig", errors="replace")
    eol = "\r\n" if "\r\n" in text else "\n"
    wanted = dict(zip(ENV_KEYS, (appid.strip(), secret.strip())))
    kept: list[str] = []
    seen: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            kept.append(line)
            continue
        key = stripped.partition("=")[0].strip()
        if key not in wanted:
            kept.append(line)
            continue
        if key in seen:  # 重复键只留第一处，避免 .env 里出现两次赋值
            continue
        seen.add(key)
        kept.append(f"{key}={wanted[key]}")
    for key, value in wanted.items():
        if key not in seen:
            kept.append(f"{key}={value}")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes((eol.join(kept) + eol).encode("utf-8"))
    os.replace(tmp, path)
    return path


def read_secrets(root: Path) -> dict:
    """凭据现在从哪读来的（环境变量 / 某个 .env），供面板显示。"""
    return qq_notify.read_credentials(Path(root))


# ---------------------------------------------------------------- 开机自启


def registered_autostart() -> str | None:
    """HKCU\\...\\Run 里 refwatch 的值；没有（或非 Windows）返回 None。"""
    try:
        import winreg
    except ImportError:  # pragma: no cover - 非 Windows
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _kind = winreg.QueryValueEx(key, RUN_VALUE)
    except OSError:
        return None
    return str(value)


def set_autostart(enabled: bool, command: str) -> bool:
    """把 Run 值校正到 enabled。返回是否真的动了注册表（幂等，便于每次启动对账）。"""
    try:
        import winreg
    except ImportError:  # pragma: no cover - 非 Windows
        return False
    current = registered_autostart()
    if enabled and current == command:
        return False
    if not enabled and current is None:
        return False
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, command)
        else:
            try:
                winreg.DeleteValue(key, RUN_VALUE)
            except FileNotFoundError:
                pass
    return True


def self_command() -> str:
    """自启要写的命令行：编译版指向 exe，源码版指向 pythonw.exe + tray.py。"""
    if is_compiled():
        return f'"{Path(sys.argv[0]).resolve()}" --tray'
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    launcher = pythonw if pythonw.is_file() else Path(sys.executable)
    return f'"{launcher}" -X utf8 "{app_dir() / "tray.py"}"'


def sync_autostart(autostart: bool, command: str | None = None) -> bool:
    """按配置把注册表校正到 `autostart`（配置是真相，注册表是它的投影）。"""
    return set_autostart(bool(autostart), command or self_command())
