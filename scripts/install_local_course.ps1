$ErrorActionPreference = 'Stop'

# Run this script as the interactive Windows user. It creates only the course DB.
$projectRoot = Split-Path -Parent $PSScriptRoot
$installRoot = Join-Path $env:LOCALAPPDATA 'DBMSCourseMariaDB-3307'
$dbPort = 3307
$pythonExe = 'C:\Users\phili\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
$archiveUrl = 'https://mirror.math.princeton.edu/pub/mariadb/mariadb-11.8.9/winx64-packages/mariadb-11.8.9-winx64.zip'
$expectedSha256 = '830c46727d9278eae212ae3eca44eeb9e71b2a68704e95f344a64fba7b1963f5'
$archivePath = Join-Path $env:TEMP 'course-mariadb-11.8.9-winx64.zip'
$mariaBin = Join-Path $installRoot 'mariadb-11.8.9-winx64\bin\mariadbd.exe'
$installer = Join-Path $installRoot 'mariadb-11.8.9-winx64\bin\mariadb-install-db.exe'
$dataDir = Join-Path $installRoot 'data'
$secretsDir = Join-Path $installRoot 'secrets'
$pylibsDir = Join-Path $installRoot 'pylibs'
$logsDir = Join-Path $installRoot 'logs'

function Test-LocalPort([int]$Port) {
    $socket = [System.Net.Sockets.TcpClient]::new()
    try { $socket.Connect('127.0.0.1', $Port); return $true }
    catch { return $false }
    finally { $socket.Dispose() }
}

function New-Secret {
    $bytes = New-Object byte[] 24
    $generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $generator.GetBytes($bytes) }
    finally { $generator.Dispose() }
    return [System.BitConverter]::ToString($bytes).Replace('-', '').ToLowerInvariant()
}

if (-not (Test-Path -LiteralPath $pythonExe)) {
    throw "Course Python is not visible to this account: $pythonExe. No installation was changed."
}
if (Test-LocalPort $dbPort) {
    throw "TCP $dbPort is already in use. No installation was changed; inspect the existing listener first."
}
if (Test-Path -LiteralPath $installRoot) {
    throw "A course DB directory already exists: $installRoot. It was not overwritten."
}

Write-Host 'Downloading the official MariaDB 11.8.9 Windows ZIP (about 93 MB)...'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
Invoke-WebRequest -Uri $archiveUrl -OutFile $archivePath -UseBasicParsing
$actualSha256 = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actualSha256 -ne $expectedSha256) {
    throw 'MariaDB ZIP checksum mismatch. Nothing was installed.'
}

New-Item -ItemType Directory -Path $installRoot -Force | Out-Null
Expand-Archive -LiteralPath $archivePath -DestinationPath $installRoot
if (-not (Test-Path -LiteralPath $mariaBin) -or -not (Test-Path -LiteralPath $installer)) {
    throw 'MariaDB ZIP extraction did not produce the expected executables.'
}
New-Item -ItemType Directory -Path $secretsDir, $pylibsDir, $logsDir -Force | Out-Null

$rootPassword = New-Secret
$monitorPassword = New-Secret
Write-Host 'Initializing an isolated, non-service MariaDB data directory...'
& $installer "--datadir=$dataDir" "--password=$rootPassword" "--port=$dbPort"
if ($LASTEXITCODE -ne 0) { throw "mariadb-install-db failed (exit $LASTEXITCODE)." }

$dataForConfig = $dataDir.Replace('\', '/')
$pluginForConfig = (Join-Path $installRoot 'mariadb-11.8.9-winx64\lib\plugin').Replace('\', '/')
$config = "[mysqld]`r`ndatadir=$dataForConfig`r`nport=$dbPort`r`nbind-address=127.0.0.1`r`n[client]`r`nport=$dbPort`r`nplugin-dir=$pluginForConfig`r`n"
[System.IO.File]::WriteAllText((Join-Path $dataDir 'my.ini'), $config, [System.Text.Encoding]::ASCII)
[System.IO.File]::WriteAllText((Join-Path $secretsDir 'root-password.txt'), $rootPassword, [System.Text.Encoding]::ASCII)
[System.IO.File]::WriteAllText((Join-Path $secretsDir 'monitor-password.txt'), $monitorPassword, [System.Text.Encoding]::ASCII)

Write-Host 'Installing the Python DB connector into the course-only folder...'
& $pythonExe -m pip install --disable-pip-version-check --target $pylibsDir 'PyMySQL==1.2.3'
if ($LASTEXITCODE -ne 0) { throw "PyMySQL installation failed (exit $LASTEXITCODE)." }

Start-Process -FilePath $mariaBin `
    -ArgumentList @("--defaults-file=$(Join-Path $dataDir 'my.ini')", '--bind-address=127.0.0.1', '--console') `
    -WorkingDirectory (Split-Path $mariaBin) -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $logsDir 'mariadb.out.log') `
    -RedirectStandardError (Join-Path $logsDir 'mariadb.err.log')
Start-Sleep -Seconds 2
if (-not (Test-LocalPort $dbPort)) { throw 'MariaDB did not start; inspect logs/mariadb.err.log.' }

$env:PYTHONPATH = $pylibsDir
$env:COURSE_ROOT_PASSWORD = $rootPassword
$env:COURSE_MONITOR_PASSWORD = $monitorPassword
$env:DB_PORT = [string]$dbPort
try {
    Push-Location -LiteralPath $projectRoot
    try { & $pythonExe -B -m scripts.setup_local_database }
    finally { Pop-Location }
    if ($LASTEXITCODE -ne 0) { throw 'Course DB account setup failed.' }
}
finally {
    Remove-Item Env:COURSE_ROOT_PASSWORD, Env:COURSE_MONITOR_PASSWORD -ErrorAction SilentlyContinue
}

Write-Host 'MariaDB is ready. Starting the course application...'
& (Join-Path $PSScriptRoot 'start_local_live.ps1')
