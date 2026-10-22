#!/usr/bin/env python3
"""QQ 官方 bot 推送器 —— 零第三方依赖，直接调用 HTTP API。

凭据来源（按优先级）：
  1. 环境变量 QQBOT_APPID / QQBOT_SECRET
  2. <root>/.env            （root 默认是当前目录；watch.py 传进来的是数据目录）
  3. %DSH_HOME%/.env 或 ~/.dsh/.env   （旧位置，兼容用，下个版本删除）

接收方 openid 属于配置，不写死在代码里：环境变量 QQBOT_OPENID 或 --openid。

用法：
    python qq_notify.py "消息文本"
    python qq_notify.py --file report.txt
    python qq_notify.py --openid <OPENID> "消息文本"
    type report.txt | python qq_notify.py --stdin

退出码：0 成功；1 发送失败；2 参数/凭据错误。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

TOKEN_URL = "https://api.bot.qq.com/app/getAppAccessToken"
API_BASE = os.environ.get("QQBOT_API_BASE", "https://api.bot.qq.com").rstrip("/")

TEXT_LIMIT = 4500
TIMEOUT = 20


def env_file_candidates(root: Path | None = None) -> list[Path]:
    """按优先级列出可能的 .env：显式指定 > root > 旧 DSH 位置。"""
    files: list[Path] = []
    explicit = os.environ.get("QQBOT_ENV_FILE", "")
    if explicit:
        files.append(Path(explicit))
    files.append((Path(root) if root is not None else Path.cwd()) / ".env")
    legacy_home = os.environ.get("DSH_HOME", "")
    files.append((Path(legacy_home) if legacy_home else Path.home() / ".dsh") / ".env")
    return files


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return values
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, raw = line.partition("=")
        values[key.strip()] = raw.strip().strip('"').strip("'")
    return values


def load_credentials(root: Path | None = None) -> tuple[str, str]:
    app_id = os.environ.get("QQBOT_APPID", "")
    secret = os.environ.get("QQBOT_SECRET", "")
    if not app_id or not secret:
        for path in env_file_candidates(root):
            values = _read_env_file(path)
            app_id = app_id or values.get("QQBOT_APPID", "")
            secret = secret or values.get("QQBOT_SECRET", "")
            if app_id and secret:
                break
    if not app_id or not secret:
        tried = ", ".join(str(p) for p in env_file_candidates(root))
        raise SystemExit(f"缺少凭据：QQBOT_APPID / QQBOT_SECRET 既不在环境变量，也不在 {tried}")
    return app_id, secret


def _post(url: str, payload: dict, headers: dict[str, str] | None = None) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=body, method="POST")
    request.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise RuntimeError(f"HTTP {exc.code} {url}: {detail}") from exc


def get_access_token(app_id: str, secret: str) -> str:
    """取 access_token。官方保证有效期内重复获取返回同一个值，不会踢掉别人的 token。"""
    data = _post(TOKEN_URL, {"appId": app_id, "clientSecret": secret})
    token = data.get("access_token")
    if not token:
        raise RuntimeError(f"获取 access_token 失败: {data}")
    return str(token)


def send_text(openid: str, content: str, token: str) -> dict:
    """主动消息（不带 msg_id）。"""
    url = f"{API_BASE}/v2/users/{openid}/messages"
    data = _post(
        url,
        {"content": content, "msg_type": 0},
        {"Authorization": f"QQBot {token}"},
    )
    if not data.get("id"):
        raise RuntimeError(f"发送失败: {data}")
    return data


def chunk(text: str, limit: int = TEXT_LIMIT) -> list[str]:
    if len(text) <= limit:
        return [text]
    return [text[i : i + limit] for i in range(0, len(text), limit)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="通过 QQ 机器人官方 API 推送消息")
    parser.add_argument("text", nargs="*", help="消息文本")
    parser.add_argument("--openid", default=os.environ.get("QQBOT_OPENID", ""), help="接收方 openid")
    parser.add_argument("--file", help="从文件读取消息内容")
    parser.add_argument("--stdin", action="store_true", help="从标准输入读取消息内容")
    args = parser.parse_args(argv)

    if (args.file or args.stdin) and args.text:
        print("参数冲突：文本参数与 --file/--stdin 只能选一种", file=sys.stderr)
        return 2

    if args.file:
        text = Path(args.file).read_text(encoding="utf-8", errors="replace")
    elif args.stdin:
        text = sys.stdin.read()
    else:
        text = " ".join(args.text)

    text = text.strip()
    if not text:
        print("消息为空", file=sys.stderr)
        return 2
    if not args.openid:
        print("缺少接收方 openid：用 --openid 或环境变量 QQBOT_OPENID 指定", file=sys.stderr)
        return 2

    app_id, secret = load_credentials()
    token = get_access_token(app_id, secret)
    for part in chunk(text):
        result = send_text(args.openid, part, token)
        print(f"sent id={result['id']} ts={result.get('timestamp', '')}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
