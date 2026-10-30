# Read-only environment report for Windows build/acceptance. No network or admin rights needed.
param([string]$Output = 'windows-environment.json')
$ErrorActionPreference = 'Stop'
$os = Get-CimInstance Win32_OperatingSystem
$cpu = Get-CimInstance Win32_Processor
$system = Get-CimInstance Win32_ComputerSystem
$gpu = Get-CimInstance Win32_VideoController
$report = [ordered]@{
  operatingSystem = $os.Caption
  version = $os.Version
  architecture = $os.OSArchitecture
  processorArchitecture = $env:PROCESSOR_ARCHITECTURE
  cpu = @($cpu | Select-Object Name, NumberOfCores, NumberOfLogicalProcessors)
  physicalMemoryBytes = [uint64]$system.TotalPhysicalMemory
  gpu = @($gpu | Select-Object Name, DriverVersion)
  dedicatedVideoMemory = $null
  videoMemoryNote = 'Win32_VideoController.AdapterRAM is not used as an authoritative dedicated VRAM size. The application uses CPU only.'
  powershell = $PSVersionTable.PSVersion.ToString()
  python = if (Get-Command python -ErrorAction SilentlyContinue) { (Get-Command python).Source } else { $null }
  java = if (Get-Command java -ErrorAction SilentlyContinue) { (Get-Command java).Source } else { $null }
}
$report | ConvertTo-Json -Depth 6 | Set-Content -Encoding UTF8 $Output
Write-Output (Resolve-Path $Output)
