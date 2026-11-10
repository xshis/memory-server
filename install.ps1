# Portable installer for Windows.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "== 本地知识库助手 · 安装 =="
Write-Host "目录：$PWD"

$candidates = @("py -3.13", "py -3.12", "py -3.11", "py -3.10", "python")
$python = $null
foreach ($candidate in $candidates) {
    $parts = $candidate.Split(" ")
    $exe = $parts[0]
    $args = @()
    if ($parts.Length -gt 1) { $args = $parts[1..($parts.Length - 1)] }
    if (Get-Command $exe -ErrorAction SilentlyContinue) {
        $versionArgs = $args + @("-c", "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)")
        & $exe @versionArgs 2>$null
        if ($LASTEXITCODE -eq 0) {
            $python = $candidate
            break
        }
    }
}

if (-not $python) {
    Write-Host "未找到 Python 3.10+。请安装 Python 3.12：https://www.python.org/downloads/"
    Write-Host "安装时勾选 Add Python to PATH，然后重新运行本脚本。"
    exit 1
}

Write-Host "Python：$python"

if (-not (Test-Path ".venv")) {
    Write-Host "创建虚拟环境 .venv ..."
    $parts = $python.Split(" ")
    $exe = $parts[0]
    $args = @()
    if ($parts.Length -gt 1) { $args = $parts[1..($parts.Length - 1)] }
    & $exe @($args + @("-m", "venv", ".venv"))
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
& $py -m pip install --upgrade pip | Out-Null
if ($env:KB_WITH_EMBED -ne "0") {
    & $py -m pip install -e ".[embed]"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  [警告] 语义检索依赖（fastembed）安装失败，退回基础安装（仅关键词搜索）。"
        Write-Host "  可稍后重试：$py -m pip install -e `".[embed]`""
        & $py -m pip install -e .
    }
} else {
    & $py -m pip install -e .
}

if ($env:KB_WITH_DOCS -eq "1") {
    Write-Host "安装办公文档解析依赖 ..."
    & $py -m pip install -e ".[docs]"
}

if ($env:KB_WITH_VISION -eq "1") {
    Write-Host "安装图片整理依赖（OpenCV）..."
    & $py -m pip install -e ".[vision]"
}

if ($env:KB_WITH_FEISHU -eq "1") {
    Write-Host "安装飞书机器人依赖 ..."
    & $py -m pip install -e ".[feishu]"
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
