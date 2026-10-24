<#
.SYNOPSIS
    refwatch 一键安装：部署到运行目录 -> 建立首个基线 -> 注册计划任务。

.DESCRIPTION
    幂等：重复运行只覆盖程序文件。config.json / state.json / repos / logs / .env
    都不会被动；只有「一个配置文件都没有」时才会从 config.example.json 生成一份。

    计划任务的动作直接指向 pythonw.exe（GUI 子系统，自己没有控制台）。注意：
    pythonw 只保证自己不闪窗口，它 spawn 的 git.exe 仍会闪 —— 那部分由 watch.py
    里的 CREATE_NO_WINDOW 负责，见 docs/adr/0003-windowless-scheduled-run.md。
    不要用 .cmd / .bat 包一层：cmd.exe 是控制台程序，每轮必闪一次。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\install.ps1
    powershell -ExecutionPolicy Bypass -File .\install.ps1 -Dest 'D:\gh-watch' -Minutes 30
    powershell -ExecutionPolicy Bypass -File .\install.ps1 -NoTask          # 只部署不注册
    powershell -ExecutionPolicy Bypass -File .\install.ps1 -Dest '.\local-test' -NoTask
#>
[CmdletBinding()]
param(
    [string]$Dest = (Join-Path $env:LOCALAPPDATA 'refwatch'),
    [int]$Minutes = 30,
    # 不要写成 `= $PSScriptRoot`：Windows PowerShell 5.1 里 param 块执行时
    # $PSScriptRoot 还是空的，Join-Path 会拿到空串而报错。
    [string]$Source = '',
    [switch]$NoTask
)

$ErrorActionPreference = 'Stop'
$TaskName = 'refwatch'
if (-not $Source) {
    $Source = if ($PSScriptRoot) { $PSScriptRoot }
              else { Split-Path -Parent $MyInvocation.MyCommand.Path }
}

function Info([string]$m) { Write-Host "  $m" }
function Step([string]$m) { Write-Host "`n== $m" -ForegroundColor Cyan }
function Fail([string]$m) { Write-Host "  [FAIL] $m" -ForegroundColor Red; exit 1 }
function Ok([string]$m)   { Write-Host "  [OK] $m" -ForegroundColor Green }

Step "0/6 环境检查"
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Fail "找不到 git，请先安装 Git for Windows" }
Info "git: $((Get-Command git).Source)"

$python = Join-Path $env:LOCALAPPDATA 'Python\bin\python.exe'
$pythonw = Join-Path $env:LOCALAPPDATA 'Python\bin\pythonw.exe'
if (-not (Test-Path $python)) {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if (-not $cmd) { Fail "找不到 python，请先安装 Python 3.9+" }
    $python = $cmd.Source
    $pythonw = Join-Path (Split-Path $python) 'pythonw.exe'
}
Info "python : $python"
if (Test-Path $pythonw) {
    Info "pythonw: $pythonw（计划任务用它，GUI 子系统，自己没有控制台）"
} else {
    $pythonw = $python
    Write-Warning "没有 pythonw.exe：计划任务会每轮闪一次控制台窗口（git 子进程另有防护）"
}

$programFiles = @('watch.py', 'qq_notify.py', 'channels.py', 'settings.py', 'i18n.py', 'scheduler.py', 'panel.py', 'tray.py')
foreach ($name in $programFiles) {
    if (-not (Test-Path (Join-Path $Source $name))) { Fail "缺少源文件: $(Join-Path $Source $name)" }
}
$assetFiles = @('refwatch.ico', 'refwatch.png') |
    ForEach-Object { Join-Path $Source ('assets\' + $_) } |
    Where-Object { Test-Path $_ }
$docFiles = @('README.md', 'README.zh-CN.md', 'LICENSE') |
    ForEach-Object { Join-Path $Source $_ } |
    Where-Object { Test-Path $_ }
$exampleConfig = Join-Path $Source 'config.example.json'
if (-not (Test-Path $exampleConfig)) { Fail "缺少源文件: $exampleConfig" }
Ok "源文件齐备（$Source）"

Step "1/6 创建运行目录"
if (Test-Path $Dest) { Info "$Dest 已存在，只覆盖程序文件" }
New-Item -ItemType Directory -Force -Path $Dest, (Join-Path $Dest 'logs') | Out-Null
Ok $Dest

Step "2/6 拷贝程序文件"
foreach ($name in $programFiles) { Copy-Item (Join-Path $Source $name) $Dest -Force }
foreach ($doc in $docFiles) { Copy-Item $doc $Dest -Force }
if ($assetFiles) {
    New-Item -ItemType Directory -Force -Path (Join-Path $Dest 'assets') | Out-Null
    foreach ($asset in $assetFiles) { Copy-Item $asset (Join-Path $Dest 'assets') -Force }
}
Copy-Item $exampleConfig $Dest -Force
Ok "watch.py / 托盘模块（settings / i18n / scheduler / panel / tray）/ assets / 文档 / config.example.json"

Step "3/6 配置文件（绝不覆盖已有配置）"
$configPath = Join-Path $Dest 'config.json'
$legacyPath = Join-Path $Dest 'watch.json'
if (Test-Path $configPath) {
    Info "config.json 已存在，保留不动"
} elseif (Test-Path $legacyPath) {
    Move-Item $legacyPath $configPath
    Ok "旧的 watch.json 已改名为 config.json（内容未改）"
} else {
    Copy-Item (Join-Path $Source 'config.example.json') $configPath
    Ok "已从 config.example.json 生成 config.json —— 记得填 repos 和 openid"
}

$envPath = Join-Path $Dest '.env'
if (Test-Path $envPath) {
    Info ".env 已存在，保留不动"
} elseif ($env:QQBOT_APPID -and $env:QQBOT_SECRET) {
    Info "凭据来自环境变量，无需 .env"
} else {
    Write-Host "  提醒：把 QQBOT_APPID / QQBOT_SECRET 写进 $envPath（一行一个 KEY=VALUE）" -ForegroundColor Yellow
}

# 把 -Minutes 同步进 config.json 的 interval_minutes：计划任务的触发器间隔和托盘读的这个键
# 必须是同一个数，否则「设置」里显示的和实际跑的对不上。
# 注意两个 PS 陷阱：5.1 的 ConvertTo-Json 默认 -Depth 2 会截断，Set-Content -Encoding UTF8
# 会写 BOM（Python 侧 json.loads 会因此失败）—— 所以显式 -Depth 10 + 无 BOM 写回。
if (Test-Path $configPath) {
    try {
        $raw = Get-Content $configPath -Raw -Encoding UTF8
        $cfg = $raw | ConvertFrom-Json
        if ($cfg.interval_minutes -ne $Minutes) {
            $cfg | Add-Member -NotePropertyName interval_minutes -NotePropertyValue $Minutes -Force
            $json = $cfg | ConvertTo-Json -Depth 10
            [System.IO.File]::WriteAllText($configPath, ($json + "`n"), (New-Object System.Text.UTF8Encoding($false)))
            Ok "config.json 的 interval_minutes 已同步为 $Minutes 分钟"
        } else {
            Info "interval_minutes 已经是 $Minutes"
        }
    } catch {
        Write-Warning "无法把 -Minutes 写进 config.json（文件保持原样）：$($_.Exception.Message)"
    }
}

Step "4/6 语法检查"
foreach ($name in $programFiles) {
    & $python -X utf8 -m py_compile (Join-Path $Dest $name)
    if ($LASTEXITCODE -ne 0) { Fail "$name 语法检查失败" }
}
Ok "py_compile 通过"

Step "5/6 建立基线（首次静默，不会把历史提交推给你）"
$watchPy = Join-Path $Dest 'watch.py'
if (Test-Path (Join-Path $Dest 'state.json')) {
    # 已有状态：只做一次 dry-run 启动检查，不推进状态、不发消息。
    & $python -X utf8 $watchPy --root $Dest --dry-run
    if ($LASTEXITCODE -ne 0) { Fail "dry-run 失败，先看上面的输出" }
    Ok "已有 state.json，dry-run 通过（未改动状态）"
} else {
    & $python -X utf8 $watchPy --root $Dest
    if ($LASTEXITCODE -ne 0) { Fail "首轮运行失败，先看上面的输出" }
    if (-not (Test-Path (Join-Path $Dest 'state.json'))) { Fail "state.json 未生成" }
    Ok "state.json 已生成"
}

if ($NoTask) {
    Write-Host "`n完成（-NoTask：未注册计划任务）。手动运行：" -ForegroundColor Yellow
    Write-Host "  `"$python`" -X utf8 `"$watchPy`" --root `"$Dest`"" -ForegroundColor Yellow
    exit 0
}

Step "6/6 注册计划任务（每 $Minutes 分钟）"
$action = New-ScheduledTaskAction -Execute $pythonw `
    -Argument ('-X utf8 "{0}" --root "{1}"' -f $watchPy, $Dest) `
    -WorkingDirectory $Dest
# 用 9999 天而不是 3650 天：后者十年后会静默停跑。
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes $Minutes) `
    -RepetitionDuration (New-TimeSpan -Days 9999)
# -Description 属于 Register-ScheduledTask，不在 New-ScheduledTaskSettingsSet 上。
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 15) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

$registered = $false
$schtasksTr = '"{0}" -X utf8 "{1}" --root "{2}"' -f $pythonw, $watchPy, $Dest
try {
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
        -Description 'refwatch：定时 fetch GitHub 仓库并推送新提交' -Force | Out-Null
    $registered = $true
    Ok "Register-ScheduledTask 注册成功"
} catch {
    Write-Warning "Register-ScheduledTask 失败：$($_.Exception.Message)"
    Info "回退到 schtasks…"
    schtasks /create /tn $TaskName /tr $schtasksTr /sc minute /mo $Minutes /f
    if ($LASTEXITCODE -eq 0) { $registered = $true; Ok "schtasks 注册成功" }
}

if (-not $registered) {
    Write-Host "`n任务注册失败（大概率需要管理员权限）。请用**管理员** PowerShell 执行：" -ForegroundColor Yellow
    Write-Host ("  schtasks /create /tn {0} /tr '{1}' /sc minute /mo {2} /f" -f $TaskName, $schtasksTr, $Minutes)
    Write-Host "程序与基线已经就位，注册任务这一步随时可以补做。"
    exit 2
}

try {
    $task = Get-ScheduledTask -TaskName $TaskName
    $repeat = $task.Triggers[0].Repetition
    Info "触发器: Interval=$($repeat.Interval) Duration=$($repeat.Duration)"
    if (-not $repeat.Interval) { Write-Warning "触发器没有重复间隔，请检查任务属性" }
    Get-ScheduledTaskInfo -TaskName $TaskName |
        Select-Object TaskName, LastRunTime, LastTaskResult, NextRunTime | Format-List
} catch {
    Write-Warning "任务已注册，但读取任务信息失败：$($_.Exception.Message)"
}
Ok "计划任务 $TaskName 已注册"

Write-Host "`n全部完成。" -ForegroundColor Green
Write-Host @"

两种运行形态，二选一（同时用只会在 .lock 上互相跳过）：

  A) 托盘应用（推荐，Windows 上的默认形态）
       "$pythonw" -X utf8 "$Dest\tray.py"
     图标进通知区域；右键有「设置…」「立即运行一轮」「预览（不发送）」。
     想让它登录就自启：托盘菜单里勾「开机自启」，或直接用 Inno 安装包（自启可勾选）。
     用了托盘之后，把下面的计划任务移除，避免两套调度抢锁。

  B) 计划任务（无头 / 服务器 / 不想常驻托盘）
     已按上一步注册，每 $Minutes 分钟跑一次。

验证清单：
  1) 立刻跑一次：   Start-ScheduledTask -TaskName '$TaskName'
                    Get-ScheduledTaskInfo -TaskName '$TaskName' | Select LastRunTime,LastTaskResult
  2) 看日志：       Get-Content '$Dest\logs\watch-*.log' -Tail 20
  3) 确认不再闪窗（Windows）：
                    python tools\window_probe.py -- "$pythonw" -X utf8 "$watchPy" --root "$Dest" --dry-run
                    应当报告 0 个新控制台窗口；有则是回归，见 docs/adr/0003。
  4) 卸载任务：     Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false
"@
