# 只跟踪 refs/heads 与 refs/tags，且不用 clone --mirror

镜像仓用 `git init --bare` 建，然后显式配置两条 refspec（`+refs/heads/*:refs/heads/*`、
`+refs/tags/*:refs/tags/*`），而不是 `git clone --mirror`。因为 `--mirror` 等价于
`+refs/*:refs/*`，会把 `refs/pull/*` 一起抓下来 —— GitHub 给每个 PR 都广播一个 ref，
热闹的仓库有几百个，跟踪它等于每推一次 PR 就发一条消息。

## Considered Options

- **`git clone --mirror`**：一行命令，但拉 `refs/*`，PR ref 全进来。
- **clone 后靠过滤规则忽略**：数据已经拉下来了，`fetch --prune` 会一直为几百个 PR ref 做工作。
- **只抓默认分支**：丢掉「分支新增/删除」和 tag 的能力，正是本工具的价值所在。

## Consequences

- `watch.py` 里维护着 `ALWAYS_EXCLUDE = ("refs/pull/*", "refs/merge-requests/*")` 作为第二道闸，
  即使有人往镜像仓里塞了 PR ref 也不会被当成变化。
- 仓库缺少 PR 信息是刻意的；要看 PR 请用 GitHub 的界面。
- 分支/tag 之外的 ref（notes、stash）不跟踪，属于同一个取舍。
