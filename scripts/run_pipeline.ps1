param(
    [string]$ProjectPath = (Split-Path -Parent $PSScriptRoot)
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $ProjectPath
$logDirectory = Join-Path $ProjectPath 'reports'
New-Item -ItemType Directory -Force -Path $logDirectory | Out-Null
$logFile = Join-Path $logDirectory ('pipeline-' + (Get-Date -Format 'yyyy-MM-dd') + '.log')
# Docker Compose owns environment loading; secrets never become command-line arguments.
# PowerShell 7 may treat native stderr as an error record; capture it without throwing.
$ErrorActionPreference = 'Continue'
& docker compose run --rm --no-deps pipeline run 2>&1 | Out-File -Append -Encoding utf8 $logFile
$pipelineExitCode = $LASTEXITCODE
exit $pipelineExitCode
