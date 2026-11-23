$ErrorActionPreference = 'Stop'
$agentWebRoot = $PSScriptRoot
$settingsPath = Join-Path $agentWebRoot '.runtime\launch.json'
$settings = if (Test-Path -LiteralPath $settingsPath) { Get-Content -Raw -LiteralPath $settingsPath | ConvertFrom-Json } else { @{} }
$venvPython = Join-Path $agentWebRoot '.venv\Scripts\python.exe'
$agentPython = if ($env:ZHIHU_WEB_PYTHON) {
    $env:ZHIHU_WEB_PYTHON
} elseif ($settings.python) {
    $settings.python
} elseif (Test-Path -LiteralPath $venvPython) {
    $venvPython
} else {
    'python'
}
$frontendBuild = Join-Path $agentWebRoot 'web\.output\server\index.mjs'
if (-not (Test-Path -LiteralPath $frontendBuild)) { throw '请先在 web 目录运行 npm ci 和 npm run build。' }
$runner = [System.Diagnostics.ProcessStartInfo]::new()
$runner.FileName = $agentPython
$runner.WorkingDirectory = $agentWebRoot
$runner.UseShellExecute = $false
if (-not $env:ZHIHU_AGENT_ROOT -and $settings.agentRoot) {
    $runner.Environment['ZHIHU_AGENT_ROOT'] = $settings.agentRoot
}
if (-not $env:ZHIHU_WEB_COHORT -and $settings.cohort) {
    $runner.Environment['ZHIHU_WEB_COHORT'] = $settings.cohort
}
foreach ($argument in @('-B', '-m', 'backend.serve')) { $runner.ArgumentList.Add($argument) }
$api = [System.Diagnostics.Process]::Start($runner)
$env:NITRO_HOST = '127.0.0.1'
$env:NITRO_PORT = '3000'
try {
    $agentReady = $false
    $agentDeadline = [DateTimeOffset]::UtcNow.AddSeconds(15)
    while (-not $agentReady -and [DateTimeOffset]::UtcNow -lt $agentDeadline) {
        if ($api.HasExited) { throw "Python API 启动失败，错误码 $($api.ExitCode)" }
        try {
            $agentReady = (Invoke-RestMethod -Uri 'http://127.0.0.1:8765/health' -NoProxy -TimeoutSec 1).status -eq 'ok'
        } catch {
            Start-Sleep -Milliseconds 250
        }
    }
    if (-not $agentReady -or $api.HasExited) { throw 'Python API 未就绪，请检查上方日志。' }
    Write-Host '阅读工作台 http://127.0.0.1:3000'
    & node $frontendBuild
    if ($LASTEXITCODE -ne 0) { throw "Nuxt 退出，错误码 $LASTEXITCODE" }
} finally {
    if (-not $api.HasExited) { $api.Kill() }
    $api.Dispose()
}
