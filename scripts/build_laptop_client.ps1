$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$packageName = 'DBMS-Laptop-Client-' + (Get-Date -Format 'yyyyMMdd-HHmmss')
$outputRoot = Join-Path $projectRoot '.local\distribution'
$packageRoot = Join-Path $outputRoot $packageName
$archivePath = "$packageRoot.zip"
if ((Test-Path -LiteralPath $packageRoot) -or (Test-Path -LiteralPath $archivePath)) {
    throw 'Package output already exists. Retry with a new timestamp.'
}

$files = @(
    'start-laptop-client.cmd',
    'scripts/start_laptop_client.ps1',
    'docs/LAPTOP_LAN_TEST.md',
    'app/__init__.py',
    'app/client_core.py',
    'app/protocol.py',
    'app/metrics.py',
    'app/feedback.py',
    'app/web_inputs.py',
    'app/web_http.py',
    'app/web_client.py',
    'app/static/index.html',
    'app/static/style.css',
    'app/static/app.js',
    'app/static/target-choices.js'
)
foreach ($relativePath in $files) {
    $source = Join-Path $projectRoot $relativePath
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
        throw "Missing package source: $relativePath"
    }
}
foreach ($relativePath in $files) {
    $destination = Join-Path $packageRoot $relativePath
    New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $projectRoot $relativePath) -Destination $destination
}
Compress-Archive -LiteralPath $packageRoot -DestinationPath $archivePath
Write-Host "Package: $archivePath"
Write-Host "Files: $($files.Count)"
Get-FileHash -LiteralPath $archivePath -Algorithm SHA256
