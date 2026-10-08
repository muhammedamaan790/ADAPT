# Starts the whole demo (world simulator :8100, API :8000, web app :5173), each in its own window, waits until all
# three answer, then opens the landing page. Services already running are left alone.
#   powershell -ExecutionPolicy Bypass -File start-demo.ps1
$root = $PSScriptRoot

function Test-Up($url) {
    try { (Invoke-WebRequest -UseBasicParsing $url -TimeoutSec 3).StatusCode -lt 500 } catch { $false }
}

function Start-Service($title, $dir, $command, $url) {
    if (Test-Up $url) { Write-Host "$title already running"; return }
    Write-Host "starting $title"
    Start-Process powershell -WorkingDirectory $dir -ArgumentList '-NoExit', '-Command',
        "`$host.UI.RawUI.WindowTitle = '$title'; $command"
}

Start-Service 'ADAPT world :8100' $root "`$env:WORLD_DIR = 'data/world/seed42'; uv run uvicorn world.main:app --app-dir world --port 8100" 'http://127.0.0.1:8100/health'
Start-Service 'ADAPT API :8000' $root 'uv run uvicorn adapt.api.main:app --app-dir backend --port 8000' 'http://127.0.0.1:8000/api/v1/health'
Start-Service 'ADAPT web :5173' (Join-Path $root 'web') '$env:VITE_DATA_MODE = ''api''; npm run dev' 'http://127.0.0.1:5173/'

$checks = @('http://127.0.0.1:8100/health', 'http://127.0.0.1:8000/api/v1/health', 'http://127.0.0.1:5173/')
$deadline = (Get-Date).AddMinutes(2)
while ((Get-Date) -lt $deadline -and -not (($checks | ForEach-Object { Test-Up $_ }) -notcontains $false)) {
    Start-Sleep -Seconds 2
}
foreach ($c in $checks) { Write-Host ("{0,-40} {1}" -f $c, $(if (Test-Up $c) { 'up' } else { 'NOT UP - check its window' })) }

Start-Process 'http://127.0.0.1:5173/product'
