# Inicio local sin ventanas; permite recargar el backend de este proyecto.
param([switch]$RestartBackend)

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
        if ($Name -ne 'backend' -or -not $RestartBackend) {
            return
        }
        $backendProcesses = @(Get-CimInstance Win32_Process | Where-Object {
            $_.Name -like 'python*.exe' -and $_.CommandLine -and
            $_.CommandLine.Contains($tradingRoot + '\backend') -and
            $_.CommandLine.Contains('-m uvicorn app.main:app')
        })
        foreach ($listener in $listeners) {
            if ($listener.OwningProcess -notin $backendProcesses.ProcessId) {
                throw 'El servicio del puerto 8000 no es el backend esperado de este proyecto.'
            }
        }
        foreach ($backendProcess in $backendProcesses) {
            Stop-Process -Id $backendProcess.ProcessId -Force -ErrorAction SilentlyContinue
        }
        foreach ($backendProcess in $backendProcesses) {
            Wait-Process -Id $backendProcess.ProcessId -Timeout 10 -ErrorAction SilentlyContinue
        }
    }
    $serviceProcess = Start-Process -FilePath $Executable -ArgumentList $Arguments -WorkingDirectory $Directory -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $runtimeDir "$Name-$launchStamp.stdout.log") `
        -RedirectStandardError (Join-Path $runtimeDir "$Name-$launchStamp.stderr.log")
    if ($Name -eq 'backend') {
        $deadline = (Get-Date).AddSeconds(45)
        while ((Get-Date) -lt $deadline) {
            $serviceProcess.Refresh()
            if ($serviceProcess.HasExited) {
                throw "El backend terminó al arrancar. Revisa .runtime/backend-$launchStamp.stderr.log."
            }
            try {
                $response = Invoke-WebRequest -Uri 'http://127.0.0.1:8000/api/mt5' -TimeoutSec 2
                if ($response.StatusCode -eq 200) { return }
            } catch { }
            Start-Sleep -Milliseconds 500
        }
        throw "El backend no responde. Revisa .runtime/backend-$launchStamp.stderr.log."
    }
}

Start-TradingService -Port 8000 -Executable (Join-Path $tradingRoot 'backend\.venv\Scripts\python.exe') `
    -Arguments @('-m','uvicorn','app.main:app','--app-dir',('"'+(Join-Path $tradingRoot 'backend')+'"'),'--port','8000') `
    -Directory $tradingRoot -Name 'backend'

$nodeExecutable = (Get-Command node.exe -ErrorAction Stop).Source
Start-TradingService -Port 5173 -Executable $nodeExecutable `
    -Arguments @(('"'+(Join-Path $tradingRoot 'frontend\node_modules\vite\bin\vite.js')+'"'),'--host','localhost','--port','5173','--strictPort') `
    -Directory (Join-Path $tradingRoot 'frontend') -Name 'frontend'
