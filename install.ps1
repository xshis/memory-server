# Portable installer for Windows.
# Windows PowerShell 5.1 在 Stop 下会把原生程序的 stderr（pip 警告、ollama 进度）当致命错误，
# 一条 pip 报错就能炸断整个安装；统一用 Continue，关键步骤显式检查 $LASTEXITCODE。
$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot

Write-Host "== 本地知识库助手 · 安装 =="
Write-Host "目录：$PWD"

function Test-PythonExe {
    param([string]$Exe, [string[]]$PrefixArgs = @())
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    # 只接受 3.10 ~ 3.12：fastembed/onnxruntime 在 3.13+ 上经常没有可用 wheel（3.15 必炸）
    & $Exe @PrefixArgs -c "import sys; v=sys.version_info; raise SystemExit(0 if (3,10) <= (v[0],v[1]) < (3,13) else 1)" 2>$null | Out-Null
    $ok = ($LASTEXITCODE -eq 0)
    $ErrorActionPreference = $prev
    return $ok
}

function Find-Python {
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $found = $null
    # 1) PATH 里的 py 启动器 / python（跳过微软商店占位别名）；只认 3.10~3.12，3.12 优先
    foreach ($candidate in @("py -3.12", "py -3.11", "py -3.10", "python")) {
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
                    ForEach-Object { $_.FullName }
            }
        }
        # 3.10~3.12 优先，3.13+ 排后（二进制依赖兼容性更稳）
        $preferred = @($dirs | Where-Object { $_ -match 'Python3(1[0-2])$' })
        $others = @($dirs | Where-Object { $_ -notmatch 'Python3(1[0-2])$' } | Sort-Object -Descending)
        $dirs = $preferred + $others
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
        Write-Host "未检测到 Python 3.10+，正在用 winget 安装 Python 3.12（约 1~2 分钟）..."
        & winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements --silent 2>$null
        Write-Host "winget 退出码：$LASTEXITCODE"
        $python = Find-Python
        if ($python) {
            Write-Host "检测到新安装的 Python：$($python.Exe)"
        } else {
            Write-Host "  [警告] winget 执行完成，但未找到 Python。"
            Write-Host "        请稍后重新运行；或手动安装：https://www.python.org/downloads/"
        }
    } else {
        Write-Host "机器上没有 winget（老版本 Windows / 精简系统），无法自动安装。"
    }
}
if (-not $python) {
    Write-Host "未找到可用的 Python 3.10~3.12。请安装 Python 3.12：https://www.python.org/downloads/"
    Write-Host "（机器上如果装了 3.13 或更新版本，fastembed 等组件暂无对应依赖包，请另装 3.12）"
    Write-Host "安装时勾选 Add Python to PATH，然后重新运行本脚本。"
    exit 1
}

Write-Host "Python：$($python.Exe) $($python.Prefix -join ' ')"
Ensure-PythonOnPath -Python $python

Write-Host ""
Write-Host "检查运行环境"
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
    $continue = Read-Host "网络不通，是否继续？[y/N]"
    if ($continue -ne "y" -and $continue -ne "Y") { exit 1 }
}

$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (Test-Path $py) {
    $venvVer = & $py -c "import sys; print(str(sys.version_info[0]) + '.' + str(sys.version_info[1]))" 2>$null
    if ($venvVer -notmatch '^3\.(10|11|12)$') {
        Write-Host "现有 .venv 是 Python $venvVer，版本不兼容（需要 3.10~3.12），删除重建 ..."
        Remove-Item -Recurse -Force (Join-Path $PSScriptRoot ".venv")
        if (Test-Path ".venv") {
            Write-Host "无法删除旧 .venv（可能被占用），请关闭相关窗口/程序后重新运行。"
            exit 1
        }
    }
}
if (-not (Test-Path ".venv")) {
    Write-Host "创建虚拟环境 .venv ..."
    & $python.Exe @($python.Prefix + @("-m", "venv", ".venv"))
}
if (-not (Test-Path $py)) {
    Write-Host "虚拟环境创建失败：$py 不存在"
    exit 1
}
Write-Host "  venv Python：$(& $py --version 2>&1)"

$pipCheck = & $py -m pip --version 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "初始化 pip ..."
    & $py -m ensurepip --upgrade 2>$null
}

New-Item -ItemType Directory -Force -Path (Join-Path $PSScriptRoot "logs") | Out-Null
$useWheels = (Test-Path (Join-Path $PSScriptRoot "wheels")) -and (Test-Path (Join-Path $PSScriptRoot "constraints-win312.txt"))
$offline = @()
if ($useWheels) { $offline = @("--no-index", "--find-links", "wheels", "-c", "constraints-win312.txt") }
Write-Host "安装基础依赖 ..."
& $py -m pip install --upgrade pip
$baseOk = $false
if ($useWheels) {
    Write-Host "使用包内依赖包安装（离线，版本锁定）..."
    & $py -m pip install @offline setuptools wheel
    & $py -m pip install @offline --no-build-isolation -e .
    if ($LASTEXITCODE -eq 0) { $baseOk = $true }
    else { Write-Host "本地依赖包不完整，改用网络安装 ..." }
}
if (-not $baseOk) {
    & $py -m pip install -e .
    if ($LASTEXITCODE -ne 0) {
        Write-Host "基础依赖安装失败（pip 退出码 $LASTEXITCODE）。"
        Write-Host "请检查网络（pypi.org）和 Python 版本。"
        exit 1
    }
}

if ($env:KB_WITH_EMBED -ne "0") {
    Write-Host "安装语义检索依赖 fastembed ..."
    $embedLog = Join-Path $PSScriptRoot "logs\pip-embed.log"
    "=== python: $(& $py --version 2>&1) ===" | Out-File -FilePath $embedLog -Encoding utf8
    "=== pip: $(& $py -m pip --version 2>&1) ===" | Out-File -FilePath $embedLog -Encoding utf8 -Append
    "=== installed (相关): $(& $py -m pip list 2>&1 | Select-String -Pattern 'numpy|onnx|pillow|token|httpx|protobuf|hugging|loguru|mmh3|tqdm|requests' | Out-String) ===" | Out-File -FilePath $embedLog -Encoding utf8 -Append
    $embedOk = $false
    if ($useWheels) {
        & $py -m pip install @offline "fastembed>=0.2.7" 2>&1 | Tee-Object -FilePath $embedLog -Append
        if ($LASTEXITCODE -eq 0) { $embedOk = $true }
    }
    if (-not $embedOk) {
        if ($useWheels) { Write-Host "本地依赖包不完整，改用网络安装 ..." }
        & $py -m pip install "fastembed>=0.2.7" 2>&1 | Tee-Object -FilePath $embedLog -Append
        if ($LASTEXITCODE -eq 0) { $embedOk = $true }
    }
    if ($embedOk) {
        Write-Host "  语义检索依赖安装完成。"
    } else {
        Write-Host "  [警告] fastembed 安装失败，语义检索不可用（关键词搜索正常）。"
        Write-Host "  日志：logs\pip-embed.log；可重试：$py -m pip install fastembed"
    }
}

if ($env:KB_WITH_DOCS -eq "1") {
    Write-Host "安装办公文档解析依赖 ..."
    $ok = $false
    if ($useWheels) { & $py -m pip install @offline -e ".[docs]"; if ($LASTEXITCODE -eq 0) { $ok = $true } }
    if (-not $ok) { & $py -m pip install -e ".[docs]"; if ($LASTEXITCODE -eq 0) { $ok = $true } }
    if (-not $ok) { Write-Host "  [警告] 文档解析依赖安装失败，PDF/Office 解析不可用。" }
}

if ($env:KB_WITH_VISION -eq "1") {
    Write-Host "安装图片整理依赖（OpenCV）..."
    $ok = $false
    if ($useWheels) { & $py -m pip install @offline -e ".[vision]"; if ($LASTEXITCODE -eq 0) { $ok = $true } }
    if (-not $ok) { & $py -m pip install -e ".[vision]"; if ($LASTEXITCODE -eq 0) { $ok = $true } }
    if (-not $ok) { Write-Host "  [警告] OpenCV 安装失败，图片归类不可用。" }
}

if ($env:KB_WITH_FEISHU -eq "1") {
    Write-Host "安装飞书机器人依赖 ..."
    $ok = $false
    if ($useWheels) { & $py -m pip install @offline -e ".[feishu]"; if ($LASTEXITCODE -eq 0) { $ok = $true } }
    if (-not $ok) { & $py -m pip install -e ".[feishu]"; if ($LASTEXITCODE -eq 0) { $ok = $true } }
    if (-not $ok) { Write-Host "  [警告] 飞书依赖安装失败，机器人不可用。" }
}

Write-Host ""
Write-Host "本地模型（可选，不装也能正常搜索）"
Write-Host "  1) qwen2.5:3b   推荐，约 1.9GB，速度和质量平衡"
Write-Host "  2) qwen2.5:1.5b 更快，约 1GB，质量略低"
Write-Host "  3) qwen2.5:7b   更强，约 4.7GB，CPU 上明显更慢"
Write-Host "  4) 不安装"
Write-Host "  下载参考：100Mbps 约 3~6 分钟"
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
        & $py -m kb model install --name $modelName
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
& $py -m kb doctor

Write-Host ""
Write-Host "完成。下一步："
Write-Host "  .\.venv\Scripts\Activate.ps1"
Write-Host "  kb scope"
Write-Host "  kb index"
Write-Host "  kb search `"关键词`""
Write-Host "  kb map --open"
Write-Host "  kb mcp --http"
