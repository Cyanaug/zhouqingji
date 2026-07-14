# 读诗剧场 启动器：确保服务在跑，再打开网页
$port = 8737
$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$serverPath = Join-Path $scriptRoot 'src\server.py'
$listening = $null -ne (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
if (-not $listening) {
    $py = 'python'
    Start-Process -FilePath $py -ArgumentList "`"$serverPath`"" -WindowStyle Hidden
    Start-Sleep -Seconds 2
}
Start-Process "http://localhost:$port"