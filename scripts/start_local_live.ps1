$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $PSScriptRoot
$installRoot = Join-Path $env:LOCALAPPDATA 'DBMSCourseMariaDB-3307'
$pythonCommand = Get-Command python -ErrorAction Stop
$pythonExe = $pythonCommand.Source
$mariaBin = Join-Path $installRoot 'mariadb-11.8.9-winx64\bin\mariadbd.exe'
$mariaConfig = Join-Path $installRoot 'data\my.ini'
$monitorSecret = Join-Path $installRoot 'secrets\monitor-password.txt'
$logsDir = Join-Path $installRoot 'logs'

foreach ($required in @($pythonExe, $mariaBin, $mariaConfig, $monitorSecret)) {
    if (-not (Test-Path -LiteralPath $required)) { throw "Required file is missing: $required" }
}
New-Item -ItemType Directory -Path $logsDir -Force | Out-Null

function Test-LocalPort([int]$Port) {
    $socket = [System.Net.Sockets.TcpClient]::new()
    try {
        $socket.Connect('127.0.0.1', $Port)
        return $true
    }
    catch { return $false }
    finally { $socket.Dispose() }
}

if (-not (Test-LocalPort 3307)) {
    Start-Process -FilePath $mariaBin `
        -ArgumentList @("--defaults-file=$mariaConfig", '--bind-address=127.0.0.1', '--console') `
        -WorkingDirectory (Split-Path $mariaBin) -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logsDir 'mariadb.out.log') `
        -RedirectStandardError (Join-Path $logsDir 'mariadb.err.log')
    Start-Sleep -Seconds 2
    if (-not (Test-LocalPort 3307)) { throw 'MariaDB did not start; inspect logs/mariadb.err.log.' }
}

if (-not (Test-LocalPort 9090)) {
    $env:PYTHONPATH = Join-Path $installRoot 'pylibs'
    $env:DB_HOST = '127.0.0.1'
    $env:DB_PORT = '3307'
    $env:DB_USER = 'course_monitor'
    $env:DB_NAME = 'course_network_demo'
    $env:DB_PASSWORD = Get-Content -LiteralPath $monitorSecret -Raw
    try {
        Start-Process -FilePath $pythonExe -ArgumentList '-B', '-m', 'app.server', '--host', '127.0.0.1', '--port', '9090' `
            -WorkingDirectory $projectRoot -WindowStyle Hidden `
            -RedirectStandardOutput (Join-Path $logsDir 'diagnostic-server.out.log') `
            -RedirectStandardError (Join-Path $logsDir 'diagnostic-server.err.log')
    }
    finally { Remove-Item Env:DB_PASSWORD -ErrorAction SilentlyContinue }
    Start-Sleep -Milliseconds 700
    if (-not (Test-LocalPort 9090)) { throw 'Diagnostic server did not start; inspect logs/diagnostic-server.err.log.' }
}

if (-not (Test-LocalPort 8080)) {
    Start-Process -FilePath $pythonExe -ArgumentList '-B', '-m', 'app.web_client', '--host', '127.0.0.1', '--port', '8080' `
        -WorkingDirectory $projectRoot -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logsDir 'web-client.out.log') `
        -RedirectStandardError (Join-Path $logsDir 'web-client.err.log')
    Start-Sleep -Milliseconds 700
    if (-not (Test-LocalPort 8080)) { throw 'Web client did not start; inspect logs/web-client.err.log.' }
}

Push-Location -LiteralPath $projectRoot
try {
    & $pythonExe -B -m scripts.check_presentation_demo --require-current-server
    $verificationExitCode = $LASTEXITCODE
}
finally { Pop-Location }
if ($verificationExitCode -ne 0) { throw 'Current course server verification failed. If 9090 is occupied by an older course server, stop that process and run this script again.' }

Write-Host 'MariaDB, TCP server, and web client are listening locally.'
Write-Host 'Open http://127.0.0.1:8080/ and start a diagnosis.'
Write-Host 'The server supports LIVE/NORMAL and all three clearly labelled DEMO states on TCP 9090.'
