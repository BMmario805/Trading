# Instala reintentos solo cuando plan y autenticación reales pasan las comprobaciones.
param([string]$PlanPath = '', [string]$Profile = 'DEFAULT')
$ErrorActionPreference = 'Stop'
$tradingRoot = Split-Path -Parent $PSScriptRoot
$tradingPython = Join-Path $tradingRoot 'backend\.venv\Scripts\python.exe'
if (-not $PlanPath) { $PlanPath = Join-Path $tradingRoot '.runtime\oracle-plan.json' }
if (-not (Test-Path -LiteralPath $PlanPath -PathType Leaf)) {
    throw 'Falta .runtime\oracle-plan.json con los OCID reales de la cuenta Oracle.'
}
$resolvedPlan = (Resolve-Path -LiteralPath $PlanPath).Path
& $tradingPython (Join-Path $PSScriptRoot 'retry_oracle.py') --plan $resolvedPlan
if ($LASTEXITCODE -ne 0) { throw 'Plan Oracle inválido; no se instala ninguna tarea.' }
& $tradingPython -c 'import oci,sys; oci.config.validate_config(oci.config.from_file(profile_name=sys.argv[1]))' $Profile
if ($LASTEXITCODE -ne 0) { throw 'Falta SDK o autenticación OCI válida; no se instala ninguna tarea.' }
if ($Profile -notmatch '^[A-Za-z0-9_-]+$' -or $resolvedPlan.Contains('"')) {
    throw 'Perfil o ruta no admitidos para la tarea programada.'
}
$runner = Join-Path $PSScriptRoot 'run-oracle-retry.ps1'
$taskArguments = '-NoProfile -NonInteractive -WindowStyle Hidden -File "{0}" -PlanPath "{1}" -Profile "{2}"' -f $runner,$resolvedPlan,$Profile
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $taskArguments -WorkingDirectory $tradingRoot
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 15) -RepetitionDuration (New-TimeSpan -Days 365)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
Register-ScheduledTask -TaskName 'MesaSiete-OracleCapacity' -Action $action -Trigger $trigger -Settings $settings -Description 'Oracle Madrid: máximo una instancia del plan; se detiene al crear o ante respuesta incierta.' -Force | Out-Null
Write-Output 'Tarea instalada: MesaSiete-OracleCapacity, cada 15 minutos.'
