#!/usr/bin/env python3
"""refwatch —— 定时 fetch GitHub 仓库的所有分支/tag，用 SHA 状态机找出新增提交，推到 QQ。

设计要点（详见 README.md / CONTEXT.md / docs/adr）：
  * 只 fetch，不 pull：镜像仓没有工作区，永远不会因为冲突/脏树失败。
  * 状态是 ref -> SHA，不是 diff：能区分「正常提交」/「历史被改写」/「分支新增或删除」。
  * 首次运行静默建基线，不会把全量历史推给你。
  * 只跟踪 refs/heads/* 和 refs/tags/*，**刻意排除 refs/pull/***
    （GitHub 会把每个 PR 都作为 ref 广播，量大的仓库有几百个，跟踪它等于自找刷屏）。
  * 推送走 qq_notify.py：摘要 + compare 链接，不发 diff 本体。
  * 计划任务用 pythonw.exe 跑，子进程一律 CREATE_NO_WINDOW（见 git()）。

用法：
    python watch.py                      # 正常一轮（读 config.json，root = 脚本目录）
    python watch.py --root <dir>         # 指定数据目录（镜像仓/状态/日志）
    python watch.py --dry-run            # 只打印将要发送的消息，不发送、不推进状态
    python watch.py --repo owner/name    # 只检查指定仓库
    python watch.py --config <path>      # 指定配置文件

本文件既是引擎也是 CLI 入口：一轮的全部逻辑在 run_round()，main() 只负责解析参数并把
RoundReport 翻成退出码。托盘应用（tray.py）用同一份 run_round()，所以「一轮」的定义只有一处。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from fnmatch import fnmatch
from pathlib import Path
from typing import Callable

HERE = Path(__file__).resolve().parent
# 推送器与监视器同目录（发布形态就是两个平铺文件）。
sys.path.insert(0, str(HERE))

import channels  # noqa: E402
import i18n  # noqa: E402
from qq_notify import chunk  # noqa: E402

# pythonw.exe 没有控制台，而它 spawn 的 git.exe 是控制台子系统程序：没有这个标志，
# Windows 会给每个 git 子进程新分配一个控制台窗口 —— 也就是计划任务每 30 分钟闪一下
# 的那个黑窗口。改这里之前先读 docs/adr/0003-windowless-scheduled-run.md。
_WINDOWS_SPAWN_KW: dict = (
    {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
)

GIT_ENV = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
GIT_ENV.pop("GIT_ASKPASS", None)

# 永远不跟踪：PR ref 是平台广播的瞬时引用，不是作者的工作分支。
ALWAYS_EXCLUDE = ("refs/pull/*", "refs/merge-requests/*")

# config.json 是现在的名字；watch.json 是旧名字，读到时记一条提醒（下一版删）。
CONFIG_NAMES = ("config.json", "watch.json")

DEFAULT_CONFIG: dict = {
    "repos": [],
    "exclude_ref_patterns": ["refs/heads/dependabot/*", "refs/heads/renovate/*"],
    "max_commits_per_ref": 15,
    "max_chunks_per_repo": 2,
    "chunk_limit": 4500,
    "openid": "",
    # 推送目的地（qq / wecom / dingtalk / feishu / serverchan / bark，见 channels.py）；
    # 非法值回退 qq（同 language 的策略）。
    "channel": "qq",
    "notify": True,
    "fetch_timeout_sec": 300,
    "fail_alert_threshold": 3,
    "lock_stale_sec": 1800,
    "git_ssl_backend": "openssl",
    # 全部运行时文案（摘要/日志/托盘/面板）的语言；非法值回退 zh（见 i18n.py）。
    "language": "zh",
    # 下面三个只对托盘应用有意义；CLI 读同一个 config.json，不理会它们。
    "interval_minutes": 30,
    "autostart": True,
    "task_prompt_done": False,
}


class GitError(RuntimeError):
    pass


# ---------------------------------------------------------------- 基础设施


def now() -> datetime:
    return datetime.now()


def load_config(root: Path, config_path: Path | None) -> dict:
    path = config_path or default_config_path()
    cfg = dict(DEFAULT_CONFIG)
    if path.is_file():
        cfg.update(json.loads(path.read_text(encoding="utf-8")))
    raw_language = cfg.get("language")
    cfg["language"] = i18n.normalize_language(raw_language)
    lang = cfg["language"]
    if path.is_file() and path.name == "watch.json" and not (path.parent / "config.json").is_file():
        log(root, i18n.t(lang, "log.config_legacy_name", name=path.name), "WARN")
    if raw_language != cfg["language"]:
        # 值本身不合法，没有「它的语言」可言：用回退后的 zh 报这条 WARN。
        log(root, i18n.t("zh", "log.language_invalid", value=raw_language), "WARN")
    raw_channel = cfg.get("channel")
    cfg["channel"] = channels.normalize_channel(raw_channel)
    if raw_channel != cfg["channel"]:
        log(root, i18n.t("zh", "log.channel_invalid", value=raw_channel), "WARN")
    cfg["openid"] = str(cfg.get("openid") or os.environ.get("QQBOT_OPENID", ""))
    cfg["_root"] = root
    cfg["_config_path"] = path
    return cfg


def default_config_path() -> Path:
    for name in CONFIG_NAMES:
        candidate = HERE / name
        if candidate.is_file():
            return candidate
    return HERE / CONFIG_NAMES[0]


def say(text: str) -> None:
    """计划任务用 pythonw.exe 跑（没有控制台），此时 stdout 是 None。"""
    if sys.stdout is None:
        return
    try:
        print(text, flush=True)
    except OSError:
        pass


def log(root: Path, message: str, level: str = "INFO") -> None:
    line = f"{now():%Y-%m-%d %H:%M:%S} [{level}] {message}"
    say(line)
    try:
        logdir = root / "logs"
        logdir.mkdir(parents=True, exist_ok=True)
        with (logdir / f"watch-{now():%Y%m%d}.log").open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def _git_opts(cfg: dict | None = None) -> list[str]:
    """Windows 上让 git 走 OpenSSL 后端：部分受限环境里 schannel 取不到凭据
    （SEC_E_NO_CREDENTIALS），换后端即可。非 Windows 不注入；配置置空可关闭。"""
    backend = str((cfg or DEFAULT_CONFIG).get("git_ssl_backend") or "")
    if os.name == "nt" and backend:
        return ["-c", f"http.sslBackend={backend}"]
    return []


def git(
    args: list[str], cwd: Path | None = None, timeout: int = 300, cfg: dict | None = None
) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            ["git", *_git_opts(cfg), *args],
            cwd=str(cwd) if cwd else None,
            env=GIT_ENV,
            capture_output=True,
            timeout=timeout,
            **_WINDOWS_SPAWN_KW,
        )
    except subprocess.TimeoutExpired as exc:
        lang = i18n.normalize_language((cfg or DEFAULT_CONFIG).get("language"))
        raise GitError(i18n.t(lang, "log.git_timeout", args=" ".join(args), timeout=timeout)) from exc
    return (
        proc.returncode,
        proc.stdout.decode("utf-8", "replace"),
        proc.stderr.decode("utf-8", "replace"),
    )


def git_ok(
    args: list[str], cwd: Path | None = None, timeout: int = 300, cfg: dict | None = None
) -> str:
    rc, out, err = git(args, cwd=cwd, timeout=timeout, cfg=cfg)
    if rc != 0:
        raise GitError(f"git {' '.join(args)} -> {rc}: {err.strip()[:400] or out.strip()[:400]}")
    return out


def state_path(root: Path) -> Path:
    return root / "state.json"


def load_state(root: Path) -> dict:
    path = state_path(root)
    if not path.is_file():
        return {"version": 1, "repos": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "repos": {}}
    data.setdefault("version", 1)
    data.setdefault("repos", {})
    return data


def save_state(root: Path, state: dict) -> None:
    path = state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


class Lock:
    """防止上一轮还没跑完、下一轮又起来（计划任务每 30 分钟一次）。"""

    def __init__(self, root: Path, stale_sec: int) -> None:
        self.path = root / ".lock"
        self.stale_sec = stale_sec
        self.acquired = False

    def __enter__(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                age = time.time() - self.path.stat().st_mtime
            except OSError:
                age = 0
            if age < self.stale_sec:
                return False
            self.path.unlink(missing_ok=True)
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                return False
        os.write(fd, f"{os.getpid()} {time.time():.0f}\n".encode())
        os.close(fd)
        self.acquired = True
        return True

    def __exit__(self, *exc: object) -> None:
        # 只删自己拿到的锁：抢锁失败的那一轮不能把正在运行那一轮的锁删掉。
        if self.acquired:
            self.path.unlink(missing_ok=True)


# ---------------------------------------------------------------- git 操作


def mirror_dir(root: Path, slug: str) -> Path:
    return root / "repos" / (slug.replace("/", "__") + ".git")


def ensure_mirror(root: Path, slug: str, cfg: dict | None = None) -> tuple[Path, bool]:
    """返回 (镜像仓路径, 是否新建)。刻意不用 clone --mirror：那会拉 refs/pull/*。"""
    path = mirror_dir(root, slug)
    if (path / "HEAD").is_file():
        return path, False
    path.parent.mkdir(parents=True, exist_ok=True)
    git_ok(["init", "--bare", "--quiet", str(path)], cfg=cfg)
    git_ok(["remote", "add", "origin", f"https://github.com/{slug}.git"], cwd=path, cfg=cfg)
    git_ok(["config", "remote.origin.fetch", "+refs/heads/*:refs/heads/*"], cwd=path, cfg=cfg)
    git_ok(["config", "--add", "remote.origin.fetch", "+refs/tags/*:refs/tags/*"], cwd=path, cfg=cfg)
    git_ok(["config", "remote.origin.tagOpt", "--no-tags"], cwd=path, cfg=cfg)
    return path, True


def fetch_mirror(mirror: Path, timeout: int, cfg: dict | None = None) -> None:
    git_ok(["fetch", "--prune", "--quiet", "origin"], cwd=mirror, timeout=timeout, cfg=cfg)


def list_refs(mirror: Path, patterns: list[str], cfg: dict | None = None) -> dict[str, str]:
    out = git_ok(
        ["for-each-ref", "--format=%(refname)%09%(objectname)%09%(objecttype)", "refs/heads", "refs/tags"],
        cwd=mirror,
        cfg=cfg,
    )
    refs: dict[str, str] = {}
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        name, sha, _kind = parts
        if any(fnmatch(name, pat) for pat in ALWAYS_EXCLUDE):
            continue
        if any(fnmatch(name, pat) for pat in patterns):
            continue
        refs[name] = sha
    return refs


def peel(mirror: Path, ref_or_sha: str, cfg: dict | None = None) -> str | None:
    rc, out, _ = git(["rev-parse", "--verify", "--quiet", f"{ref_or_sha}^{{commit}}"], cwd=mirror, cfg=cfg)
    return out.strip() if rc == 0 else None


def is_ancestor(mirror: Path, older: str, newer: str, cfg: dict | None = None) -> bool:
    rc, _, _ = git(["merge-base", "--is-ancestor", older, newer], cwd=mirror, cfg=cfg)
    return rc == 0


def commit_details(
    mirror: Path, older: str, newer: str, cap: int, cfg: dict | None = None
) -> tuple[list[dict], int]:
    """返回 (最多 cap 条提交明细（时间正序）, 总提交数)。"""
    total = int(git_ok(["rev-list", "--count", f"{older}..{newer}"], cwd=mirror, cfg=cfg).strip() or 0)
    out = git_ok(
        [
            "log",
            f"-n{cap}",
            "--numstat",
            "--pretty=format:%x1e%H%x1f%an%x1f%aI%x1f%P%x1f%s",
            f"{older}..{newer}",
        ],
        cwd=mirror,
        cfg=cfg,
    )
    commits: list[dict] = []
    for record in out.split("\x1e"):
        record = record.strip("\n")
        if not record:
            continue
        header, _, body = record.partition("\n")
        fields = header.split("\x1f")
        if len(fields) < 5:
            continue
        sha, author, iso, parents, subject = fields[0], fields[1], fields[2], fields[3], fields[4]
        added = deleted = 0
        for line in body.splitlines():
            cols = line.split("\t")
            if len(cols) < 3:
                continue
            a, d = cols[0], cols[1]
            if a.isdigit():
                added += int(a)
            if d.isdigit():
                deleted += int(d)
        try:
            # 统一换算到本机时区：作者可能在任意时区提交，混着看没法比。
            when = datetime.fromisoformat(iso).astimezone()
        except ValueError:
            when = now()
        commits.append(
            {
                "sha": sha,
                "author": author.strip() or "?",
                "when": when,
                "subject": " ".join(subject.split()),
                "added": added,
                "deleted": deleted,
                "merge": len(parents.split()) > 1,
            }
        )
    commits.reverse()  # log 是时间倒序，展示按时间正序
    return commits, total


def safe_commit_details(
    mirror: Path, older: str, newer: str, cap: int, cfg: dict | None = None
) -> tuple[list[dict], int]:
    """仓库只有 1 个提交时 `<sha>~1` 不存在，这类边界不能把整轮搞失败。"""
    try:
        return commit_details(mirror, older, newer, cap, cfg=cfg)
    except GitError:
        return [], 0


# ---------------------------------------------------------------- 消息构造


def short_ref(name: str) -> str:
    for prefix in ("refs/heads/", "refs/tags/"):
        if name.startswith(prefix):
            return name[len(prefix) :]
    return name


def kind_of(name: str) -> str:
    return "tag" if name.startswith("refs/tags/") else "branch"


def fmt_commit(index: int, c: dict) -> str:
    stats = "merge" if c["merge"] else f"+{c['added']}/-{c['deleted']}"
    when = f"{c['when']:%m-%d %H:%M}"
    return f"{index}. {c['sha'][:7]} {when} {c['author'][:18]} {stats}\n   {c['subject'][:100]}"


def fmt_span(commits: list[dict], lang: str) -> str:
    if not commits:
        return ""
    first, last = commits[0]["when"], commits[-1]["when"]
    if first.date() == last.date():
        return ""
    days = (last.date() - first.date()).days
    return i18n.t(lang, "digest.span", days=days)


def build_repo_message(slug: str, events: list[dict], cfg: dict) -> str:
    lang = i18n.normalize_language(cfg.get("language"))
    tz = datetime.now().astimezone().strftime("%z")
    header_key = "digest.header_one" if len(events) == 1 else "digest.header"
    lines = [i18n.t(lang, header_key, slug=slug, count=len(events), tz=tz)]
    for ev in events:
        ref = short_ref(ev["ref"])
        is_tag = kind_of(ev["ref"]) == "tag"
        if ev["kind"] == "deleted":
            key = "digest.tag_deleted" if is_tag else "digest.branch_deleted"
            lines.append(f"* {i18n.t(lang, key, ref=ref)}")
            continue
        if ev["kind"] == "new":
            if is_tag:
                lines.append(f"* {i18n.t(lang, 'digest.new_tag', ref=ref)}")
            else:
                head = ev["commits"][-1] if ev["commits"] else None
                extra = f"  ({head['sha'][:7]} {head['subject'][:60]})" if head else ""
                lines.append(f"* {i18n.t(lang, 'digest.new_branch', ref=ref)}{extra}")
            if ev.get("commits"):
                lines.extend("  " + fmt_commit(i, c) for i, c in enumerate(ev["commits"][-3:], 1))
            continue
        if ev["kind"] == "rewritten":
            head = ev["commits"][-1] if ev["commits"] else None
            if is_tag:
                lines.append(
                    f"* {i18n.t(lang, 'digest.tag_moved', ref=ref, old=ev['old'][:7], new=ev['new'][:7])}"
                )
            else:
                lines.append(
                    f"* {i18n.t(lang, 'digest.rewritten', ref=ref, old=ev['old'][:7], new=ev['new'][:7])}"
                )
            if head:
                lines.append("  " + fmt_commit(1, head))
                lines.append(f"  https://github.com/{slug}/commit/{head['sha'][:12]}")
            continue
        # 正常新增提交
        total = ev["total"]
        shown = len(ev["commits"])
        span = fmt_span(ev["commits"], lang)
        head_key = "digest.update_head_one" if total == 1 else "digest.update_head"
        lines.append(f"* {i18n.t(lang, head_key, ref=ref, total=total, span=span)}")
        lines.extend("  " + fmt_commit(i, c) for i, c in enumerate(ev["commits"], 1))
        if total > shown:
            hidden_key = "digest.older_hidden_one" if total - shown == 1 else "digest.older_hidden"
            lines.append(i18n.t(lang, hidden_key, count=total - shown))
        if ev["kind_label"] == "branch":
            lines.append(f"  compare: https://github.com/{slug}/compare/{ev['old'][:12]}..{ev['new'][:12]}")
            latest_url = f"https://github.com/{slug}/commit/{ev['new'][:12]}"
            lines.append(f"  {i18n.t(lang, 'digest.latest', url=latest_url)}")
        else:
            lines.append(f"  tag: https://github.com/{slug}/releases/tag/{ref}")
    return "\n".join(lines)


# ---------------------------------------------------------------- 推送接缝
# deliver() 已经搬进 channels.prepare()：按 cfg["channel"] 选通道，返回 send(text)。
# 这里只保留接缝的注入点（run_round 的 deliver_fn 参数），加通道 = 在 channels.py
# 里实现一个 _prepare_<channel>（ADR-0008）。


# ---------------------------------------------------------------- 一轮检查


def check_repo(root: Path, slug: str, state: dict, cfg: dict) -> list[str]:
    """检查一个仓库，返回待发送的分片（无变化返回 []）。

    只负责「看」：是否落盘 state.json、是否真的发送，都由调用方（run_round）决定 ——
    dry-run 不推进 ref 基线正是靠调用方不落盘，而不是靠这里分叉。
    """
    lang = i18n.normalize_language(cfg.get("language"))
    mirror, fresh = ensure_mirror(root, slug, cfg)
    if fresh:
        fetch_mirror(mirror, cfg["fetch_timeout_sec"], cfg)
        refs = list_refs(mirror, cfg["exclude_ref_patterns"], cfg)
        state["repos"][slug] = {
            "refs": refs,
            "baseline_at": now().isoformat(timespec="seconds"),
            "fail_count": 0,
            "fail_alerted": False,
        }
        log(root, i18n.t(lang, "log.baseline_first", slug=slug, count=len(refs)))
        return []

    fetch_mirror(mirror, cfg["fetch_timeout_sec"], cfg)
    refs = list_refs(mirror, cfg["exclude_ref_patterns"], cfg)
    known = state["repos"].setdefault(
        slug, {"refs": {}, "fail_count": 0, "fail_alerted": False}
    )
    old_refs: dict[str, str] = known.get("refs", {})
    if not old_refs:
        known["refs"] = refs
        known["baseline_at"] = now().isoformat(timespec="seconds")
        log(root, i18n.t(lang, "log.baseline_missing", slug=slug, count=len(refs)))
        return []

    events: list[dict] = []
    for name, sha in sorted(refs.items()):
        if name not in old_refs:
            commits: list[dict] = []
            content = peel(mirror, sha, cfg)
            if content and kind_of(name) == "branch":
                commits, _ = safe_commit_details(mirror, f"{content}~1", content, 3, cfg)
            events.append(
                {
                    "kind": "new",
                    "ref": name,
                    "new": sha,
                    "kind_label": kind_of(name),
                    "commits": commits,
                }
            )
            continue
        if old_refs[name] != sha:
            older, newer = old_refs[name], sha
            if kind_of(name) == "tag":
                events.append({"kind": "rewritten", "ref": name, "old": older, "new": newer, "commits": []})
                continue
            base_old, base_new = peel(mirror, older, cfg), peel(mirror, newer, cfg)
            if not base_old or not base_new:
                events.append({"kind": "rewritten", "ref": name, "old": older, "new": newer, "commits": []})
                continue
            if is_ancestor(mirror, base_old, base_new, cfg):
                commits, total = commit_details(mirror, base_old, base_new, cfg["max_commits_per_ref"], cfg)
                events.append(
                    {
                        "kind": "update",
                        "ref": name,
                        "old": base_old,
                        "new": base_new,
                        "commits": commits,
                        "total": total,
                        "kind_label": "branch",
                    }
                )
            else:
                commits, _ = safe_commit_details(mirror, f"{base_new}~1", base_new, 1, cfg)
                events.append(
                    {"kind": "rewritten", "ref": name, "old": base_old, "new": base_new, "commits": commits}
                )
    for name in sorted(set(old_refs) - set(refs)):
        events.append({"kind": "deleted", "ref": name})

    known["refs"] = refs
    known["last_check"] = now().isoformat(timespec="seconds")
    known["fail_count"] = 0
    known["fail_alerted"] = False
    if not events:
        log(root, i18n.t(lang, "log.no_changes", slug=slug, count=len(refs)))
        return []

    message = build_repo_message(slug, events, cfg)
    log(root, i18n.t(lang, "log.repo_changed", slug=slug, count=len(events), chars=len(message)))
    for ev in events:
        desc = f"{ev['kind']} {ev['ref']}"
        if ev.get("total"):
            desc += f" ({ev['total']} commits)"
        log(root, f"  - {desc}")
    parts = chunk(message, cfg["chunk_limit"])
    if len(parts) > cfg["max_chunks_per_repo"]:
        parts = parts[: cfg["max_chunks_per_repo"]]
        parts[-1] += "\n" + i18n.t(lang, "digest.truncated")
    return parts


@dataclass(frozen=True)
class RoundReport:
    """一轮的结果。CLI 用它算退出码，托盘应用用它更新状态行与预览抽屉。"""

    repos: int = 0
    parts: list[str] = field(default_factory=list)  # 待发/已发的分片（预览时就是将要发的内容）
    sent: int = 0
    failures: int = 0
    skipped: str | None = None  # "lock"：上一轮仍在运行
    status: str = "ok"  # ok / dry-run / not-sent / no-recipient / setup-failed / no-repos

    @property
    def exit_code(self) -> int:
        """与引入托盘前 main() 的返回值逐字一致：0 正常，1 发不出去，2 配置有问题。"""
        if self.status == "no-repos":
            return 2
        if self.status in ("no-recipient", "setup-failed"):
            return 1
        return 0


def run_round(
    root: Path,
    cfg: dict,
    repos: list[str] | None = None,
    *,
    dry_run: bool = False,
    no_notify: bool = False,
    deliver_fn: Callable[[str], None] | None = None,
    on_event: Callable[[dict], None] | None = None,
) -> RoundReport:
    """跑一轮：取锁 → 逐仓库 fetch/比对 → 发送 → 落盘状态。

    dry_run 只打印不发送、**不推进 ref 基线**（不 save_state）；no_notify 推进状态但不发送。
    这是本工具唯一的「一轮」实现，CLI 与托盘都走这里。deliver_fn 是推送接缝的注入点
    （测试/预览用），缺省用 channels.prepare(cfg, root) 现做的发送器。
    """
    lang = i18n.normalize_language(cfg.get("language"))
    run_repos = list(repos if repos is not None else cfg["repos"])
    emit = on_event or (lambda _event: None)

    def finish(report: RoundReport) -> RoundReport:
        emit({"kind": "round-done", "status": report.status, "sent": report.sent})
        return report

    if not run_repos:
        log(root, i18n.t(lang, "log.no_repos", path=cfg.get("_config_path")), "ERROR")
        return finish(RoundReport(status="no-repos"))

    with Lock(root, cfg["lock_stale_sec"]) as acquired:
        if not acquired:
            log(root, i18n.t(lang, "log.lock_busy"))
            return finish(RoundReport(repos=len(run_repos), skipped="lock"))

        emit({"kind": "round-start", "repos": len(run_repos), "dry_run": dry_run})
        state = load_state(root)
        pending: list[str] = []
        previewed: list[str] = []  # dry-run 的分片：只进报告，永不进发送队列
        failures = 0
        for slug in run_repos:
            emit({"kind": "repo-start", "repo": slug})
            try:
                parts = check_repo(root, slug, state, cfg)
                if dry_run:
                    for i, part in enumerate(parts, 1):
                        say(i18n.t(lang, "log.dry_run_part", slug=slug, index=i, total=len(parts), chars=len(part), part=part))
                    previewed.extend(parts)
                else:
                    pending.extend(parts)
                emit({"kind": "repo-done", "repo": slug, "parts": len(parts)})
            except GitError as exc:
                failures += 1
                emit({"kind": "repo-error", "repo": slug, "error": str(exc)})
                entry = state["repos"].setdefault(slug, {"refs": {}, "fail_count": 0, "fail_alerted": False})
                entry["fail_count"] = int(entry.get("fail_count", 0)) + 1
                log(root, i18n.t(lang, "log.check_failed", slug=slug, count=entry["fail_count"], error=exc), "ERROR")
                if entry["fail_count"] >= cfg["fail_alert_threshold"] and not entry.get("fail_alerted"):
                    entry["fail_alerted"] = True
                    pending.append(i18n.t(lang, "digest.fail_alert", slug=slug, count=entry["fail_count"], error=exc))
            finally:
                if not dry_run:  # dry-run 只是预览，不能推进状态，否则真跑时就不报了
                    save_state(root, state)

        if dry_run or no_notify or not cfg.get("notify", True):
            if pending and dry_run:
                for part in pending:
                    say(i18n.t(lang, "log.dry_run_notice", part=part))
            log(root, i18n.t(lang, "log.round_pending", repos=len(run_repos), failures=failures, pending=len(pending)))
            return finish(
                RoundReport(
                    repos=len(run_repos),
                    parts=previewed + pending,  # 预览分片只出现在报告里，不会被发送
                    failures=failures,
                    status="dry-run" if dry_run else "not-sent",
                )
            )

        if not pending:
            log(root, i18n.t(lang, "log.round_quiet", repos=len(run_repos)))
            return finish(RoundReport(repos=len(run_repos), failures=failures))

        try:
            send = deliver_fn or channels.prepare(cfg, root)
        except channels.ChannelConfigError as exc:
            log(root, i18n.t(lang, "log.no_recipient", error=exc), "ERROR")
            return finish(
                RoundReport(repos=len(run_repos), parts=pending, failures=failures, status="no-recipient")
            )
        except channels.ChannelSetupError as exc:
            log(root, i18n.t(lang, "log.setup_failed", error=exc), "ERROR")
            return finish(
                RoundReport(repos=len(run_repos), parts=pending, failures=failures, status="setup-failed")
            )

        sent = 0
        for index, part in enumerate(pending, 1):
            try:
                send(part)
                sent += 1
                emit({"kind": "sent", "index": index, "total": len(pending), "chars": len(part)})
                log(root, i18n.t(lang, "log.sent_chars", chars=len(part)))
            except RuntimeError as exc:
                log(root, i18n.t(lang, "log.send_failed", error=exc), "ERROR")
            time.sleep(1.2)  # QQ 单关系 / 群机器人普遍限 20 条/分钟，留足余量
        log(root, i18n.t(lang, "log.round_done", repos=len(run_repos), failures=failures, sent=sent, total=len(pending)))
        return finish(
            RoundReport(repos=len(run_repos), parts=pending, sent=sent, failures=failures)
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="refwatch —— GitHub 仓库监视器 -> 推送通道")
    parser.add_argument("--root", help="数据目录（镜像仓/state.json/logs），默认脚本所在目录")
    parser.add_argument("--config", help="配置文件路径（默认脚本目录下的 config.json）")
    parser.add_argument("--repo", action="append", help="只检查该仓库（可重复）")
    parser.add_argument("--dry-run", action="store_true", help="只打印消息，不发送、不推进状态")
    parser.add_argument("--no-notify", action="store_true", help="不推送，只写日志/状态")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve() if args.root else HERE
    root.mkdir(parents=True, exist_ok=True)
    cfg = load_config(root, Path(args.config).resolve() if args.config else None)
    report = run_round(
        root,
        cfg,
        args.repo or cfg["repos"],
        dry_run=args.dry_run,
        no_notify=args.no_notify,
    )
    return report.exit_code


def _root_from_argv() -> Path:
    argv = sys.argv[1:]
    for i, item in enumerate(argv):
        if item == "--root" and i + 1 < len(argv):
            return Path(argv[i + 1]).resolve()
        if item.startswith("--root="):
            return Path(item.split("=", 1)[1]).resolve()
    return HERE


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    except SystemExit:
        raise
    except BaseException:  # pythonw 下没有 stderr，异常必须自己落盘
        import traceback

        _tb = traceback.format_exc()
        try:
            _root = _root_from_argv()
            _logs = _root / "logs"
            _logs.mkdir(parents=True, exist_ok=True)
            # 语言跟着配置走；连配置都读不出来时按 zh —— 这本身就是兜底路径。
            _lang = "zh"
            try:
                _lang = i18n.normalize_language(load_config(_root, None).get("language"))
            except Exception:  # noqa: BLE001 - 兜底路径里读盘失败只能回到默认
                pass
            with (_logs / "error.log").open("a", encoding="utf-8") as fh:
                fh.write(f"{now():%Y-%m-%d %H:%M:%S} {i18n.t(_lang, 'log.uncaught')}\n{_tb}\n")
        except OSError:
            pass
        raise SystemExit(1) from None
