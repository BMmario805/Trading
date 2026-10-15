# Inicio local sin ventanas; conserva los servicios de este proyecto ya activos.
$ErrorActionPreference = 'Stop'
$tradingRoot = $PSScriptRoot
$runtimeDir = Join-Path $tradingRoot '.runtime'
New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null
$launchStamp = Get-Date -Format 'yyyyMMdd-HHmmss'

function Start-TradingService {
    param([int]$Port, [string]$Executable, [string[]]$Arguments, [string]$Directory, [string]$Name)
    $listeners = @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)
    if ($listeners.Count -gt 0) {
        foreach ($listener in $listeners) {
            $ownerProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)"
            if (-not $ownerProcess.CommandLine -or -not $ownerProcess.CommandLine.Contains($tradingRoot)) {
                throw "El puerto $Port está ocupado por otra aplicación."
            }
        }
        return
    }
    Start-Process -FilePath $Executable -ArgumentList $Arguments -WorkingDirectory $Directory -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $runtimeDir "$Name-$launchStamp.stdout.log") `
        -RedirectStandardError (Join-Path $runtimeDir "$Name-$launchStamp.stderr.log") | Out-Null
}

Start-TradingService -Port 8000 -Executable (Join-Path $tradingRoot 'backend\.venv\Scripts\python.exe') `
    -Arguments @('-m','uvicorn','app.main:app','--app-dir',('"'+(Join-Path $tradingRoot 'backend')+'"'),'--port','8000') `
    -Directory $tradingRoot -Name 'backend'

$nodeExecutable = (Get-Command node.exe -ErrorAction Stop).Source
Start-TradingService -Port 5173 -Executable $nodeExecutable `
    -Arguments @(('"'+(Join-Path $tradingRoot 'frontend\node_modules\vite\bin\vite.js')+'"'),'--host','localhost','--port','5173','--strictPort') `
    -Directory (Join-Path $tradingRoot 'frontend') -Name 'frontend'
