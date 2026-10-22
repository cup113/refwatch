# 变更日志

本文件记录 refwatch 的重要变更，格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号用 [语义化版本](https://semver.org/lang/zh-CN/)。

版本号**只来自 git tag**（`v*`）：它同时决定安装包文件名、exe 的文件版本，以及 `build.yml` 是否发 Release。
代码里没有第二处版本字符串，所以不存在“改了一处忘了另一处”。

## [1.0.0] - 2026-10-22

首个公开版本：从「纯命令行 + Windows 计划任务」变成「托盘应用 + 图形化配置 + 安装包」。

### 新增

- **托盘应用**（pystray + Pillow + Tk）：常驻通知区域，配置在窗口里改，随时「立即跑一轮」或「预览」；
  取代计划任务成为 Windows 的默认形态（[ADR-0005](docs/adr/0005-tray-app-replaces-scheduled-task.md)、
  [ADR-0006](docs/adr/0006-tray-stack-pystray-pillow-tk.md)）
- **设置面板**四个页签（仓库 / 通知 / 调度 / 高级）+ 日志抽屉：保存即生效，合并写入，
  不认识的键原样保留；配置写坏先备份成 `config.json.bak.<时间戳>`
- **内置调度器**（`interval_minutes`，默认 30）：手动一轮不会移动下次运行时间，支持暂停 / 继续
- **单实例**（`Local\refwatch-tray` 互斥体）与**开机自启**（HKCU Run，托盘菜单可随时开关）
- 首次启动检测旧的 `refwatch` 计划任务，**询问一次**后才删除，绝不静默删除
- **安装包与免安装 zip**：Nuitka `--standalone` + Inno Setup，每用户安装、不需要管理员；
  卸载默认**保留**配置与基线（[ADR-0007](docs/adr/0007-distribution-nuitka-standalone-inno.md)）
- 图标资产由 `tools/make_icon.py` 预生成入库，`--check` 校验产物与生成器同步
- `build.yml`：打 `v*` tag 时构建 exe + 安装包 + zip 并发 Release；exe 冒烟要求退出码 `2`

### 变更

- `watch.py` 拆出可测的轮次接口 `run_round()` → `RoundReport`（退出码 `0`/`1`/`2`），`main()` 变薄
- 配置新增 `interval_minutes` / `autostart` / `task_prompt_done`；`settings.py` 统一负责原子写、
  校验与 `_` 开头的注释键
- 命令行与无头用法完全保留；GUI 依赖只在 `requirements-gui.txt`，CLI 仍是零第三方依赖
- README 中英重写，补托盘用法、运维速查与面板截图（`docs/screenshots/`）；ADR-0003 补记托盘形态

### 修复

- 安装包缺 `ChineseSimplified.isl` 会让 release 步骤 `Compile aborted` → 语言包 vendor 进仓库
  （stock Inno Setup 只带 29 个 `.isl`，不含中文）
- `build.yml` 的版本名去掉文件名非法字符（`workflow_dispatch` 到分支时 `ref_name` 带 `/`）
- `tray.py` 无头透传时显式传 `--config`，否则 `--root` 之外的配置路径会被忽略

### 说明

- `git` **不随包分发**：需要自己装 Git for Windows 并在 `PATH` 里
- 面板只做配置与预览：重置基线、删镜像仓这类破坏性操作仍然要手动来（见 README「运维速查」）
