# 昼青集·读诗剧场启动器：识别旧后台，确保浏览器连接当前代码。
$port = 8737
$expectedApiLevel = 3
$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$serverPath = Join-Path $scriptRoot 'src\server.py'
$baseUrl = "http://127.0.0.1:$port"
$expectedBuildId = (Get-FileHash -LiteralPath $serverPath -Algorithm SHA256).Hash.Substring(0, 16).ToLowerInvariant()

function Get-ZqRuntime {
    try {
        return Invoke-RestMethod -Uri "$baseUrl/api/runtime" -TimeoutSec 2
    } catch {
        return $null
    }
}

function Get-PortListeners {
    return @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
}

function Test-OwnServerProcess($processId) {
    $processInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $processId" -ErrorAction SilentlyContinue
    if (-not $processInfo -or $processInfo.Name -notmatch '^python(w)?\.exe$') { return $false }
    return ([string]$processInfo.CommandLine).IndexOf(
        $serverPath, [StringComparison]::OrdinalIgnoreCase) -ge 0
}

$listeners = Get-PortListeners
if ($listeners.Count -gt 0) {
    $runtime = Get-ZqRuntime
    $listenerIds = @($listeners | Select-Object -ExpandProperty OwningProcess -Unique)
    $ownServer = $listenerIds.Count -gt 0
    foreach ($listenerId in $listenerIds) {
        if (-not (Test-OwnServerProcess $listenerId)) { $ownServer = $false; break }
    }
    if (-not $ownServer) {
        Add-Type -AssemblyName PresentationFramework
        [System.Windows.MessageBox]::Show(
            "端口 $port 已被其他程序占用。昼青集没有关闭它；请检查端口占用后重试。",
            '昼青集未能启动', 'OK', 'Error') | Out-Null
        exit 1
    }
    if (-not $runtime -or $runtime.app -ne 'zhouqingji' -or
            [int]$runtime.author_api_level -ne $expectedApiLevel -or
            $runtime.build_id -ne $expectedBuildId) {
        Add-Type -AssemblyName PresentationFramework
        $answer = [System.Windows.MessageBox]::Show(
            "检测到旧版昼青集仍占用端口 $port。`n`n是否关闭这个旧后台并启动当前版本？",
            '昼青集需要重启后台', 'YesNo', 'Warning')
        if ($answer -ne 'Yes') { exit 1 }
        try {
            foreach ($listenerId in $listenerIds) {
                if (-not (Test-OwnServerProcess $listenerId)) { throw '端口占用进程已变化' }
                Stop-Process -Id $listenerId -ErrorAction Stop
            }
            for ($i = 0; $i -lt 20 -and (Get-PortListeners).Count -gt 0; $i++) {
                Start-Sleep -Milliseconds 150
            }
        } catch {
            [System.Windows.MessageBox]::Show(
                "无法关闭旧后台。请在任务管理器中结束占用端口 $port 的 Python 进程后重试。",
                '昼青集未能启动', 'OK', 'Error') | Out-Null
            exit 1
        }
    }
}

if ((Get-PortListeners).Count -eq 0) {
    Start-Process -FilePath 'python' -ArgumentList "`"$serverPath`"" -WindowStyle Hidden
}

$runtime = $null
for ($i = 0; $i -lt 24 -and -not $runtime; $i++) {
    Start-Sleep -Milliseconds 250
    $runtime = Get-ZqRuntime
}
if (-not $runtime -or $runtime.app -ne 'zhouqingji' -or
        [int]$runtime.author_api_level -ne $expectedApiLevel -or
        $runtime.build_id -ne $expectedBuildId) {
    Add-Type -AssemblyName PresentationFramework
    [System.Windows.MessageBox]::Show(
        '当前版本后台没有成功启动。请关闭残留的 Python 进程后重试。',
        '昼青集未能启动', 'OK', 'Error') | Out-Null
    exit 1
}

Start-Process "http://localhost:$port"
