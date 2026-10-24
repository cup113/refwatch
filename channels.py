#!/usr/bin/env python3
"""推送通道 —— 「除 QQ 外还允许哪些渠道」的唯一真相（ADR-0008）。

每个通道实现成「准备 + 发送」两段：

  * `prepare(cfg, root) -> Sender`：校验接收方/凭据、做一次性准备（QQ 取 access_token、
    钉钉/飞书构造签名）。缺配置抛 `ChannelConfigError`（→ RoundReport 的
    `no-recipient`），准备阶段失败抛 `ChannelSetupError`（→ `setup-failed`）。
  * `Sender(text)`：把**一段**文本发出去。发送失败抛 RuntimeError（→ `send_failed`，
    同 QQ 的既有行为）。文本先由 run_round 按 `chunk_limit` 切过，适配器只对自家
    硬上限做**通道内再分片**（wecom 的 2048 字节）。

凭据全走 .env（环境变量优先，见 `CREDENTIALS`）；接收方标识属于 config.json
（目前只有 qq 的 openid）。错误串一律英文中性诊断文本 —— 人话前缀由 watch 的
捕获点按当前语言加（issue #2 的分工）。

冒烟用法（各通道独立发一条，便于配好后验证）：
    python channels.py --channel wecom "测试"
    python channels.py --channel qq --openid <OPENID> "测试"

退出码：0 成功；1 发送/准备失败；2 参数/配置错误。
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import os
import sys
import time
import urllib.parse
from collections.abc import Callable
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import qq_notify  # noqa: E402

Sender = Callable[[str], None]

# 通道代码，顺序即设置面板下拉框的顺序；"qq" 是缺省，行为与旧版完全一致。
CHANNELS: tuple[str, ...] = ("qq", "wecom", "dingtalk", "feishu", "serverchan", "bark")

# 每个通道的凭据字段：env = .env 里的键名，label = i18n 表的键，
# masked = 面板里是否密文显示。openid 不在这里 —— 它是 config 键，不是凭据。
CREDENTIALS: dict[str, tuple[dict[str, object], ...]] = {
    "qq": (
        {"env": "QQBOT_APPID", "label": "panel.field_appid", "masked": False},
        {"env": "QQBOT_SECRET", "label": "panel.field_secret", "masked": True},
    ),
    "wecom": (
        {"env": "WECOM_WEBHOOK", "label": "panel.field_wecom_webhook", "masked": False},
    ),
    "dingtalk": (
        {"env": "DINGTALK_WEBHOOK", "label": "panel.field_dingtalk_webhook", "masked": False},
        {"env": "DINGTALK_SECRET", "label": "panel.field_dingtalk_secret", "masked": True},
    ),
    "feishu": (
        {"env": "FEISHU_WEBHOOK", "label": "panel.field_feishu_webhook", "masked": False},
        {"env": "FEISHU_SECRET", "label": "panel.field_feishu_secret", "masked": True},
    ),
    "serverchan": (
        {"env": "SERVERCHAN_SENDKEY", "label": "panel.field_serverchan_sendkey", "masked": False},
    ),
    "bark": (
        {"env": "BARK_KEY", "label": "panel.field_bark_key", "masked": False},
        {"env": "BARK_SERVER", "label": "panel.field_bark_server", "masked": False},
    ),
}

# 各通道官方文本上限（通道内再分片的依据）。qq 的 4500 与 run_round 的
# chunk_limit 缺省一致，列在这里只为表完整。
TEXT_LIMITS: dict[str, int] = {
    "qq": qq_notify.TEXT_LIMIT,
    "wecom": 2000,  # 官方 content ≤ 2048 **字节**（UTF-8，约 680 汉字），留 JSON 转义余量
    "dingtalk": 4500,
    "feishu": 4500,  # 官方 text 上限 30KB 量级，chunk_limit 已远在其下
    "serverchan": 4500,  # desp 上限 32KB；真正紧的是免费额度 5 条/天
    "bark": 4500,
}

SERVERCHAN_API = "https://sctapi.ftqq.com"
BARK_DEFAULT_SERVER = "https://api.day.app"


class ChannelConfigError(RuntimeError):
    """接收方/凭据没配好（缺 openid、webhook、SendKey…）→ status=no-recipient。"""


class ChannelSetupError(RuntimeError):
    """发送前准备失败（取 token、构造签名…）→ status=setup-failed。"""


def normalize_channel(value: object) -> str:
    """channel 配置值兜底：非法/缺省回到 qq —— 与 language 的策略同构。"""
    text = str(value if value is not None else "").strip().lower()
    return text if text in CHANNELS else "qq"


def env_keys_of(channel: str) -> tuple[str, ...]:
    """通道的 .env 键，按声明顺序（提示文案、落盘、读取都用它）。"""
    return tuple(str(spec["env"]) for spec in CREDENTIALS.get(channel, ()))


# ---------------------------------------------------------------- 工具


def _credentials(channel: str, root: Path | None) -> dict[str, str]:
    """读一个通道的 .env 凭据（环境变量优先）。"""
    return qq_notify.read_env_values(env_keys_of(channel), root)


def _require(values: dict[str, str], key: str, hint: str) -> str:
    value = (values.get(key) or "").strip()
    if not value:
        raise ChannelConfigError(f"{key} not set in environment variables or {hint}")
    return value


def split_by_bytes(text: str, limit: int) -> list[str]:
    """按 UTF-8 字节数贪心切块（wecom 专用：2048 字节上限，按字符切对 CJK 会超）。

    永不拆半个字符（按字符迭代天然保证）；join 回去与原文逐字相同。
    """
    if len(text.encode("utf-8")) <= limit:
        return [text]
    parts: list[str] = []
    buf: list[str] = []
    size = 0
    for ch in text:
        width = len(ch.encode("utf-8"))
        if size + width > limit and buf:
            parts.append("".join(buf))
            buf, size = [], 0
        buf.append(ch)
        size += width
    if buf:
        parts.append("".join(buf))
    return parts


def _title_body(text: str, title_limit: int) -> tuple[str, str]:
    """serverchan/bark 的标题拆分：首行当标题（截断），全文当正文。"""
    first, _, _rest = text.partition("\n")
    title = first.strip()[:title_limit] or "refwatch"
    return title, text


# ---------------------------------------------------------------- 适配器


def _prepare_qq(cfg: dict, root: Path | None) -> Sender:
    openid = str(cfg.get("openid") or "").strip()
    if not openid:
        raise ChannelConfigError("no openid in config (or env QQBOT_OPENID)")
    values = _credentials("qq", root)
    app_id = _require(values, "QQBOT_APPID", ".env")
    secret = _require(values, "QQBOT_SECRET", ".env")
    try:
        token = qq_notify.get_access_token(app_id, secret)
    except RuntimeError as exc:
        raise ChannelSetupError(str(exc)) from exc

    def send(text: str) -> None:
        qq_notify.send_text(openid, text, token)

    return send


def _prepare_wecom(cfg: dict, root: Path | None) -> Sender:  # noqa: ARG001 - 通道无 config 键
    url = _require(_credentials("wecom", root), "WECOM_WEBHOOK", ".env")

    def send(text: str) -> None:
        for part in split_by_bytes(text, TEXT_LIMITS["wecom"]):
            data = qq_notify.post_json(url, {"msgtype": "text", "text": {"content": part}}, hide_query_in_errors=True)
            if data.get("errcode") != 0:
                raise RuntimeError(f"wecom send failed: {data}")

    return send


def _dingtalk_signed_url(webhook: str, secret: str, now_ms: int | None = None) -> str:
    """官方加签：把 `{timestamp}\\n{secret}` 同时当 HMAC key 与消息，base64 后 urlencode。"""
    if not secret:
        return webhook
    ts = now_ms if now_ms is not None else int(time.time() * 1000)
    string_to_sign = f"{ts}\n{secret}"
    digest = hmac.new(string_to_sign.encode("utf-8"), string_to_sign.encode("utf-8"), hashlib.sha256).digest()
    sign = urllib.parse.quote_plus(base64.b64encode(digest).decode("utf-8"))
    sep = "&" if "?" in webhook else "?"
    return f"{webhook}{sep}timestamp={ts}&sign={sign}"


def _prepare_dingtalk(cfg: dict, root: Path | None) -> Sender:  # noqa: ARG001 - 通道无 config 键
    values = _credentials("dingtalk", root)
    webhook = _require(values, "DINGTALK_WEBHOOK", ".env")
    secret = (values.get("DINGTALK_SECRET") or "").strip()
    try:
        url = _dingtalk_signed_url(webhook, secret)
    except Exception as exc:  # noqa: BLE001 - 签名构造失败归为准备失败
        raise ChannelSetupError(f"dingtalk sign failed: {exc}") from exc

    def send(text: str) -> None:
        for part in qq_notify.chunk(text, TEXT_LIMITS["dingtalk"]):
            data = qq_notify.post_json(url, {"msgtype": "text", "text": {"content": part}}, hide_query_in_errors=True)
            if data.get("errcode") != 0:
                raise RuntimeError(f"dingtalk send failed: {data}")

    return send


def _feishu_sign(secret: str, ts_sec: int) -> str:
    """官方签名：`{timestamp}\\n{secret}` 整串当 HMAC key，对空消息做 HMAC-SHA256 再 base64。"""
    key = f"{ts_sec}\n{secret}".encode("utf-8")
    return base64.b64encode(hmac.new(key, b"", hashlib.sha256).digest()).decode("utf-8")


def _prepare_feishu(cfg: dict, root: Path | None) -> Sender:  # noqa: ARG001 - 通道无 config 键
    values = _credentials("feishu", root)
    webhook = _require(values, "FEISHU_WEBHOOK", ".env")
    secret = (values.get("FEISHU_SECRET") or "").strip()

    def send(text: str) -> None:
        payload: dict = {"msg_type": "text", "content": {"text": text}}
        if secret:
            try:
                ts = int(time.time())
                payload["timestamp"] = str(ts)
                payload["sign"] = _feishu_sign(secret, ts)
            except Exception as exc:  # noqa: BLE001 - 签名构造失败归为准备失败
                raise ChannelSetupError(f"feishu sign failed: {exc}") from exc
        data = qq_notify.post_json(webhook, payload, hide_query_in_errors=True)
        code = data.get("code", data.get("StatusCode"))  # 新旧两代返回体都以 0 为成功
        if code != 0:
            raise RuntimeError(f"feishu send failed: {data}")

    return send


def _prepare_serverchan(cfg: dict, root: Path | None) -> Sender:  # noqa: ARG001 - 通道无 config 键
    key = _require(_credentials("serverchan", root), "SERVERCHAN_SENDKEY", ".env")
    url = f"{SERVERCHAN_API}/{key}.send"

    def send(text: str) -> None:
        title, _ = _title_body(text, 32)  # desp（正文）给全文，标题只是通知栏那一句
        data = qq_notify.post_json(url, form={"title": title, "desp": text}, hide_query_in_errors=True)
        if data.get("code") != 0:
            raise RuntimeError(f"serverchan send failed: {data}")

    return send


def _prepare_bark(cfg: dict, root: Path | None) -> Sender:  # noqa: ARG001 - 通道无 config 键
    values = _credentials("bark", root)
    key = _require(values, "BARK_KEY", ".env")
    server = (values.get("BARK_SERVER") or "").strip() or BARK_DEFAULT_SERVER
    url = f"{server.rstrip('/')}/push"

    def send(text: str) -> None:
        title, body = _title_body(text, 64)
        data = qq_notify.post_json(url, {"title": title, "body": body, "device_key": key})
        if data.get("code") != 200:
            raise RuntimeError(f"bark send failed: {data}")

    return send


_PREPARERS: dict[str, Callable[[dict, Path | None], Sender]] = {
    "qq": _prepare_qq,
    "wecom": _prepare_wecom,
    "dingtalk": _prepare_dingtalk,
    "feishu": _prepare_feishu,
    "serverchan": _prepare_serverchan,
    "bark": _prepare_bark,
}


def prepare(cfg: dict, root: Path | None = None) -> Sender:
    """按 cfg["channel"] 选通道并准备发送器。这是推送接缝在多通道时代的形状。"""
    code = normalize_channel(cfg.get("channel"))
    return _PREPARERS[code](cfg, root)


# ---------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="按已支持的通道推送一条消息（冒烟用）")
    parser.add_argument("text", nargs="*", help="消息文本")
    parser.add_argument("--channel", default="qq", help=f"通道：{' / '.join(CHANNELS)}")
    parser.add_argument("--root", default=".", help="数据目录（读 .env），默认当前目录")
    parser.add_argument("--openid", default=os.environ.get("QQBOT_OPENID", ""), help="qq 通道的接收方 openid")
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
    if args.channel not in CHANNELS:
        print(f"未知通道 {args.channel}（可选：{' / '.join(CHANNELS)}）", file=sys.stderr)
        return 2

    cfg = {"channel": args.channel, "openid": args.openid}
    try:
        send = prepare(cfg, Path(args.root).resolve())
    except ChannelConfigError as exc:
        print(f"配置缺失: {exc}", file=sys.stderr)
        return 2
    except ChannelSetupError as exc:
        print(f"准备失败: {exc}", file=sys.stderr)
        return 1
    for part in qq_notify.chunk(text):
        send(part)
        print(f"sent via {args.channel}: {len(part)} chars")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
