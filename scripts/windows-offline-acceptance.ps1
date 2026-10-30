#requires -Version 5.1
<#
Run from an elevated Windows PowerShell session. No adapter/global policy changes.
Temporary outbound rules cover only the supplied GUI, CLI and this run's probe.
The GUI is not launched here; its interactive acceptance remains a separate test.
Windows Firewall does not consistently filter loopback; only public IPv4 is probed.
#>
param(
    [string]$ApplicationDirectory = (Join-Path (Split-Path $PSScriptRoot -Parent) 'dist\LocalPDFtoEPUB'),
    [string]$ResourcesDirectory = (Join-Path (Split-Path $PSScriptRoot -Parent) 'resources'),
    [string]$InputPdf = (Join-Path (Split-Path $PSScriptRoot -Parent) 'tests\fixtures\02_mixed_bad_layer.pdf'),
    [string]$EvidenceDirectory = (Join-Path (Split-Path $PSScriptRoot -Parent) 'artifacts\windows-offline'),
    [string[]]$ProbeAddresses = @('1.1.1.1', '223.5.5.5', '8.8.8.8'),
    [int]$ProbePort = 443,
    [int]$ProbeTimeoutMilliseconds = 3000,
    [int]$ConversionTimeoutSeconds = 300
)
$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path $PSScriptRoot -Parent
$Utf8 = New-Object System.Text.UTF8Encoding($false)
$RunId = [Guid]::NewGuid().ToString('N')

function Full-Path([string]$Path) {
    if (-not [IO.Path]::IsPathRooted($Path)) { $Path = Join-Path $ProjectRoot $Path }
    return [IO.Path]::GetFullPath($Path)
}
function Write-Json([string]$Path, $Value) {
    [IO.File]::WriteAllText($Path, ($Value | ConvertTo-Json -Depth 18), $Utf8)
}
function Native-Argument([string]$Value) {
    if ($Value.Length -gt 0 -and $Value -notmatch '[\s"]') { return $Value }
    $Value = [Regex]::Replace($Value, '(\\*)"', '$1$1\"')
    $Value = [Regex]::Replace($Value, '(\\+)$', '$1$1')
    return '"' + $Value + '"'
}
function Invoke-Captured {
    param([string]$Executable, [string[]]$Arguments, [string]$Label, [int]$TimeoutSeconds = 30, [switch]$Observe)
    $info = New-Object Diagnostics.ProcessStartInfo
    $info.FileName = $Executable
    $info.Arguments = (($Arguments | ForEach-Object { Native-Argument $_ }) -join ' ')
    $info.WorkingDirectory = $RunDirectory
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $info.StandardOutputEncoding = $Utf8
    $info.StandardErrorEncoding = $Utf8
    foreach ($name in @('PYTHONPATH','PYTHONHOME','VIRTUAL_ENV','CONDA_PREFIX','QT_PLUGIN_PATH','QT_QPA_PLATFORM_PLUGIN_PATH')) {
        $info.EnvironmentVariables.Remove($name)
    }
    $info.EnvironmentVariables['PATH'] = (Join-Path $env:SystemRoot 'System32') + ';' + $env:SystemRoot
    $info.EnvironmentVariables['PYTHONIOENCODING'] = 'ascii'
    $info.EnvironmentVariables['TEMP'] = $RuntimeTemp
    $info.EnvironmentVariables['TMP'] = $RuntimeTemp
    $process = New-Object Diagnostics.Process
    $process.StartInfo = $info
    [void]$process.Start()
    $outputTask = $process.StandardOutput.ReadToEndAsync()
    $errorTask = $process.StandardError.ReadToEndAsync()
    $samples = New-Object 'System.Collections.Generic.List[object]'
    $observationErrors = New-Object 'System.Collections.Generic.List[string]'
    $started = [DateTime]::UtcNow
    $timedOut = $false
    try {
        while (-not $process.HasExited) {
            if ($Observe) {
                try {
                    # Enumerate then filter: zero matches is an ordinary empty observation.
                    $connections = @(Get-NetTCPConnection -ErrorAction Stop | Where-Object { $_.OwningProcess -eq $process.Id } | ForEach-Object {
                        [ordered]@{ state = [string]$_.State; local_address = $_.LocalAddress; local_port = $_.LocalPort; remote_address = $_.RemoteAddress; remote_port = $_.RemotePort }
                    })
                    $samples.Add([ordered]@{ utc = [DateTime]::UtcNow.ToString('o'); pid = $process.Id; tcp = $connections })
                } catch { $observationErrors.Add($_.Exception.ToString()) }
            }
            if (([DateTime]::UtcNow - $started).TotalSeconds -gt $TimeoutSeconds) {
                $process.Kill()
                $timedOut = $true
                break
            }
            [void]$process.WaitForExit(120)
        }
        $process.WaitForExit()
        $stdout = $outputTask.GetAwaiter().GetResult()
        $stderr = $errorTask.GetAwaiter().GetResult()
        [IO.File]::WriteAllText((Join-Path $RunDirectory ($Label + '.stdout.log')), $stdout, $Utf8)
        [IO.File]::WriteAllText((Join-Path $RunDirectory ($Label + '.stderr.log')), $stderr, $Utf8)
        return [ordered]@{ pid = $process.Id; exit_code = $process.ExitCode; timed_out = $timedOut; elapsed_seconds = ([DateTime]::UtcNow - $started).TotalSeconds; stdout = $stdout; stderr = $stderr; samples = $samples.ToArray(); observation_errors = $observationErrors.ToArray() }
    } finally {
        if (-not $process.HasExited) { $process.Kill(); $process.WaitForExit() }
        $process.Dispose()
    }
}
function Invoke-Probe([string]$Address, [string]$Label) {
    $capture = Invoke-Captured -Executable $ProbeExe -Arguments @($Address, [string]$ProbePort, [string]$ProbeTimeoutMilliseconds) -Label $Label -TimeoutSeconds 15
    if ($capture.timed_out -or $capture.exit_code -ne 0) { throw "Probe execution failed (timeout=$($capture.timed_out)): $($capture.stderr)" }
    $answer = $capture.stdout | ConvertFrom-Json
    return [ordered]@{ address = $Address; port = $ProbePort; connected = [bool]$answer.connected; native_error_code = $answer.native_error_code; socket_error = $answer.socket_error; timed_out = $answer.timed_out; elapsed_ms = $answer.elapsed_ms; pid = $answer.pid; error = $answer.error; stderr = $capture.stderr }
}

$ApplicationDirectory = Full-Path $ApplicationDirectory
$ResourcesDirectory = Full-Path $ResourcesDirectory
$InputPdf = Full-Path $InputPdf
$RunDirectory = Join-Path (Full-Path $EvidenceDirectory) ('run-' + $RunId)
[void][IO.Directory]::CreateDirectory($RunDirectory)
$RuntimeTemp = Join-Path $RunDirectory 'tmp'
[void][IO.Directory]::CreateDirectory($RuntimeTemp)
$GuiExe = Join-Path $ApplicationDirectory 'LocalPDFtoEPUB.exe'
$CliExe = Join-Path $ApplicationDirectory 'LocalPDFtoEPUB-CLI.exe'
$ProbeExe = Join-Path $RunDirectory 'network-probe.exe'
$RuleNames = @(("LocalPDFEPUB-" + $RunId + "-gui"), ("LocalPDFEPUB-" + $RunId + "-cli"), ("LocalPDFEPUB-" + $RunId + "-probe"))
$Errors = New-Object 'System.Collections.Generic.List[string]'
$Cleanup = New-Object 'System.Collections.Generic.List[object]'
$Result = [ordered]@{ schema_version = 1; run_id = $RunId; started_utc = [DateTime]::UtcNow.ToString('o'); status = 'running'; scope = 'Frozen CLI conversion; temporary GUI rule installed but GUI not launched'; loopback = 'Not a pass condition: Windows Firewall may exempt loopback; this application has no loopback inference service'; gui = $GuiExe; cli = $CliExe; resource_directory = $ResourcesDirectory; input_pdf = $InputPdf; rule_names = $RuleNames; profiles = @(); active_profiles = @(); services = @(); effective_rules = @(); baseline_attempts = @(); blocked_probe = $null; restored_probe = $null; probe_policy_verified = $false; conversion = $null; cleanup = @(); errors = @() }
Write-Json (Join-Path $RunDirectory 'rule-intent.json') ([ordered]@{ rule_names = $RuleNames; programs = @($GuiExe, $CliExe, $ProbeExe); note = 'Cleanup only these GUID names if the PowerShell host is forcibly terminated.' })
$SelectedAddress = $null

try {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'An elevated administrator token is required; no firewall settings were changed.' }
    foreach ($path in @($GuiExe, $CliExe, $InputPdf, (Join-Path $ResourcesDirectory 'manifest.json'))) {
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Missing required file: $path" }
    }
    $Result.services = @(Get-Service BFE, MpsSvc | ForEach-Object { [ordered]@{ name = $_.Name; status = [string]$_.Status } })
    if (@($Result.services | Where-Object { $_.status -ne 'Running' }).Count -gt 0) { throw 'Windows Firewall/BFE service is not running; test cannot claim kernel enforcement.' }
    $Result.profiles = @(Get-NetFirewallProfile -PolicyStore ActiveStore | ForEach-Object { [ordered]@{ name = [string]$_.Name; enabled = [string]$_.Enabled; allow_local_rules = [string]$_.AllowLocalFirewallRules; default_outbound_action = [string]$_.DefaultOutboundAction } })
    $Result.active_profiles = @(Get-NetConnectionProfile | ForEach-Object { if ([string]$_.NetworkCategory -eq 'DomainAuthenticated') { 'Domain' } else { [string]$_.NetworkCategory } } | Select-Object -Unique)
    if ($Result.active_profiles.Count -eq 0) { throw 'Cannot determine an active network profile.' }
    foreach ($profileName in $Result.active_profiles) {
        $profileDetails = @($Result.profiles | Where-Object { $_.name -eq $profileName })
        if ($profileDetails.Count -ne 1 -or $profileDetails[0].enabled -ne 'True' -or $profileDetails[0].allow_local_rules -eq 'False') { throw "Active firewall profile does not enforce local rules: $profileName" }
    }

    $ProbeSource = Join-Path $RunDirectory 'network-probe.cs'
    $Code = @'
using System;
using System.Diagnostics;
using System.Net;
using System.Net.Sockets;
using System.Text;
public static class NetworkProbe {
    static string Quote(string text) {
        if (text == null) return "null";
        return "\"" + text.Replace("\\", "\\\\").Replace("\"", "\\\"").Replace("\r", "\\r").Replace("\n", "\\n") + "\"";
    }
    public static int Main(string[] args) {
        Console.OutputEncoding = new UTF8Encoding(false);
        bool connected = false, timedOut = false;
        int? nativeCode = null;
        string socketError = null, error = null;
        Stopwatch watch = Stopwatch.StartNew();
        using (Socket socket = new Socket(AddressFamily.InterNetwork, SocketType.Stream, ProtocolType.Tcp)) {
            try {
                IAsyncResult pending = socket.BeginConnect(IPAddress.Parse(args[0]), Int32.Parse(args[1]), null, null);
                using (pending.AsyncWaitHandle) {
                    if (pending.AsyncWaitHandle.WaitOne(Int32.Parse(args[2]))) { socket.EndConnect(pending); connected = socket.Connected; }
                    else { timedOut = true; error = "TCP connection did not complete before the probe timeout"; }
                }
            } catch (SocketException ex) { nativeCode = ex.NativeErrorCode; socketError = ex.SocketErrorCode.ToString(); error = ex.ToString(); }
              catch (Exception ex) { error = ex.ToString(); }
        }
        watch.Stop();
        if (error != null) Console.Error.WriteLine(error);
        Console.WriteLine("{\"connected\":" + (connected ? "true" : "false") + ",\"native_error_code\":" + (nativeCode.HasValue ? nativeCode.Value.ToString() : "null") + ",\"socket_error\":" + Quote(socketError) + ",\"timed_out\":" + (timedOut ? "true" : "false") + ",\"elapsed_ms\":" + watch.ElapsedMilliseconds + ",\"pid\":" + Process.GetCurrentProcess().Id + ",\"error\":" + Quote(error) + "}");
        return 0;
    }
}
'@
    [IO.File]::WriteAllText($ProbeSource, $Code, $Utf8)
    $Compiler = Join-Path $env:SystemRoot 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
    if (-not (Test-Path -LiteralPath $Compiler)) { throw "Local .NET Framework C# compiler not found: $Compiler" }
    $compile = Invoke-Captured -Executable $Compiler -Arguments @('/nologo', '/target:exe', '/platform:anycpu', ('/out:' + $ProbeExe), $ProbeSource) -Label 'probe-compile'
    if ($compile.timed_out -or $compile.exit_code -ne 0) { throw "C# probe compilation failed (timeout=$($compile.timed_out)). stdout: $($compile.stdout) stderr: $($compile.stderr)" }
    $Result['program_sha256'] = [ordered]@{ gui = (Get-FileHash -Algorithm SHA256 -LiteralPath $GuiExe).Hash; cli = (Get-FileHash -Algorithm SHA256 -LiteralPath $CliExe).Hash; probe = (Get-FileHash -Algorithm SHA256 -LiteralPath $ProbeExe).Hash }
    foreach ($address in $ProbeAddresses) {
        $parsed = [Net.IPAddress]::Parse($address)
        $bytes = $parsed.GetAddressBytes()
        if ($parsed.AddressFamily -ne [Net.Sockets.AddressFamily]::InterNetwork -or [Net.IPAddress]::IsLoopback($parsed) -or $bytes[0] -eq 10 -or ($bytes[0] -eq 172 -and $bytes[1] -ge 16 -and $bytes[1] -le 31) -or ($bytes[0] -eq 192 -and $bytes[1] -eq 168)) { throw "A public IPv4 probe is required: $address" }
        $probe = Invoke-Probe $address ('probe-baseline-' + $address.Replace('.', '-'))
        $Result.baseline_attempts += $probe
        if ($probe.connected) { $SelectedAddress = $address; break }
    }
    if (-not $SelectedAddress) { throw 'No reachable public TCP positive control. A generic network failure cannot prove firewall blocking.' }

    $programs = @($GuiExe, $CliExe, $ProbeExe)
    for ($index = 0; $index -lt $programs.Count; $index++) {
        [void](New-NetFirewallRule -Name $RuleNames[$index] -DisplayName $RuleNames[$index] -Group ('LocalPDFEPUB-offline-' + $RunId) -Direction Outbound -Action Block -Enabled True -Profile Any -Program $programs[$index])
        $rule = Get-NetFirewallRule -PolicyStore ActiveStore -Name $RuleNames[$index]
        $application = $rule | Get-NetFirewallApplicationFilter
        $Result.effective_rules += [ordered]@{ name = $rule.Name; enabled = [string]$rule.Enabled; direction = [string]$rule.Direction; action = [string]$rule.Action; profile = [string]$rule.Profile; program = $application.Program }
        if ([string]$rule.Enabled -ne 'True' -or [string]$rule.Action -ne 'Block' -or [string]$rule.Direction -ne 'Outbound' -or $application.Program -ine $programs[$index]) { throw "Firewall rule not effective as requested: $($RuleNames[$index])" }
    }
    Start-Sleep -Milliseconds 500
    $Result.blocked_probe = Invoke-Probe $SelectedAddress 'probe-blocked'
    if ($Result.blocked_probe.connected) { throw 'The controlled probe still connects with its outbound Block rule installed.' }

    $job = Join-Path $RunDirectory '中文任务'
    $epub = Join-Path $RunDirectory '断网验收.epub'
    $Result.conversion = Invoke-Captured -Executable $CliExe -Arguments @('convert', $InputPdf, '--job', $job, '--resources', $ResourcesDirectory, '--output', $epub) -Label 'conversion' -TimeoutSeconds $ConversionTimeoutSeconds -Observe
    if ($Result.conversion.timed_out -or $Result.conversion.exit_code -ne 0) { throw "Frozen CLI failed ($($Result.conversion.exit_code), timeout=$($Result.conversion.timed_out)). stdout: $($Result.conversion.stdout) stderr: $($Result.conversion.stderr)" }
    if ($Result.conversion.observation_errors.Count -gt 0 -or $Result.conversion.samples.Count -eq 0) { throw 'TCP process observation was unavailable; see the captured errors.' }
    $connected = @($Result.conversion.samples | ForEach-Object { $_.tcp } | Where-Object { $_.state -eq 'Established' })
    $Result.conversion['established_sockets_observed'] = ($connected.Count -gt 0)
    if ($connected.Count -gt 0) { throw 'An established TCP socket was observed on the frozen CLI PID.' }
    $jobState = [IO.File]::ReadAllText((Join-Path $job 'job.json'), $Utf8) | ConvertFrom-Json
    $Result.conversion['page_statuses'] = @($jobState.pages | ForEach-Object { [ordered]@{ page = $_.number; status = $_.status; error = $_.error } })
    $Result.conversion['epub'] = $epub
    if ($jobState.status -ne 'complete' -or @($jobState.pages | Where-Object { $_.status -ne 'success' }).Count -gt 0 -or -not (Test-Path -LiteralPath $epub)) { throw 'Conversion did not produce a complete EPUB with all input pages successful.' }
    $Result.status = 'conversion_passed_pending_cleanup'
} catch {
    $Errors.Add($_.Exception.ToString())
    $Result.status = 'failed'
} finally {
    foreach ($name in $RuleNames) {
        try {
            # Query failure is not evidence of absence. Enumerating before the
            # exact-name filter also avoids treating an ordinary missing rule as an error.
            $existing = @(Get-NetFirewallRule -PolicyStore PersistentStore -ErrorAction Stop | Where-Object { $_.Name -eq $name })
            if ($existing.Count -gt 0) { Remove-NetFirewallRule -Name $name -PolicyStore PersistentStore -ErrorAction Stop }
            $remaining = @(Get-NetFirewallRule -PolicyStore PersistentStore -ErrorAction Stop | Where-Object { $_.Name -eq $name })
            $remainingActive = @(Get-NetFirewallRule -PolicyStore ActiveStore -ErrorAction Stop | Where-Object { $_.Name -eq $name })
            $absent = $remaining.Count -eq 0 -and $remainingActive.Count -eq 0
            $Cleanup.Add([ordered]@{ name = $name; absent_after_cleanup = $absent })
            if (-not $absent) { $Errors.Add("Cleanup failed: $name remains installed") }
        } catch { $Cleanup.Add([ordered]@{ name = $name; absent_after_cleanup = $false; error = $_.Exception.ToString() }); $Errors.Add($_.Exception.ToString()) }
    }
}

if ($SelectedAddress -and $Result.blocked_probe -and @($Cleanup | Where-Object { -not $_.absent_after_cleanup }).Count -eq 0) {
    try {
        $Result.restored_probe = Invoke-Probe $SelectedAddress 'probe-restored'
        $Result.probe_policy_verified = (-not $Result.blocked_probe.connected) -and $Result.restored_probe.connected
        if (-not $Result.probe_policy_verified) { $Errors.Add('Public positive control did not recover after removing our rules; enforcement evidence is inconclusive.') }
    } catch { $Errors.Add($_.Exception.ToString()) }
}
if ($Result.status -eq 'conversion_passed_pending_cleanup' -and $Errors.Count -eq 0 -and $Result.probe_policy_verified) { $Result.status = 'passed' } else { $Result.status = 'failed' }
$Result.cleanup = $Cleanup.ToArray()
$Result.errors = $Errors.ToArray()
$Result['finished_utc'] = [DateTime]::UtcNow.ToString('o')
$ReportPath = Join-Path $RunDirectory 'result.json'
Write-Json $ReportPath $Result
if ($Errors.Count -gt 0) { Write-Error -Message ($Errors -join [Environment]::NewLine) -ErrorAction Continue }
[ordered]@{ status = $Result.status; report = $ReportPath; probe_policy_verified = $Result.probe_policy_verified; conversion_exit_code = if ($Result.conversion) { $Result.conversion.exit_code } else { $null }; rules_cleaned = (@($Cleanup | Where-Object { -not $_.absent_after_cleanup }).Count -eq 0) } | ConvertTo-Json -Depth 5
if ($Result.status -eq 'passed') { exit 0 } else { exit 1 }
