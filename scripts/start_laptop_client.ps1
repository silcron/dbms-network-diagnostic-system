param(
    [ValidateRange(1024, 65535)]
    [int]$WebPort = 8080
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonExe = $null
$pythonPrefix = @()
$localPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
$candidates = @()
if (Test-Path -LiteralPath $localPython) {
    $candidates += @{ Path = $localPython; Prefix = @() }
}
foreach ($name in @('python', 'py')) {
    $command = Get-Command $name -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($command) {
        $prefix = @()
        if ($name -eq 'py') { $prefix = @('-3.14') }
        $candidates += @{ Path = $command.Source; Prefix = $prefix }
    }
}
foreach ($candidate in $candidates) {
    $candidatePrefix = $candidate.Prefix
    & $candidate.Path @candidatePrefix -c 'import sys; sys.exit(0 if sys.version_info >= (3, 14, 3) else 1)' 2>$null
    if ($LASTEXITCODE -eq 0) {
        $pythonExe = $candidate.Path
        $pythonPrefix = $candidate.Prefix
        break
    }
}
if (-not $pythonExe) {
    throw 'Install Python 3.14.3 or newer, enable PATH, then run this launcher again.'
}

$listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $WebPort)
try {
    $listener.Start()
}
catch {
    throw "Local port $WebPort is unavailable. Run start-laptop-client.cmd -WebPort 8081 to use another port."
}
finally { $listener.Stop() }

Write-Host "Open http://127.0.0.1:$WebPort/ in your laptop browser."
Write-Host 'Set Server address to the desktop LAN IPv4 address and Port to 9092.'
Write-Host 'Select DEMO first. Keep this window open. Press Ctrl+C to stop.'
Push-Location -LiteralPath $projectRoot
try {
    & $pythonExe @pythonPrefix -B -m app.web_client --host 127.0.0.1 --port $WebPort
    if ($LASTEXITCODE -ne 0) { throw "Web client exited with code $LASTEXITCODE." }
}
finally { Pop-Location }
