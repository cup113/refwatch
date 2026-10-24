# refwatch — agent 须知

本地 GitHub 变更监视器：拉 ref 的 SHA、比上一轮基线、把摘要推到 QQ。领域词与取舍在
[CONTEXT.md](CONTEXT.md) 与 [docs/adr/](docs/adr/)，用户文档在 [README.zh-CN.md](README.zh-CN.md)
（英文 [README.md](README.md)）。本文件只写「踩过、且还会再踩」的工程事实。

## 部署：新增顶层模块必须登记两处之一

源码部署（`install.ps1`，README 的无头安装路径）只拷贝 `$programFiles` 里列出的文件；
**新增一个根级 `.py` 而忘了登记，就是部署副本 `ModuleNotFoundError`，而 CI 全绿** ——
因为 CI 从不跑 `install.ps1`。

- 新增运行时模块 → 加进 `install.ps1` 的 `$programFiles`。
- 新增**不**部署的根级脚本 → 加进 `tests/test_deploy_manifest.py` 的 `BLACKLIST`（附注释）。
- `tests/test_deploy_manifest.py` 守这条：`git` 追踪的根级 `.py` − 黑名单 == 白名单，
  外加「白名单不许列不存在的文件」「黑名单不许藏入口 import 的模块」。
  它用 `git ls-files` 而不是 `Path.glob`，所以并行会话留下的未追踪文件不会造成假阳性。

exe 形态不需要手改：Nuitka 按 import 图收模块，`installer.iss` 递归拷贝 `build\tray.dist\*`。
`build.yml` 只在 `v*` tag / 手动 dispatch 时跑，所以**打包链路没有日常 CI 覆盖**。

## 工具链

```bash
python -m ruff check .                          # CI 跑的就是这一条
python -X utf8 -m unittest discover -s tests     # CI 主体；别再引第三方测试依赖
```

- **只跑 `ruff check`，不要跑 `ruff format`。** 仓库不是 format-clean，`ruff format` 会
  一次改写十几条既有文件（含 `tools/`、`installer.iss` 之外的东西），是纯噪音。
  配置见 `ruff.toml`：line-length 120、**target py39**、忽略 E501。
- `target py39` 是真约束：同引号嵌套 f-string 是 3.12+，ruff 会报 `invalid-syntax`。
- 测试矩阵（`ci.yml`）是 ubuntu/windows × 3.9/3.13，四个腿都跑 ruff + `watch.py --help` + 单测。
- 临时目录用 `tests/helpers.temp_root()`（`os.makedirs` + uuid），**不要** `tempfile.mkdtemp`：
  受限 Windows（ACL 沙箱）里 mkdtemp 建的目录会丢掉可继承写权限，之后连 `mkdir logs/`
  都 `WinError 5`。`REFWATCH_TEST_TMP` 可以把临时目录指到别处。
- 需要建裸仓当 `origin` 的测试（`test_state_machine`、`test_round_seam` 的真仓部分、
  `test_refs`）在 ACL 沙箱里必挂（git 本地传输要 fork `sh.exe`）：无沙箱跑，或交给 CI。
  其余约 140 个测试在沙箱里不到 1 秒。

## CLI 的 `--root` 不搬配置

`watch.main` 的配置落点仍是**脚本目录**（`watch.HERE`），除非显式 `--config`。
`--root` 只决定 `state.json` / `repos/` / `logs/`。`install.ps1` 之所以能只用 `--root`，
是因为它把 `watch.py` 也拷进了 `$Dest`。手工冒烟时忘了这点，会把「读到脚本目录的
默认配置」误判成功能失效。

## 文案：六层 + 数据不翻译

`config.json` 的 `language`（`zh`|`en`，默认 `zh`）翻转全部运行时文案，唯一真源是
[i18n.py](i18n.py)。改文案时守三条不变式：

1. 六层都走 `t()`：摘要、日志行、托盘（菜单/状态/弹框）、设置面板、面板事件行、
   `qq_notify` 抛出的错误（那层抛**英文中性诊断串**，人话前缀由 `watch.py` 捕获点翻译）。
2. **数据不翻译**：commit subject/author、repo slug、ref 名、URL、git 与 QQ API 的
   原始输出，永远原样。
3. **zh 条目必须与引入 i18n 前逐字相同** —— 大量断言依赖它；改中文措辞要同步改测试。

`tests/test_i18n.py` 守两表键集合与占位符一致；`t()` 对缺 key 直接 `AssertionError`。
`_one` 后缀是单数变体（zh 两式同文，代码按计数选，不按语言分叉）。

托盘菜单标签是 pystray 的 callable `text`，`update_menu()` 时按当前 `cfg` 重算 —— 语言
因此能热切换（含「界面语言」子菜单的勾选）；面板标签建一次，换语言后由
`App.set_language`/`on_saved` 销毁重建。

## 面板截图（docs/screenshots/*.png）

现有截图全是 1180×799、无标题栏、DPI-aware 的客户区。复现配方：

1. `tray.enable_dpi_awareness()`（必须在创建 Tk 之前，否则是位图拉伸）；
2. 直接构造 `panel.Panel(...)`，`on_saved`/`on_run`/`get_status` 传假回调，
   数据目录塞一份临时 `config.json`（可选 `.env` 或凭据环境变量）；
   **先把所有通道的凭据环境变量清空**（`channels.env_keys_of`），否则通知页会显示
   这台机器真实的值；再把 `DSH_HOME` 指到不存在的目录，避免读到 `~/.dsh/.env`；
3. `p.show()` → 从 `p._window` 的子控件里找 `ttk.Notebook` 并 `select(页号)`；
4. `ImageGrab.grab(bbox=(winfo_rootx, winfo_rooty, +winfo_width, +winfo_height), all_screens=True)`。

托盘**右键菜单**截不到（原生弹出菜单），菜单文案只能靠 `test_tray_contract.py` 断言。

## 协作与提交

- 多个会话可能同时改这个工作区：提交前用**显式的路径** `git add <file>…`，
  不要 `git add -A`（会把别人未完成的 WIP 卷进你的提交）。并行会话之间优先各自
  `git worktree`。
- 提交信息用 conventional 前缀（`feat:` / `fix:` / `docs:` / `ci:`…），并在正文里用
  `Closes #N` 关联 issue；PR 只在被明确要求时开。
- 推送走工具桥（`bridge_run` 的 `git push origin <branch>`），不要用沙箱内的 `pwsh` 直推。
