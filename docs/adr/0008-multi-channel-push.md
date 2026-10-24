# 多通道推送：首批 QQ 之外的五个通道

ADR-0004 落地时把「送出去」收敛成 `watch.py` 里的 `deliver(text, cfg, token)`，并预告「加一个通道
= 替换这一个函数」。多通道需求真来的时候，这个形状不够用了：openid 检查和取 token 的逻辑住在
`run_round` 里，换通道绕不开它们。本 ADR 记录接缝的加宽和首批通道的取舍。

## Decision

- **通道选择**：`config.json` 新增 `channel`（缺省 `qq`，非法值回退 + WARN，与 `language` 同构）。
  单选，不做多通道并行。
- **首批通道**：`wecom`（企业微信群机器人）、`dingtalk`（钉钉）、`feishu`（飞书）、
  `serverchan`（Server酱，微信）、`bark`（iOS）。全部「一条 URL/Key + 一次 HTTP POST」形态，
  全部标准库可实现（钉钉加签、飞书签名用 hmac）。
- **接缝加宽**：`deliver()` 搬进 `channels.py`，成为 `prepare(cfg, root) -> Sender`：
  - `ChannelConfigError`（缺 openid/webhook/SendKey）→ RoundReport 的 `no-recipient`；
  - `ChannelSetupError`（取 token、构造签名失败）→ `setup-failed`；
  - 两类都映射退出码 1（与旧的 `no-openid` / `token-failed` 契约一致）。
    QQ 缺 AppID/Secret 从旧 `token-failed` 挪到 `no-recipient` —— 它本来就是配置缺失，
    新语义更诚实；退出码与面板提示路径不变。
- **凭据全走 `.env`**（环境变量优先）：webhook URL / SendKey「持有即可骚扰」，信任级别与
  `QQBOT_SECRET` 相同，统一交给 tray 的崩溃日志脱敏；接收方标识（qq 的 openid）仍属 config。
  `channels.py` 的报错串带 URL 时只保留路径（`?***`），webhook 的 key 不进日志。
- **通道内再分片**：`run_round` 仍按 `chunk_limit` 切（`max_chunks_per_repo` 语义不变），
  适配器只对自家硬上限二次切分 —— wecom 的 2048 **字节**需要按 UTF-8 贪心打包（按字符切对
  CJK 会超）；serverchan/bark 做 title（首行）+ body 拆分。
- **仍然不做插件系统**（延续 ADR-0004）：通道 = `channels.py` 里一个 `_prepare_<channel>()`
  加注册表一行，`test_deploy_manifest` 守着新模块进 `install.ps1` 清单。

## Considered Options

- **Telegram / Discord / Slack**：API 都极简，但国内不可达（需代理），与 QQ-first 的受众错位；
  留在后续梯队，与「通用 webhook」一起覆盖。
- **通用 webhook（自定义 URL + 字段名）**：一个通道覆盖 ntfy/Gotify/Discord/Slack/Pushover，
  但配置面变成「URL + 模板」，第一版说不清「装完就能用」；后续按需进。
- **pushplus / WxPusher / PushDeer**：与 Server酱同构（token+title+content），加 Adapter 成本
  极低，等有人要再进。
- **多通道并行发送**：`channel` 变列表、接缝变 fan-out，测试面翻倍且 UI 复杂化；单选覆盖
  「个人接收」的主场景。

## Consequences

- QQ 行为不变：`channel` 缺省即旧路径，openid/token 语义只是换了个落点。
- 加通道 = 一个 `_prepare_*` 函数 + `CREDENTIALS` 一行 + i18n 两个语言各三四个键；
  `test_channels` 的注册表测试会逼着面板标签跟上。
- 各通道端点与上限以官方文档为准核对过（wecom 2048 字节、钉钉/飞书 20 条/分钟、
  Server酱免费 5 条/天）；超限的再分片是适配器自己的事，不传染 `check_repo`。
- 新根级模块 `channels.py` 必须进 `install.ps1` 的 `$programFiles`（源码部署唯一清单），
  `tests/test_deploy_manifest.py` 守着。
