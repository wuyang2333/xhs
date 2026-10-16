param(
    [ValidateSet("menu", "start", "stop", "status", "open")]
    [string]$Action = "menu"
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"
$logDirectory = Join-Path $projectRoot "local-artifacts\logs"
$stdoutLog = Join-Path $logDirectory "xhs-service.out.log"
$stderrLog = Join-Path $logDirectory "xhs-service.err.log"
$frontendUrl = "http://localhost:5173"
$healthUrl = "http://127.0.0.1:8000/api/health"
$servicePorts = @(8000, 5173)
$startupTimeoutSeconds = 90

function Get-PortProcess {
    param([int]$Port)

    Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
        Select-Object -First 1
}

function Test-Url {
    param([string]$Url)

    try {
        $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 5
        return $response.StatusCode -ge 200 -and $response.StatusCode -lt 400
    }
    catch {
        return $false
    }
}

function Show-ServiceStatus {
    $backendReady = Test-Url $healthUrl
    $frontendReady = Test-Url $frontendUrl

    Write-Host ""
    Write-Host "XHS 服务状态"
    Write-Host "  后端 8000：$(if ($backendReady) { '运行中' } else { '未运行' })"
    Write-Host "  前端 5173：$(if ($frontendReady) { '运行中' } else { '未运行' })"
    if ($frontendReady) {
        Write-Host "  地址：$frontendUrl"
    }
    Write-Host ""

    return $backendReady -and $frontendReady
}

function Start-XhsService {
    if ((Test-Url $healthUrl) -and (Test-Url $frontendUrl)) {
        Write-Host "XHS 已经在运行，正在打开浏览器……"
        Start-Process $frontendUrl
        return
    }

    $backendRunning = Test-Url $healthUrl
    $frontendRunning = Test-Url $frontendUrl
    if ($backendRunning -or $frontendRunning) {
        Write-Host "检测到部分服务正在运行，正在重启前后端……"
        Stop-XhsService
        Start-Sleep -Seconds 1
    }

    foreach ($port in $servicePorts) {
        $connection = Get-PortProcess $port
        if ($connection) {
            $process = Get-Process -Id $connection.OwningProcess -ErrorAction SilentlyContinue
            $processName = if ($process) { $process.ProcessName } else { "unknown process" }
            throw "端口 $port 已被 $processName 占用（PID $($connection.OwningProcess)），请先停止占用它的程序。"
        }
    }

    if (-not (Test-Path -LiteralPath $pythonPath)) {
        throw "找不到 Python 环境：$pythonPath。请先运行 install.bat。"
    }
    if (-not (Test-Path -LiteralPath (Join-Path $projectRoot "frontend\package.json"))) {
        throw "找不到 frontend\package.json，项目文件不完整。"
    }

    New-Item -ItemType Directory -Force -Path $logDirectory | Out-Null
    Remove-Item -LiteralPath $stdoutLog, $stderrLog -Force -ErrorAction SilentlyContinue

    Write-Host "正在后台启动 XHS，请稍候……"
    Start-Process `
        -FilePath $pythonPath `
        -ArgumentList @("main.py", "--host", "0.0.0.0", "--port", "8000", "--with-frontend", "--frontend-host", "0.0.0.0") `
        -WorkingDirectory $projectRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput $stdoutLog `
        -RedirectStandardError $stderrLog

    $deadline = (Get-Date).AddSeconds($startupTimeoutSeconds)
    $lastProgressAt = Get-Date
    do {
        Start-Sleep -Seconds 1
        $backendReady = Test-Url $healthUrl
        $frontendReady = Test-Url $frontendUrl
        if (-not ($backendReady -and $frontendReady) -and ((Get-Date) - $lastProgressAt).TotalSeconds -ge 10) {
            $backendText = if ($backendReady) { "后端已就绪" } else { "后端启动中" }
            $frontendText = if ($frontendReady) { "前端已就绪" } else { "前端启动中" }
            Write-Host "  $backendText，$frontendText……"
            $lastProgressAt = Get-Date
        }
    } while ((-not ($backendReady -and $frontendReady)) -and (Get-Date) -lt $deadline)

    if (-not ($backendReady -and $frontendReady)) {
        Write-Host "启动超时：服务在 $startupTimeoutSeconds 秒内没有全部就绪。" -ForegroundColor Red
        Write-Host "这通常是数据库迁移或首次启动较慢，不一定代表启动失败。" -ForegroundColor Yellow
        Write-Host "最近的后端错误日志："
        if (Test-Path -LiteralPath $stderrLog) {
            Get-Content -LiteralPath $stderrLog -Tail 20
        }
        throw "请稍后运行 XHS.bat 的'查看状态'，或检查日志目录：$logDirectory"
    }

    Write-Host "XHS 启动成功，正在打开 $frontendUrl"
    Start-Process $frontendUrl
}

function Stop-XhsService {
    $processIds = foreach ($port in $servicePorts) {
        $connection = Get-PortProcess $port
        if ($connection) {
            $connection.OwningProcess
        }
    }

    $processIds = @($processIds | Sort-Object -Unique)
    if ($processIds.Count -eq 0) {
        Write-Host "XHS 当前没有运行。"
        return
    }

    foreach ($processId in $processIds) {
        if (Get-Process -Id $processId -ErrorAction SilentlyContinue) {
            Start-Process `
                -FilePath "taskkill.exe" `
                -ArgumentList @("/PID", $processId, "/T", "/F") `
                -WindowStyle Hidden `
                -Wait
        }
    }

    Start-Sleep -Seconds 1
    Write-Host "XHS 已停止。"
}

function Open-XhsPage {
    if (-not (Test-Url $frontendUrl)) {
        throw "前端没有运行，请先启动 XHS。"
    }
    Start-Process $frontendUrl
}

function Show-Menu {
    Clear-Host
    Write-Host "=============================="
    Write-Host "        XHS 服务管理器"
    Write-Host "=============================="
    Show-ServiceStatus | Out-Null
    Write-Host "  1. 后台启动并打开浏览器"
    Write-Host "  2. 停止服务"
    Write-Host "  3. 查看状态"
    Write-Host "  4. 打开浏览器"
    Write-Host "  0. 退出"
    Write-Host ""

    switch (Read-Host "请选择操作") {
        "1" { Start-XhsService }
        "2" { Stop-XhsService }
        "3" { Show-ServiceStatus | Out-Null; Read-Host "按回车关闭" | Out-Null }
        "4" { Open-XhsPage }
        "0" { return }
        default { throw "无效的选项。"
        }
    }
}

try {
    switch ($Action) {
        "start" { Start-XhsService }
        "stop" { Stop-XhsService }
        "status" { Show-ServiceStatus | Out-Null }
        "open" { Open-XhsPage }
        default { Show-Menu }
    }
}
catch {
    Write-Host ""
    Write-Host "操作失败：$($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
