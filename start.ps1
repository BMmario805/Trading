# Recarga el backend, conserva el frontend y abre el panel.
$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'start-background.ps1') -RestartBackend
Start-Process "http://localhost:5173"
