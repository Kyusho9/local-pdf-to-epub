[CmdletBinding()]
param(
  [string]$ProjectRoot = 'D:\Projects\pdf-to-epub',
  [string]$Runtime = 'D:\Projects\pdf-to-epub\gpu-runtime',
  [ValidateSet('cuda','directml')][string]$Device = 'cuda',
  [string]$EvidenceName = 'offline-embedded-final'
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$py = Join-Path $Runtime 'python.exe'
$evidence = Join-Path (Join-Path $ProjectRoot 'gpu-probe') $EvidenceName
[IO.Directory]::CreateDirectory($evidence) | Out-Null
$utf8 = [Text.UTF8Encoding]::new($false)
$env:PYTHONIOENCODING = 'utf-8'
$name = 'LocalPDFtoEPUB-' + $Device + '-Probe-' + [guid]::NewGuid().ToString('N')
$networkProbe = @'
import socket,json
s=socket.socket();s.settimeout(5)
try:
 s.connect(('223.5.5.5',443));print(json.dumps({'connected':True}))
except OSError as e:
 print(json.dumps({'connected':False,'winerror':getattr(e,'winerror',None),'errno':e.errno,'error':str(e)}))
finally:s.close()
'@
$record = [ordered]@{scope='Temporary per-program outbound Windows Firewall rule; no global policy or interface change';device=$Device;runtime=$Runtime;rule_name=$name;loopback_block_claimed=$false}
try {
  $record.profiles = @(Get-NetFirewallProfile | Select-Object Name,Enabled)
  if (@($record.profiles | Where-Object { -not $_.Enabled }).Count) { throw 'A firewall profile is disabled; cannot claim effective offline test' }
  $record.positive_before = (& $py -I -S -B -c $networkProbe) | ConvertFrom-Json
  if (-not $record.positive_before.connected) { throw 'Public network positive control unavailable' }
  New-NetFirewallRule -Name $name -DisplayName $name -Group $name -Program $py -Direction Outbound -Action Block -Profile Any -Enabled True | Out-Null
  $active = Get-NetFirewallRule -PolicyStore ActiveStore -Name $name
  if ($active.Enabled -ne 'True' -or $active.Action -ne 'Block') { throw 'Temporary program block is not active' }
  $record.program_filter = (Get-NetFirewallApplicationFilter -AssociatedNetFirewallRule $active).Program
  $record.blocked_probe = (& $py -I -S -B -c $networkProbe) | ConvertFrom-Json
  if ($record.blocked_probe.connected -or $record.blocked_probe.winerror -ne 10013) { throw 'Firewall did not produce native WSAEACCES 10013' }
  $arguments = @('-I','-S','-B',"$ProjectRoot\gpu-probe\gpu-inference-probe.py",'--source',"$ProjectRoot\source\tests\fixtures\02_mixed_bad_layer.pdf",'--resources',"$ProjectRoot\resources",'--device',$Device,'--images-dir',"$ProjectRoot\gpu-probe\results-cuda",'--output',$evidence,'--repeat','0')
  $process = Start-Process -FilePath $py -ArgumentList $arguments -PassThru -NoNewWindow -RedirectStandardOutput "$evidence\stdout.log" -RedirectStandardError "$evidence\stderr.log"
  # Retain a real process handle before it exits. PowerShell's Process wrapper
  # otherwise loses ExitCode on fast processes, although the child succeeded.
  $null = $process.Handle
  $record.pid = $process.Id
  $sockets = @()
  $samples = 0
  do {
    $sockets += @(Get-NetTCPConnection -OwningProcess $process.Id -ErrorAction SilentlyContinue | Select-Object State,LocalAddress,LocalPort,RemoteAddress,RemotePort,OwningProcess)
    $samples += 1
    Start-Sleep -Milliseconds 150
    $process.Refresh()
  } while (-not $process.HasExited)
  $process.WaitForExit()
  $record.exit_code = $process.ExitCode
  $record.tcp_samples = $samples
  $record.observed_sockets = $sockets
  if ($process.ExitCode -ne 0) { throw ('Embedded GPU inference failed: ' + [IO.File]::ReadAllText("$evidence\stderr.log")) }
  $result = [IO.File]::ReadAllText("$evidence\result.json") | ConvertFrom-Json
  $record.cuda_all_models = $result.actual_cuda_execution_all_three_models
  $record.requested_provider_all_models = $result.actual_requested_provider_execution_all_three_models
  $record.truth_passed = $result.truth_all_passed
  if (-not $record.requested_provider_all_models -or -not $record.truth_passed) { throw 'GPU truth/profile evidence failed' }
  if (@($sockets | Where-Object State -eq 'Established').Count) { throw 'Unexpected established socket observed during offline GPU inference' }
  $record.passed = $true
} catch {
  $record.passed = $false
  $record.error = $_.ToString()
  throw
} finally {
  Remove-NetFirewallRule -Name $name -ErrorAction SilentlyContinue
  $record.rule_removed = @(Get-NetFirewallRule -Name $name -ErrorAction SilentlyContinue).Count -eq 0
  $record.positive_after = (& $py -I -S -B -c $networkProbe) | ConvertFrom-Json
  if (-not $record.rule_removed -or -not $record.positive_after.connected) {
    $record.passed = $false
    $record.error = 'Temporary rule cleanup or restored positive control failed'
  }
  $record.finished_at = (Get-Date).ToUniversalTime().ToString('o')
  [IO.File]::WriteAllText("$evidence\firewall.json",($record | ConvertTo-Json -Depth 10),$utf8)
}
$record | ConvertTo-Json -Depth 10
if (-not $record.passed) { throw 'Offline probe failed; see firewall.json' }
