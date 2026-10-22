# refwatch

一个本地运行的 GitHub 变更监视器：从镜像仓里读出 ref 的 SHA，与上一轮的基线比对，
把「哪些 ref 发生了什么」整理成摘要推到 QQ。

## Language

### 仓库与抓取

**被监视仓库 (watched repository)**:
GitHub 上的一个 `owner/name`，在 `config.json` 的 `repos` 里出现一次。
_Avoid_: 项目、目标、GMP

**镜像仓 (mirror repo)**:
被监视仓库在本地的一个 bare 副本，只 `git fetch`，永远没有工作区。
_Avoid_: 克隆、本地仓、worktree

**ref 基线 (ref baseline)**:
某个 ref 上一轮看到的 SHA；`state.json` 里 `refs` 映射的一项，是整个工具的唯一真相。
_Avoid_: 快照、diff、上次提交

**静默基线 (silent baseline)**:
首次看到某个被监视仓库（或发现基线为空）时，只记录 SHA、不发消息的那一轮行为。
_Avoid_: 初始化、首次同步

### 变更

**事件 (event)**:
一个 ref 在一轮里发生的变化，只有四种：update、rewritten、new、deleted。
_Avoid_: 变化、diff、更新通知

**历史改写 (rewritten)**:
新 SHA 不是旧 SHA 的祖先，例如 force-push、rebase、tag 被移动。
_Avoid_: 冲突、回滚

**摘要 (digest)**:
一轮里某个被监视仓库的所有事件合成的一条人类可读文本。
_Avoid_: 报告、消息体

**compare 链接 (compare link)**:
GitHub 的 `old..new` 对比页，用来替代 diff 本体。
_Avoid_: diff 链接、补丁链接

**分片 (chunk)**:
摘要超过 `chunk_limit` 时切出来的单条可发送文本。
_Avoid_: 分段、part

### 运行

**一轮 (run)**:
一次完整执行：取锁 → 逐仓库 fetch → 比对 → 发送 → 落盘状态。
_Avoid_: 扫描、任务、job

**推送接缝 (deliver seam)**:
`watch.py` 里 `deliver()` 这个函数，是本工具唯一知道「消息怎么送出去」的地方。
_Avoid_: 通道、插件、notifier

**失败计数 / 告警 (fail count / alert)**:
连续抓取失败次数；达到 `fail_alert_threshold` 推一条告警，成功后清零。
_Avoid_: 错误率、重试次数

**接管 (take over)**:
锁文件年龄超过 `lock_stale_sec` 后，新一轮删掉旧锁并取得锁。
_Avoid_: 抢占、超时

**dry-run**:
只打印将要发送的内容，不发送、**不推进 ref 基线**。
_Avoid_: 试运行、validation

## Relationships

- 一个**被监视仓库**对应一个**镜像仓**（1:1）
- 一个**镜像仓**有多条 **ref 基线**（1:N）
- 一次**一轮**在每个**被监视仓库**上产生 0..N 个**事件**
- **事件**决定**摘要**内容；**摘要**被切成 1..N 个**分片**
- **ref 基线**只在「一轮完整成功」后推进；dry-run 与抓取失败都不推进
- **推送接缝**把**分片**交给具体的推送通道

## Example dialogue

> **Dev:** 「这轮 main 有变化吗？」
> **Maintainer:** 要看是**事件**还是内容变化：SHA 变了但不加新提交（force-push），那也是一个**事件**——**历史改写**。
>
> **Dev:** 「那我把 dry-run 跑一遍，基线就推进了，真跑时就不报了？」
> **Maintainer:** 不会。dry-run **不推进 ref 基线**，这正是它存在的理由；推进只发生在完整成功的一轮之后。
>
> **Dev:** 「抓取挂了但消息发出去了？」
> **Maintainer:** 抓取失败不产生**事件**，只累加**失败计数**；同一个仓库连续失败到阈值才发一条**告警**，成功后清零。

## Flagged ambiguities

- "变化" 曾被同时用来指「提交内容变了」和「ref 上出现了一个**事件**」——统一用**事件**，
  「内容变化」只在讨论 diff 时使用。
- "推送" 与 "通知" 混用：**抓取**（fetch）是拉数据，**推送**（deliver）是发消息。两者失败互不相干。
- "rewrite" 在 git 里分 rebase 和 force-push 两种，本工具不区分，统一叫**历史改写**——
  因为对一个被监视的 ref 来说，两者的可观测结果相同。
