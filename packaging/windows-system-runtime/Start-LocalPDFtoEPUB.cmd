@echo off
setlocal
cd /d "%~dp0"
set "PDFEPUB_PS=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if exist "%SystemRoot%\Sysnative\WindowsPowerShell\v1.0\powershell.exe" set "PDFEPUB_PS=%SystemRoot%\Sysnative\WindowsPowerShell\v1.0\powershell.exe"
"%PDFEPUB_PS%" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0Prepare-System-Runtime.ps1"
if errorlevel 1 (
  echo.
  echo Application did not start. Read the message above and README-system-runtime.txt.
  pause
  exit /b 1
)
endlocal
