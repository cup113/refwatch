# 首发只做 QQ，但留一个推送接缝

消息是纯文本，QQ 只是一个目的地；但「把文本送到某处」的实现细节（AppID/Secret 换 token、
接收方 openid、主动消息频控、失败重试策略）在每个通道都不一样。首发只实现 QQ，并把
「送出去」收敛成 `watch.py` 里的一个函数 `deliver(text, cfg, token)`，而不是做一个通知插件系统。

## Considered Options

- **首发就做 QQ + 通用 webhook**：受众更广，但配置多一个分支、测试面翻倍，而第一版的价值在
  「装完就能用」。
- **彻底通用化：主程序只吐文本**：最干净，却丢掉「无人值守、开箱即用」这个核心卖点。
- **插件系统（entry points 式通道注册）**：零依赖的脚本形态最先被这种抽象吃掉，成本远超收益。

## Consequences

- 加一个通道 = 实现 `deliver()`，README 里用几行说明；不需要动 fetch / 比对 / 格式化。
- `qq_notify.py` 自己也能单独当命令行工具用（`python qq_notify.py "消息"`）。
- 取 token 的逻辑仍留在 `main()` 的 QQ 分支里；换成不需要 token 的通道时，把 `token` 参数
  忽略即可 —— 这是刻意的，避免为一个假想中的插件 API 提前设计。
