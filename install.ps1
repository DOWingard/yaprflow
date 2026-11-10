# Installs yaprflow on Windows: a Startup-folder shortcut runs it without a console window at every login.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { throw "uv is not installed: https://docs.astral.sh/uv/" }
uv sync --frozen --no-dev
if ($LASTEXITCODE -ne 0) { throw "uv sync failed" }

$pythonw = Join-Path $PSScriptRoot ".venv\Scripts\pythonw.exe"
New-Item -ItemType Directory -Force (Join-Path $env:APPDATA "yaprflow") | Out-Null

$shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path ([Environment]::GetFolderPath("Startup")) "yaprflow.lnk"))
$shortcut.TargetPath = $pythonw
$shortcut.Arguments = "-m yaprflow"
$shortcut.WorkingDirectory = $PSScriptRoot
$shortcut.Save()

# Replace a running copy so the new install takes effect now.
Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" |
    Where-Object { $_.CommandLine -like "*-m yaprflow*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
Start-Process -FilePath $pythonw -ArgumentList "-m", "yaprflow" -WorkingDirectory $PSScriptRoot

Write-Host "yaprflow installed and running; it starts at every login."
Write-Host "Logs: $env:LOCALAPPDATA\yaprflow\yaprflow.log"
Write-Host "Config: $env:APPDATA\yaprflow\env"
