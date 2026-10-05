$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonCommand = Get-Command python -ErrorAction Stop
$pythonExe = $pythonCommand.Source
$expected = @(
    @{ Port = 9090; Module = 'app.server' },
    @{ Port = 9091; Module = 'app.server' },
    @{ Port = 8080; Module = 'app.web_client' }
)

function Get-LoopbackListenerPid([int]$Port) {
    $pattern = '^\s*TCP\s+127\.0\.0\.1:' + $Port + '\s+0\.0\.0\.0:0\s+LISTENING\s+(\d+)\s*$'
    $matchesForPort = @(
        netstat -ano -p tcp | ForEach-Object {
            if ($_ -match $pattern) { [int]$Matches[1] }
        }
    )
    if ($matchesForPort.Count -gt 1) { throw "More than one loopback listener was found on port $Port." }
    if ($matchesForPort.Count -eq 1) { return $matchesForPort[0] }
    return $null
}

# Validate every target before stopping any process.
$toStop = @()
foreach ($entry in $expected) {
    $ownerPid = Get-LoopbackListenerPid $entry.Port
    if ($null -eq $ownerPid) { continue }
    $processInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $ownerPid"
    if ($null -eq $processInfo) { throw "Could not inspect process $ownerPid on port $($entry.Port)." }
    $actualExe = [System.IO.Path]::GetFullPath([string]$processInfo.ExecutablePath)
    $requiredModule = '(?i)(^|\s)-m\s+' + [regex]::Escape($entry.Module) + '(\s|$)'
    $requiredPort = '(?i)(^|\s)--port\s+' + $entry.Port + '(\s|$)'
    if (-not [string]::Equals($actualExe, $pythonExe, [System.StringComparison]::OrdinalIgnoreCase) -or
        [string]$processInfo.CommandLine -notmatch $requiredModule -or
        [string]$processInfo.CommandLine -notmatch $requiredPort) {
        throw "Port $($entry.Port) belongs to a process other than the verified course $($entry.Module). Nothing was stopped."
    }
    $toStop += [pscustomobject]@{ Port = $entry.Port; Pid = $ownerPid; Module = $entry.Module }
}

foreach ($target in $toStop) {
    Write-Host "Stopping course $($target.Module) on TCP $($target.Port) (PID $($target.Pid))."
    Stop-Process -Id $target.Pid -ErrorAction Stop
}
Start-Sleep -Milliseconds 400
foreach ($target in $toStop) {
    if ($null -ne (Get-LoopbackListenerPid $target.Port)) {
        throw "TCP $($target.Port) is still listening; the new server was not started."
    }
}

& (Join-Path $PSScriptRoot 'start_local_live.ps1')
if ($LASTEXITCODE -ne 0) { throw 'Course application startup verification failed.' }
Write-Host "Course application restarted from $projectRoot."
