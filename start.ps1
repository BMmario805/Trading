# Arranca backend (8000) y frontend (5173) en ventanas separadas
$root = $PSScriptRoot

Start-Process powershell -ArgumentList "-NoExit", "-Command",
  "& '$root\backend\.venv\Scripts\python.exe' -m uvicorn app.main:app --app-dir '$root\backend' --port 8000"

Start-Process powershell -ArgumentList "-NoExit", "-Command",
  "Set-Location '$root\frontend'; npm run dev"

Start-Sleep -Seconds 3
Start-Process "http://localhost:5173"
