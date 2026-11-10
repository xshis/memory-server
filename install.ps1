# Portable installer for Windows.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "== 本地知识库助手 · 安装 =="
Write-Host "目录：$PWD"

function Test-PythonExe {
    param([string]$Exe, [string[]]$PrefixArgs = @())
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & $Exe @PrefixArgs -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" 2>$null | Out-Null
    $ok = ($LASTEXITCODE -eq 0)
    $ErrorActionPreference = $prev
    return $ok
}

function Find-Python {
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $found = $null
    # 1) PATH 里的 py 启动器 / python（跳过微软商店占位别名）
    foreach ($candidate in @("py -3.13", "py -3.12", "py -3.11", "py -3.10", "python")) {
        $parts = $candidate.Split(" ")
        $exe = $parts[0]
        $prefix = @()
        if ($parts.Length -gt 1) { $prefix = $parts[1..($parts.Length - 1)] }
        foreach ($cmd in (Get-Command $exe -All -ErrorAction SilentlyContinue)) {
            if (-not $cmd.Source) { continue }
            if ($cmd.Source -like "*\WindowsApps\*") { continue }
            if (Test-PythonExe -Exe $cmd.Source -PrefixArgs $prefix) {
                $found = @{ Exe = $cmd.Source; Prefix = $prefix }
                break
            }
        }
        if ($found) { break }
    }
    # 2) 直接扫常见安装目录：不依赖 PATH（winget 刚装完、当前窗口 PATH 没刷新也能找到）
    if (-not $found) {
        $dirs = @()
        foreach ($base in @("$env:LOCALAPPDATA\Programs\Python", "C:\Program Files", "C:\")) {
            if ($base -and (Test-Path $base)) {
                $dirs += Get-ChildItem $base -Directory -ErrorAction SilentlyContinue |
                    Where-Object { $_.Name -like "Python3*" } |
                    Sort-Object Name -Descending |
                    ForEach-Object { $_.FullName }
            }
        }
        if (Test-Path "C:\Windows\py.exe") { $dirs += "C:\Windows\py.exe" }
        foreach ($dir in $dirs) {
            $exe = if ($dir -like "*.exe") { $dir } else { Join-Path $dir "python.exe" }
            if ((Test-Path $exe) -and (Test-PythonExe -Exe $exe)) {
                $found = @{ Exe = $exe; Prefix = @() }
                break
            }
        }
    }
    $ErrorActionPreference = $prevEAP
    return $found
}

function Ensure-PythonOnPath {
    param($Python)
    $target = ""
    if ($Python.Exe -like "*\python.exe") {
        $target = Split-Path $Python.Exe -Parent
    } else {
        # py 启动器：问它真正的解释器路径，再把那个目录补进 PATH
        try {
            $real = (& $Python.Exe @($Python.Prefix) -c "import sys; print(sys.executable)" 2>$null | Select-Object -First 1)
            if ($real -and (Test-Path $real)) { $target = Split-Path $real -Parent }
        } catch { }
    }
    if (-not $target) { return }
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if (-not $userPath) { $userPath = "" }
    if ($userPath -notlike "*$target*") {
        [Environment]::SetEnvironmentVariable("Path", "$target;$userPath", "User")
        Write-Host "  已把 $target 写入用户 PATH（新开的命令行窗口可直接用 python）"
    }
    if (-not (($env:Path -split ";") -contains $target)) {
        $env:Path = "$target;$env:Path"
        Write-Host "  当前窗口已生效：python 命令可直接使用"
    }
}

$python = Find-Python
if (-not $python) {
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($winget) {
        Write-Host "未检测到 Python 3.10+，尝试用 winget 自动安装 Python 3.12（约 1~2 分钟，下面会显示安装进度）..."
        & winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements --silent 2>$null
        Write-Host "winget 退出码：$LASTEXITCODE"
        $python = Find-Python
        if ($python) {
            Write-Host "检测到新安装的 Python：$($python.Exe)"
        } else {
            Write-Host "[警告] winget 执行完但没找到 Python：可能被系统策略拦截、需要管理员权限，或安装尚未完成。"
            Write-Host "        可稍等 1 分钟重新运行本脚本；或手动安装：https://www.python.org/downloads/"
        }
    } else {
        Write-Host "机器上没有 winget（老版本 Windows / 精简系统），无法自动安装。"
    }
}
if (-not $python) {
    Write-Host "未找到 Python 3.10+。请安装 Python 3.12：https://www.python.org/downloads/"
    Write-Host "安装时勾选 Add Python to PATH，然后重新运行本脚本。"
    exit 1
}

Write-Host "Python：$($python.Exe) $($python.Prefix -join ' ')"
Ensure-PythonOnPath -Python $python

Write-Host ""
Write-Host "第 0 步 · 环境预检（有问题会先提示，不动任何文件）"
# 网络：pypi.org 443 可达性（依赖安装的前提）
$networkOk = $false
try {
    $tcp = New-Object System.Net.Sockets.TcpClient
    $connect = $tcp.BeginConnect("pypi.org", 443, $null, $null)
    $networkOk = $connect.AsyncWaitHandle.WaitOne(6000, $false) -and $tcp.Connected
    $tcp.Close()
} catch { }
if ($networkOk) {
    Write-Host "  网络（pypi.org:443）：OK"
} else {
    Write-Host "  [警告] 连不上 pypi.org——依赖安装会失败。请先解决网络/代理，再重新运行本脚本。"
}
# 磁盘：所在盘剩余空间 ≥ 5GB（依赖约 1GB + 本地模型约 2~5GB）
$driveName = (Get-Item $PSScriptRoot).PSDrive.Name
$freeGB = [math]::Round((Get-PSDrive $driveName).Free / 1GB, 1)
if ($freeGB -ge 5) {
    Write-Host "  磁盘（${driveName}: 剩 ${freeGB}GB）：OK"
} else {
    Write-Host "  [警告] ${driveName}: 盘只剩 ${freeGB}GB，建议至少 5GB 再装（可跳过本地模型）。"
}
if (-not $networkOk) {
    $continue = Read-Host "网络不通仍要继续吗？[y/N]"
    if ($continue -ne "y" -and $continue -ne "Y") { exit 1 }
}

if (-not (Test-Path ".venv")) {
    Write-Host "创建虚拟环境 .venv ..."
    & $python.Exe @($python.Prefix + @("-m", "venv", ".venv"))
}

$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "虚拟环境创建失败：$py 不存在"
    exit 1
}

$pipCheck = & $py -m pip --version 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "初始化 pip ..."
    & $py -m ensurepip --upgrade 2>$null
}

Write-Host "安装依赖 ..."
& $py -m pip install --upgrade pip 2>$null | Out-Null
if ($env:KB_WITH_EMBED -ne "0") {
    & $py -m pip install -e ".[embed]" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  [警告] 语义检索依赖（fastembed）安装失败，退回基础安装（仅关键词搜索）。"
        Write-Host "  可稍后重试：$py -m pip install -e `".[embed]`""
        & $py -m pip install -e . 2>$null
    }
} else {
    & $py -m pip install -e . 2>$null
}
if ($LASTEXITCODE -ne 0) {
    Write-Host "依赖安装失败（pip 退出码 $LASTEXITCODE）。常见原因：网络不通 pypi.org，可配置镜像后重试。"
    exit 1
}

if ($env:KB_WITH_DOCS -eq "1") {
    Write-Host "安装办公文档解析依赖 ..."
    & $py -m pip install -e ".[docs]" 2>$null
}

if ($env:KB_WITH_VISION -eq "1") {
    Write-Host "安装图片整理依赖（OpenCV）..."
    & $py -m pip install -e ".[vision]" 2>$null
}

if ($env:KB_WITH_FEISHU -eq "1") {
    Write-Host "安装飞书机器人依赖 ..."
    & $py -m pip install -e ".[feishu]" 2>$null
}

Write-Host ""
Write-Host "本地模型（可选，不装也能正常搜索）"
Write-Host "  1) qwen2.5:3b   推荐，约 1.9GB，速度和质量平衡"
Write-Host "  2) qwen2.5:1.5b 更快，约 1GB，质量略低"
Write-Host "  3) qwen2.5:7b   更强，约 4.7GB，CPU 上明显更慢"
Write-Host "  4) 不安装"
Write-Host "  提示：100Mbps 下载约 3~6 分钟；慢网 10~30 分钟。资料不出本机。"
$modelName = if ($env:KB_OLLAMA_MODEL) { $env:KB_OLLAMA_MODEL } else { "qwen2.5:3b" }
$ollamaChoice = $env:KB_WITH_OLLAMA
if (-not $ollamaChoice -and $env:KB_OLLAMA_MODEL) { $ollamaChoice = "1" }
if (-not $ollamaChoice) {
    if ([Console]::IsInputRedirected) {
        $ollamaChoice = "0"
    } else {
        $pick = Read-Host "请选择 [1]"
        if (-not $pick) { $pick = "1" }
        switch ($pick) {
            "2" { $modelName = "qwen2.5:1.5b"; $ollamaChoice = "1" }
            "3" { $modelName = "qwen2.5:7b"; $ollamaChoice = "1" }
            "4" { $ollamaChoice = "0" }
            default { $modelName = "qwen2.5:3b"; $ollamaChoice = "1" }
        }
    }
}
if ($ollamaChoice -eq "1") {
    $ollama = Get-Command ollama -ErrorAction SilentlyContinue
    if ($ollama) {
        Write-Host "下载模型 $modelName（首次会启动 Ollama，可能需要几分钟）..."
        & $py -m kb model install --name $modelName 2>$null
    } else {
        Write-Host "  未检测到 Ollama。先安装：https://ollama.com/download"
        Write-Host "  安装后运行：kb model install --name $modelName"
        Start-Process "https://ollama.com/download"
    }
} else {
    Write-Host "  已跳过；以后需要可运行 kb model install --name qwen2.5:3b"
}

Write-Host ""
Write-Host "运行环境自检 ..."
& $py -m kb doctor 2>$null

Write-Host ""
Write-Host "完成。下一步："
Write-Host "  .\.venv\Scripts\Activate.ps1"
Write-Host "  kb scope"
Write-Host "  kb index"
Write-Host "  kb search `"关键词`""
Write-Host "  kb map --open"
Write-Host "  kb mcp --http"
