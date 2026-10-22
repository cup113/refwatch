# 状态是 ref → SHA，不是 diff

我们对每个 ref 只记住上次看到的 SHA，而不是记住「上次看到的位置」或保留一份 diff。
原因：只有 ref → SHA 才能把四种情况区分开 —— 正常新增提交（旧 SHA 是新 SHA 的祖先）、
历史被改写（不是祖先）、ref 新增（状态里没有）、ref 删除（状态里有但 fetch 后不见了）。
diff 方案做不到第四种，也会把 force-push 显示成一大堆莫名其妙的删除。

## Considered Options

- **保存 diff / patch**：无法区分「历史改写」与「大批删除」，且体积随轮次增长。
- **保存 `git log --oneline -1` 的提交信息**：改名/改信息的 commit 会误判为变化。
- **保存 GitHub API 的 `pushed_at`**：只反映「有没有推」，不告诉你是哪个 ref，也不区分四种情况。

## Consequences

- `state.json` 是唯一真相，删掉它等于让所有仓库重新静默建基线。
- 状态必须是「一轮完整成功之后」才推进；`--dry-run` 不推进，否则真跑时就不报了。
- SHA 追不到时（有人删了对象/镜像仓被重建）落到 `rewritten`，宁可多报一次也不静默。
