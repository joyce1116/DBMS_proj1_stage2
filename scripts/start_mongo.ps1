param(
    [ValidateRange(1024, 65535)]
    [int]$Port = 27018
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$dataDirectory = Join-Path $projectRoot '.runtime\mongo-data'
New-Item -ItemType Directory -Path $dataDirectory -Force | Out-Null
$mongoExecutable = (Get-Command mongod -ErrorAction Stop).Source
Write-Host "MongoDB data: $dataDirectory"
Write-Host "MongoDB URL: mongodb://127.0.0.1:$Port"
Write-Host 'Keep this terminal open. Press Ctrl+C to stop MongoDB.'
& $mongoExecutable --dbpath $dataDirectory --bind_ip 127.0.0.1 --port $Port
exit $LASTEXITCODE
