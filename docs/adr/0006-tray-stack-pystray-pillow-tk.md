# ADR-0006: 托盘用 pystray + Pillow，面板用 tkinter

托盘与设置面板引入两个第三方运行时依赖：`pystray`（托盘图标与菜单）与 `Pillow`
（`pystray` 的图标接口就是 PIL 图像）。面板用标准库 `tkinter`，不再引入第三个。

这推翻了「零依赖」这条卖点，所以写清楚新的边界：**CLI 仍然零依赖**（`watch.py` / `qq_notify.py`
只用标准库，`python watch.py` 照旧不需要 `pip install`）；只有**打包出来的 exe 自带依赖**。
`requirements-gui.txt` 与 `requirements-dev.txt` 分开，现有的零依赖 CI 矩阵一行都不用改。

## Considered Options

- **纯 ctypes 自写 `Shell_NotifyIcon`**（约 150 行：隐藏消息窗口 + 窗口过程 + 菜单 + 图标）：
  依赖保持为零、exe 也小，但托盘的消息循环、DPI 缩放、气球提示全要自己维护，且只能跑 Windows；
  与「用成熟的、还在维护的库」相冲突，这部分不是本项目的价值所在。
- **`pywebview` + HTML 面板**：面板自由度最高，但多一个 webview 运行时依赖，
  打包体积与杀软误报都上一个台阶 —— 对一个本地小工具是净亏。
- **只做 `config.json` 的文本框编辑器**：成本最低，但 #1 要的「图形化的配置与编辑」几乎没被满足。

## Consequences

- 图标资产由 `tools/make_icon.py`（Pillow）生成并**入库**（`assets/refwatch.ico` / `.png`）：
  打包只需要文件，不必在 CI 里重跑绘制逻辑。`tests/test_icon.py` 只用 `struct` 解析 ICO/PNG，
  所以那 7 个尺寸的断言在没有第三方依赖的矩阵里也能跑。
- `panel.py` 把纯逻辑（表单 ↔ 配置、日志尾部、文案）放在顶层，widget 组装放在 `Panel` 里；
  前者可测，后者不在 CI 里开窗口。`tkinter` 用受保护的 import，缺了它只是面板不可用，
  纯逻辑测试不受影响。
- 托盘契约是**装配期**的硬约束：`text`/`checked` 回调收 1 个参数（MenuItem），`action` 收 2 个
  （icon, item），多了 pystray 在构造 `MenuItem` 时就抛 `ValueError` —— 而 `pythonw`/exe 下
  没有 stderr，现象只是「双击没反应」。`tests/test_tray_contract.py` 直接遍历真实菜单断言形状。
- `tray.py` 顶层 `import pystray`（不 lazy），于是 exe 的 `--one-round` 冒烟会真正加载
  `pystray._win32` / `PIL` / `six.moves.queue` —— 打包漏模块会在 CI 当场失败。
- 进程启动时要 `SetProcessDpiAwareness`（`tray.enable_dpi_awareness()`）：不做的话 Windows 把
  整个窗口按位图拉伸，高分屏上发虚，而且 Tk 报告的尺寸与屏幕像素对不上（控件会显得被切掉）。
- 面板里的控件必须由**同一个 parent** 创建再 `grid`：Tk 的 `pack`/`grid` 默认落在控件的 master 上，
  把别处建好的控件塞进某一行会静默排到父容器里，整个面板会散架 —— 这个 bug 已经在
  `Panel._field()` 的注释里留了记号。
