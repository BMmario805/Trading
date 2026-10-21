param([Parameter(Mandatory)][string]$PlanPath, [string]$Profile = 'DEFAULT')
$ErrorActionPreference = 'Stop'
$tradingRoot = Split-Path -Parent $PSScriptRoot
$tradingPython = Join-Path $tradingRoot 'backend\.venv\Scripts\python.exe'
$journal = Join-Path $tradingRoot '.runtime\oracle-retry.sqlite'
$logPath = Join-Path $tradingRoot '.runtime\oracle-retry-status.json'
try {
    $raw = & $tradingPython (Join-Path $PSScriptRoot 'retry_oracle.py') --plan $PlanPath --journal $journal --profile $Profile --apply
    if ($LASTEXITCODE -ne 0) { throw 'La configuración o autenticación Oracle necesita revisión.' }
    $result = ($raw -join "`n") | ConvertFrom-Json
    $result | Add-Member -NotePropertyName checkedAt -NotePropertyValue (Get-Date).ToUniversalTime().ToString('o') -Force
    $result | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $logPath -Encoding UTF8
    if ($result.status -notin @('capacity','backoff')) {
        Disable-ScheduledTask -TaskName 'MesaSiete-OracleCapacity' | Out-Null
    }
} catch {
    @{status='configuration_error';message='Revisar plan, SDK o autenticación; reintentos detenidos.';checkedAt=(Get-Date).ToUniversalTime().ToString('o')} | ConvertTo-Json | Set-Content -LiteralPath $logPath -Encoding UTF8
    Disable-ScheduledTask -TaskName 'MesaSiete-OracleCapacity' -ErrorAction SilentlyContinue | Out-Null
    exit 1
}
