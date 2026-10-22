# ADR-0007: Nuitka --standalone + Inno Setup，一个 GUI 子系统的 exe

发行物是 `refwatch-setup-<version>.exe`（Inno Setup 打的每用户安装包）与一个免安装 zip，
里面的主体都是 Nuitka `--standalone` 编出来的目录：`refwatch.exe` + 依赖 + `assets/`。
`refwatch.exe` 是**一个**入口，两种形态：不带参数 = 托盘常驻；带 `--one-round`（或
`--dry-run`/`--no-notify`/`--repo`）= 跑一轮就走，直接转发给 `watch.main()`。

单入口是刻意的：GUI 子系统进程没有 stdout，另做一个控制台版的 `refwatch-cli.exe` 意味着
CI 里编两次、发两个 exe、文档解释两遍差异；而计划任务与窗口探针真正需要的只是
「跑一轮然后退出并给出退出码」，控制台输出它们都不用。

`git` **不随包分发**：Git for Windows 有一整套自己的运行时，塞进 installer 会让包体翻倍
且升级路径互相打架。所以 `Requirements` 里始终写着「需要自己装 git」，缺了它就是抓取全失败的告警。

## Considered Options

- **PyInstaller `--onefile`**：分发物只有一个文件，最符合直觉；但每次启动都要解包到临时目录
  （托盘常驻虽只解一次，首次启动明显变慢），而且单文件壳是杀软误报的重灾区。
- **Nuitka `--onefile`**：同上，只是编译器不同。
- **`--standalone` 目录裸发（不放安装包）**：用户拿到一个目录，自启、开始菜单、卸载都得手动 ——
  与「装完就能用」冲突。所以目录形态保留为 zip 备选，主推安装包。

## Consequences

- 版本号从 tag 派生：安装包用 `AppVersion` 预处理器变量（`/DAPP_VERSION=...`，不能用 `GetEnv`），
  exe 的版本资源用**纯数字**点分格式（`--file-version` 只认 `A.B.C[.D]`），所以 CI 里算两次。
- 每用户安装：`%LOCALAPPDATA%\Programs\refwatch`，数据在 `%LOCALAPPDATA%\refwatch` ——
  升级/卸载不碰 `state.json`；卸载默认**保留**数据，删数据是卸载页里默认「否」的一个确认。
- 安装/升级前 `taskkill /IM refwatch.exe`：托盘进程占着 exe，不先停掉覆盖会失败。
- exe 里的资源路径按 `Path(sys.argv[0]).parent` 解析，**不用 `sys.executable`**
  （Nuitka 编译后它可能指向解释器）；判断「是不是编译版」用 `__compiled__`。
- CI 必须带 `--assume-yes-for-downloads`（`--standalone` 在 Windows 上要下 Dependency Walker，
  非交互环境默认拒绝会让构建直接 FATAL），压缩用 `Compress-Archive`（runner 上没有 `zip`），
  release 步骤要 `permissions: contents: write`。
- 唯一能证明「包没漏模块」的检查是跑 exe：`refwatch.exe --one-round --dry-run --root <tmp>`
  期望退出码 2（没有配置仓库）。那是导入期错误码 1 与正常路径的分界。
