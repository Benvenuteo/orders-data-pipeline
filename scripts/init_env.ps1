$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$envPath = Join-Path $projectRoot '.env'
if (Test-Path -LiteralPath $envPath) {
    Write-Host 'Existing .env preserved.'
    exit 0
}
function New-RandomHex([int]$ByteCount) {
    $bytes = New-Object byte[] $ByteCount
    $generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $generator.GetBytes($bytes) } finally { $generator.Dispose() }
    return [BitConverter]::ToString($bytes).Replace('-', '').ToLowerInvariant()
}
$lines = @(
    '# Local synthetic lab. Never commit this file.'
    ('API_KEY=' + (New-RandomHex 24))
    ('SQL_ADMIN_PASSWORD=Admin1!' + (New-RandomHex 18))
    ('SQL_PASSWORD=App1!' + (New-RandomHex 18))
    'SQL_DATABASE=orders_warehouse'
    'SQL_USER=orders_app'
    'SQL_TRUST_CERTIFICATE=true'
    'PAGE_SIZE=500'
    'MAX_EVENTS=100000'
    'HTTP_ATTEMPTS=4'
)
# CreateNew prevents an accidental overwrite even if another process created the file.
$stream = [System.IO.File]::Open($envPath, [System.IO.FileMode]::CreateNew)
$writer = New-Object System.IO.StreamWriter($stream, (New-Object System.Text.UTF8Encoding($false)))
try { $writer.Write(($lines -join "`n") + "`n") } finally { $writer.Dispose() }
Write-Host 'Created .env with new local secrets.'
