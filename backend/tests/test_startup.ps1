# Pruebas de arranque con procesos y red simulados; no detienen servicios reales.
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$startupScript = Join-Path $projectRoot 'start-background.ps1'
$startupTestState = [pscustomobject]@{
    Scenario = ''
    Started = [System.Collections.Generic.List[string]]::new()
    Stopped = [System.Collections.Generic.List[int]]::new()
}

function Get-NetTCPConnection {
    param($State, $LocalPort, $ErrorAction)
    [pscustomobject]@{ OwningProcess = $(if ($LocalPort -eq 8000) { 100 } else { 200 }) }
}

function Get-CimInstance {
    param($ClassName, $Filter)
    $command = '"' + (Join-Path $projectRoot 'backend\.venv\Scripts\python.exe') + '" -m uvicorn app.main:app'
    if ($Filter) {
        if ($startupTestState.Scenario -eq 'foreign' -and $Filter -eq 'ProcessId=100') {
            return [pscustomobject]@{ CommandLine = 'unrelated application' }
        }
        return [pscustomobject]@{ CommandLine = $command }
    }
    foreach ($processId in (100, 101)) {
        [pscustomobject]@{ ProcessId = $processId; Name = 'python.exe'; CommandLine = $command }
    }
}

function Stop-Process {
    param($Id, [switch]$Force, $ErrorAction)
    $startupTestState.Stopped.Add($Id)
}

function Wait-Process {
    param($Id, $Timeout, $ErrorAction)
}

function Start-Process {
    param($FilePath, $ArgumentList, $WorkingDirectory, $WindowStyle, [switch]$PassThru,
          $RedirectStandardOutput, $RedirectStandardError)
    if ($WindowStyle -ne 'Hidden') { throw 'El servicio debe arrancar sin ventana.' }
    $startupTestState.Started.Add($FilePath)
    $fake = [pscustomobject]@{ HasExited = ($startupTestState.Scenario -eq 'failed') }
    $fake | Add-Member -MemberType ScriptMethod -Name Refresh -Value { }
    return $fake
}

function Invoke-WebRequest {
    param($Uri, $TimeoutSec)
    [pscustomobject]@{ StatusCode = 200 }
}

foreach ($case in ('keep', 'restart', 'foreign', 'failed')) {
    $startupTestState.Scenario = $case
    $startupTestState.Started.Clear()
    $startupTestState.Stopped.Clear()
    $failed = $false
    try {
        & $startupScript -RestartBackend:($case -ne 'keep')
    } catch {
        $failed = $true
        if ($case -notin ('foreign', 'failed')) { throw }
    }
    switch ($case) {
        'keep' {
            if ($startupTestState.Started.Count -or $startupTestState.Stopped.Count) { throw 'El arranque normal debe conservar los servicios activos.' }
        }
        'restart' {
            if ($startupTestState.Started.Count -ne 1 -or ($startupTestState.Stopped -join ',') -ne '100,101') {
                throw 'El reinicio debe sustituir únicamente los procesos Python del backend.'
            }
        }
        'foreign' {
            if (-not $failed -or $startupTestState.Stopped.Count -or $startupTestState.Started.Count) { throw 'No se debe tocar un puerto ajeno.' }
        }
        'failed' {
            if (-not $failed) { throw 'Un fallo del proceso de arranque debe notificarse.' }
        }
    }
}
Write-Output 'OK: 4 escenarios de arranque, sin modificar procesos reales.'
