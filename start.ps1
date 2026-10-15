# Arranca los servicios en segundo plano y abre el panel.
& (Join-Path $PSScriptRoot 'start-background.ps1')
Start-Sleep -Seconds 3
Start-Process "http://localhost:5173"
