$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')
$env:PYTHONPATH = Join-Path (Get-Location) 'src'
& .venv\Scripts\python.exe scripts\build.py @args
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
