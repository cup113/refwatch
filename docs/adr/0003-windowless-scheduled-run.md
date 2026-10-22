# 计划任务用 pythonw + 子进程 CREATE_NO_WINDOW

`pythonw.exe` 没有控制台，所以它自己从不显示窗口。但它 spawn 的每个 `git.exe` 都是控制台子系统
程序；父进程没有控制台时，Windows 会给子进程**新分配**一个控制台窗口 —— 于是每 30 分钟闪几下黑
窗口（每仓库一轮 5~8 次 git 调用）。修法只有一处：`watch.py` 的 `git()` 用
`creationflags=subprocess.CREATE_NO_WINDOW` 启动 git。

在 Windows 11 + Git for Windows 上实测（`pythonw` → 子进程，枚举可见顶层窗口）：

| 场景 | 新出现的可见窗口 |
|---|---|
| `timeout /t 2`，无 flag | 1 个（旁噪，与 git 无关） |
| `timeout /t 2` + `CREATE_NO_WINDOW` | 0 |
| `git -C <mirror> fetch`，无 flag | **1 个 `ConsoleWindowClass`，标题 `...\Git\cmd\git.exe`** |
| `git fetch` + `CREATE_NO_WINDOW` | **0** |

同一份代码、只差一个 flag，所以这不是「环境碰巧」：窗口是 git 子进程的控制台。

## Considered Options

- **`.cmd` / `.bat` 包装器**：`cmd.exe` 是控制台程序，每轮必闪一次，正是要避免的。
- **VBScript / `mshta` 隐藏启动**：父进程仍然是控制台程序，只是把窗口藏起来；而且 `.vbs`
  容易被 AV 拦。
- **把任务改成「不管用户是否登录都运行」（session 0 / S4U）**：从根上不可能有可见窗口，
  但注册需要管理员权限，S4U 上下文可能取不到网络凭据（`git fetch` 会挂）。最小修复已经归零，
  没必要付这个代价。
- **任务的 `Hidden` 设置**：只把任务从计划任务 UI 里藏起来，与窗口无关。

## Consequences

- 这一行看起来像「可以清理掉的冗余」，实际是载荷 —— `watch.py` 里写了注释指向本 ADR。
- 窗口不会出现在任何日志里，单元测试也抓不到，所以仓库里带 `tools/window_probe.py`：
  它在目标命令运行期间枚举可见顶层窗口，0 个才算过。**改动 git 调用方式后跑一次**。
- 将来若引入其它子进程（例如解压、调用 `gh`），同样要带这个 flag，否则同样的 bug 会以别的
  形式回来。探针是唯一的回归检查。
