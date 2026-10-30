[CmdletBinding()]
param(
    [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$AppDir = '',
    [string]$Resources = '',
    [string]$EvidenceRoot = '',
    [switch]$VerifyPageNumberRepair,
    [switch]$VerifyRebuildLayout,
    [ValidateSet('directml','cuda','fallback','strict')][string]$GpuMode = 'directml',
    [ValidateSet('directml','cuda')][string]$GpuBackend = 'directml',
    [Parameter(Mandatory=$true)][string]$GpuRuntime,
    [switch]$Child,
    [string]$RunDirectory = ''
)
# Explicit package acceptance only. No SendKeys, desktop scraping, or external
# UI automation: the package's own gui-test runs its real Qt event loop.
$ErrorActionPreference = 'Stop'
$ProjectRoot = [IO.Path]::GetFullPath($ProjectRoot)
if (-not $AppDir) { $AppDir = Join-Path $ProjectRoot 'dist\LocalPDFtoEPUB' }
if (-not $Resources) { $Resources = Join-Path $ProjectRoot 'resources' }
if (-not $EvidenceRoot) { $EvidenceRoot = Join-Path $ProjectRoot 'artifacts\windows-gui' }
$AppDir = [IO.Path]::GetFullPath($AppDir)
$Resources = [IO.Path]::GetFullPath($Resources)
$EvidenceRoot = [IO.Path]::GetFullPath($EvidenceRoot)
$guiExe = Join-Path $AppDir 'LocalPDFtoEPUB.exe'
$cliExe = Join-Path $AppDir 'LocalPDFtoEPUB-CLI.exe'
$fixture = Join-Path $ProjectRoot 'tests\fixtures\02_mixed_bad_layer.pdf'
if ($VerifyPageNumberRepair) { $fixture = Join-Path $ProjectRoot 'tests\fixtures\04_page_number_gap.pdf' }
$GpuRuntime = [IO.Path]::GetFullPath($GpuRuntime)
if ($GpuMode -in @('directml','cuda')) { $GpuBackend = $GpuMode }
$gpuPython = Join-Path $GpuRuntime 'python.exe'
$allowedExecutables = @($guiExe, $cliExe)
if (Test-Path -LiteralPath $gpuPython -PathType Leaf) { $allowedExecutables += $gpuPython }
$utf8 = [Text.UTF8Encoding]::new($false)

function Write-JsonFile([string]$Path, $Value) {
    [IO.File]::WriteAllText($Path, ((ConvertTo-Json -InputObject $Value -Depth 14) + "`n"), $script:utf8)
}
function Read-JsonFile([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    try { return ([IO.File]::ReadAllText($Path, $script:utf8) | ConvertFrom-Json) }
    catch { return $null } # A live sampling file may be between writes.
}
function Quote-Argument([string]$Value) {
    if ($Value.Contains('"')) { throw 'A native argument contains an unsupported quote.' }
    return '"' + ($Value -replace '(\\+)$', '${1}${1}') + '"'
}
function Process-Identity([int]$ProcessId) {
    $candidate = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if (-not $candidate) { return $null }
    try {
        return [pscustomobject]@{
            pid = $ProcessId; executable = $candidate.Path
            start_ticks = $candidate.StartTime.ToUniversalTime().Ticks.ToString()
            session_id = $candidate.SessionId
        }
    } catch { return $null }
}
function Stop-ExactProcess($Record) {
    if (-not $Record -or $script:allowedExecutables -notcontains $Record.executable) { return }
    $current = Process-Identity ([int]$Record.pid)
    if ($current -and $current.executable -ieq $Record.executable -and
        $current.start_ticks -eq $Record.start_ticks) {
        Stop-Process -Id ([int]$Record.pid) -Force -ErrorAction SilentlyContinue
    }
}
function Assert-Inputs {
    if ($GpuMode -eq 'strict' -and $VerifyRebuildLayout) {
        throw 'Strict missing-runtime acceptance has no successful page cache to rebuild.'
    }
    if ($GpuMode -in @('directml','cuda') -and -not (Test-Path -LiteralPath $gpuPython -PathType Leaf)) {
        throw 'Actual GPU acceptance requires its complete independent runtime.'
    }
    foreach ($path in @($script:guiExe, $script:cliExe, $script:fixture)) {
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Missing acceptance input: $path" }
    }
    if (-not (Test-Path -LiteralPath $script:Resources -PathType Container)) {
        throw "Missing local resources: $script:Resources"
    }
}

if ($Child) {
    if (-not $RunDirectory) { throw '-Child requires the parent-created -RunDirectory.' }
    $RunDirectory = [IO.Path]::GetFullPath($RunDirectory)
    $evidencePrefix = $EvidenceRoot.TrimEnd('\') + '\'
    if (-not $RunDirectory.StartsWith($evidencePrefix, [StringComparison]::OrdinalIgnoreCase) -or
        (Split-Path -Leaf $RunDirectory) -notmatch '^run-[0-9TZ-]+-[0-9a-f]{32}$' -or
        -not (Test-Path -LiteralPath $RunDirectory -PathType Container)) {
        throw 'Child output must be the parent-created GUID run directory.'
    }
    $tracked = @{}
    $moduleSamples = [Collections.Generic.List[object]]::new()
    $tcpSamples = [Collections.Generic.List[object]]::new()
    $sampleErrors = [Collections.Generic.List[string]]::new()
    $completed = $false
    $failureMessage = $null
    $guiResult = $null
    $guiProcess = $null
    $childSession = [Diagnostics.Process]::GetCurrentProcess().SessionId
    $startedUtc = [DateTime]::UtcNow
    $childTimer = [Diagnostics.Stopwatch]::StartNew()
    $guiModulesReady = $false
    $moduleSampled = @{}
    $childDonePath = Join-Path $RunDirectory 'child-complete.json'
    try {
        Assert-Inputs
        if ($childSession -le 0) { throw 'Scheduled GUI acceptance must run in an interactive session above session 0.' }
        foreach ($variable in @('PYTHONPATH','PYTHONHOME','PYTHONSTARTUP','PYTHONUTF8',
            'VIRTUAL_ENV','CONDA_PREFIX','CONDA_DEFAULT_ENV','CONDA_PYTHON_EXE',
            'QT_PLUGIN_PATH','QT_QPA_PLATFORM_PLUGIN_PATH','QT_QPA_PLATFORM','QT_DEBUG_PLUGINS',
            'LOCAL_EPUB_RESOURCES','LOCAL_EPUB_EPUBCHECK','DYLD_LIBRARY_PATH','DYLD_FRAMEWORK_PATH')) {
            Remove-Item -LiteralPath "Env:$variable" -ErrorAction SilentlyContinue
        }
        $env:PATH = "$env:SystemRoot\System32;$env:SystemRoot;$env:SystemRoot\System32\Wbem"
        $env:PYTHONIOENCODING = 'ascii' # The production worker must explicitly emit UTF-8.
        $outputFile = Join-Path $RunDirectory 'book.epub'
        $guiEvidence = Join-Path $RunDirectory 'gui'
        $arguments = @('gui-test','--source',$fixture,'--resources',$Resources,
            '--output',$outputFile,'--evidence',$guiEvidence,'--timeout','240',
            '--verify-gpu-mode',$GpuMode,'--gpu-backend',$GpuBackend,'--gpu-runtime',$GpuRuntime)
        if ($GpuMode -ne 'strict') { $arguments += '--verify-cancel' }
        if ($VerifyPageNumberRepair) { $arguments += '--verify-page-number-repair' }
        if ($VerifyRebuildLayout) { $arguments += '--verify-rebuild-layout' }
        $argumentLine = ($arguments | ForEach-Object { Quote-Argument $_ }) -join ' '
        Write-JsonFile (Join-Path $RunDirectory 'child-status.json') ([ordered]@{
            phase='launching'; child_pid=$PID; session_id=$childSession; utc=$startedUtc.ToString('o')
            executable=$guiExe; developer_environment_removed=$true; path=$env:PATH
            pythonioencoding=$env:PYTHONIOENCODING
        })
        $guiProcess = Start-Process -FilePath $guiExe -ArgumentList $argumentLine -WorkingDirectory $RunDirectory -PassThru
        $null = $guiProcess.Handle
        $identity = Process-Identity $guiProcess.Id
        if (-not $identity) { throw 'Could not record the launched GUI process identity.' }
        $tracked[$identity.pid] = $identity
        Write-JsonFile (Join-Path $RunDirectory 'processes.json') @($tracked.Values)
        while ($childTimer.Elapsed.TotalSeconds -lt 270) {
            $guiProcess.Refresh()
            if ($guiProcess.HasExited) { break }
            # Follow only descendants of this exact test GUI/CLI. Never collect
            # or terminate unrelated Python processes using the same runtime.
            foreach ($ancestor in @($tracked.Values)) {
                $alive = Process-Identity ([int]$ancestor.pid)
                if (-not $alive -or $alive.start_ticks -ne $ancestor.start_ticks) { continue }
                foreach ($worker in @(Get-CimInstance Win32_Process -Filter "ParentProcessId = $($ancestor.pid)" -ErrorAction SilentlyContinue)) {
                    if ($allowedExecutables -contains $worker.ExecutablePath) {
                        $workerIdentity = Process-Identity ([int]$worker.ProcessId)
                        if ($workerIdentity) { $tracked[$workerIdentity.pid] = $workerIdentity }
                    }
                }
            }
            Write-JsonFile (Join-Path $RunDirectory 'processes.json') @($tracked.Values)
            foreach ($record in @($tracked.Values)) {
                $live = Process-Identity ([int]$record.pid)
                if (-not $live -or $live.start_ticks -ne $record.start_ticks) { continue }
                if (-not $moduleSampled.ContainsKey($record.pid)) {
                    try {
                        $paths = @(Get-Process -Id ([int]$record.pid) -ErrorAction Stop | ForEach-Object {
                            $_.Modules | ForEach-Object { $_.FileName }
                        })
                        $hasPython = @($paths | Where-Object { $_ -match '[\\/]python3(?:12)?\.dll$' }).Count -gt 0
                        $hasQt = @($paths | Where-Object { $_ -match '[\\/]Qt6Core\.dll$' }).Count -gt 0
                        $runtimeReady = $hasPython -and (($record.pid -ne $guiProcess.Id) -or $hasQt)
                        if ($runtimeReady) {
                            $moduleSamples.Add([ordered]@{utc=[DateTime]::UtcNow.ToString('o'); pid=$record.pid
                                executable=$record.executable; paths=$paths})
                            $moduleSampled[$record.pid] = $true
                            if ($record.pid -eq $guiProcess.Id) { $guiModulesReady = $true }
                        }
                    } catch { $sampleErrors.Add("Module sample $($record.pid): $($_.Exception.Message)") }
                }
                try {
                    $connections = @(Get-NetTCPConnection -OwningProcess ([int]$record.pid) -ErrorAction SilentlyContinue |
                        Select-Object LocalAddress,LocalPort,RemoteAddress,RemotePort,State,OwningProcess)
                    $tcpSamples.Add([ordered]@{utc=[DateTime]::UtcNow.ToString('o'); pid=$record.pid; connections=$connections})
                } catch { $sampleErrors.Add("TCP sample $($record.pid): $($_.Exception.Message)") }
            }
            Start-Sleep -Milliseconds 200
        }
        $guiProcess.Refresh()
        if (-not $guiProcess.HasExited) { throw 'GUI acceptance exceeded the child timeout.' }
        $guiProcess.WaitForExit()
        $guiResult = Read-JsonFile (Join-Path $guiEvidence 'result.json')
        if (-not $guiResult) { throw "Packaged GUI exited without its result.json (exit $($guiProcess.ExitCode))." }
        $devModules = @($moduleSamples | ForEach-Object { $_.paths } | Where-Object {
            $_ -match '(?i)(^|[\\/])(?:anaconda\d*|miniconda\d*|\.venv|venv)([\\/]|$)'
        })
        if (-not $guiModulesReady) { throw 'No live GUI module sample containing bundled Python and Qt was captured.' }
        if ($devModules.Count -gt 0) { throw 'A development-environment module path was observed.' }
        if ($tcpSamples.Count -eq 0) { throw 'No PID-scoped TCP samples were collected.' }
        $externalConnections = @($tcpSamples | ForEach-Object { $_.connections } | Where-Object {
            $_.RemoteAddress -notin @('127.0.0.1','::1','0.0.0.0','::') -and
            $_.State.ToString() -in @('Established','SynSent','SynReceived')
        })
        if ($externalConnections.Count -gt 0) { throw 'An external TCP connection or attempt was observed for a test process.' }
        if ($guiProcess.ExitCode -ne 0 -or $guiResult.status -ne 'done' -or -not $guiResult.frozen -or
            $guiResult.platform_plugin -ne 'windows' -or [int]$guiResult.session.id -le 0 -or
            [int]$guiResult.session.id -ne $childSession -or [int]$guiResult.pid -ne $guiProcess.Id -or
            $guiResult.executable -ine $guiExe -or $guiResult.worker_executable -ine $cliExe -or
            ($GpuMode -ne 'strict' -and -not $guiResult.cancellation_and_resume_tested)) {
            throw 'The result did not prove a successful frozen Windows GUI in the interactive session.'
        }
        if (@($guiResult.workers | Where-Object { $_.executable -ine $cliExe }).Count -gt 0) {
            throw 'A worker ran outside the packaged CLI executable.'
        }
        if (-not $guiResult.gpu_mode_tested -or $guiResult.gpu_mode_requested -ne $GpuMode -or
            $guiResult.gpu_backend_requested -ne $GpuBackend) { throw 'Packaged GUI did not prove requested GPU mode.' }
        if ($GpuMode -in @('directml','cuda')) {
            $observedGpu = @($tracked.Values | Where-Object { $_.executable -ieq $gpuPython })
            if (-not $observedGpu.Count) { throw 'No descendant independent GPU runtime process was captured.' }
            if (-not @($moduleSamples | Where-Object { $_.executable -ieq $gpuPython }).Count) {
                throw 'No live independent GPU worker module sample was captured.'
            }
        }
        if ($GpuMode -ne 'strict') {
        foreach ($field in @('stable_native_items_on_export','running_margins_available_default_off',
            'running_margins_on_off_reexport_tested','body_only_without_page_info_appendix','export_kept_parse_cache')) {
            if (-not $guiResult.$field) { throw "The packaged GUI did not verify $field." }
        }
        if (-not (Test-Path -LiteralPath (Join-Path $guiEvidence 'running-margins.epub') -PathType Leaf)) {
            throw 'The actual enabled-margin GUI export was not preserved for independent format/reader checks.'
        }
        if ($VerifyPageNumberRepair -and -not $guiResult.page_number_repair_tested) {
            throw 'The result did not verify explicit page-number confirmation and undo.'
        }
        if ($VerifyRebuildLayout -and (-not $guiResult.layout_rebuild_requested -or
            -not $guiResult.layout_rebuild_tested -or -not $guiResult.layout_rebuild_evidence.raw_unchanged -or
            -not $guiResult.layout_rebuild_evidence.unselected_pages_unchanged -or
            -not $guiResult.layout_rebuild_evidence.manual_edit_preserved)) {
            throw 'The result did not verify selected-page layout rebuild, immutable OCR cache, and preserved correction.'
        }
        if ($VerifyPageNumberRepair) {
            if ($guiResult.page_number_confirmed_record.provenance -ne 'user_confirmed_inference' -or
                $guiResult.page_number_confirmed_record.label -ne '22' -or
                $null -ne $guiResult.page_number_confirmed_record.confidence -or
                -not (Test-Path -LiteralPath (Join-Path $guiEvidence 'page-number-confirmed.epub') -PathType Leaf)) {
                throw 'The confirmed label 22 or its human-inference identity was not preserved.'
            }
        } elseif (-not $guiResult.scanned_page_numbers_verified) {
            throw 'The mixed fixture did not verify actual OCR page labels 2 and 3.'
        }
        }
        $completed = $true
    } catch {
        $failureMessage = $_.Exception.Message
    } finally {
        foreach ($record in @($tracked.Values)) { Stop-ExactProcess $record }
        Write-JsonFile (Join-Path $RunDirectory 'process-modules.json') @($moduleSamples)
        Write-JsonFile (Join-Path $RunDirectory 'tcp-samples.json') @($tcpSamples)
        Write-JsonFile $childDonePath ([ordered]@{
            success=$completed; error=$failureMessage; started_utc=$startedUtc.ToString('o')
            finished_utc=[DateTime]::UtcNow.ToString('o'); child_pid=$PID; session_id=$childSession
            gui_pid=$(if($guiProcess){$guiProcess.Id}else{$null}); gui_result=$guiResult
            executable=$guiExe; worker_executable=$cliExe; tracked_processes=@($tracked.Values)
            gui_runtime_modules_observed=$guiModulesReady; module_samples=$moduleSamples.Count
            tcp_samples=$tcpSamples.Count; sample_errors=@($sampleErrors)
        })
    }
    if ($completed) { exit 0 } else { exit 1 }
}

# Parent: only app-specific outbound rules and one temporary interactive task.
# Never change adapters, global firewall policy, profiles, or SSH rules.
Assert-Inputs
$principalIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
$securityPrincipal = [Security.Principal.WindowsPrincipal]::new($principalIdentity)
if (-not $securityPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this acceptance script in the already-authorized elevated administrator session.'
}
$runId = [Guid]::NewGuid().ToString('N')
$RunDirectory = Join-Path $EvidenceRoot ('run-' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ') + '-' + $runId)
[IO.Directory]::CreateDirectory($RunDirectory) | Out-Null
$taskName = 'LocalPDFEPUB-GUIAcceptance-' + $runId
$probeExe = Join-Path $RunDirectory 'NetworkProbe.exe'
$blockedPrograms = @($allowedExecutables) + @($probeExe)
$ruleNames = @()
for ($index=0; $index -lt $blockedPrograms.Count; $index++) { $ruleNames += ($taskName + '-Program-' + $index) }
$networkBefore = $null
$networkBlocked = $null
$networkAfter = $null
$ruleAudit = @()
$firewallProfiles = @()
$activeProfiles = @()
$childResult = $null
$failureMessage = $null
$cleanupErrors = [Collections.Generic.List[string]]::new()
$cleanup = $null
$taskLastResult = $null
$parentStarted = [DateTime]::UtcNow
$parentTimer = [Diagnostics.Stopwatch]::StartNew()
try {
    $probeSource = @'
using System;
using System.Net;
using System.Net.Sockets;
public class NetworkProbe {
  public static void Main() {
    using (var socket=new Socket(AddressFamily.InterNetwork,SocketType.Stream,ProtocolType.Tcp)) {
      try {
        var pending=socket.BeginConnect(IPAddress.Parse("223.5.5.5"),443,null,null);
        if(!pending.AsyncWaitHandle.WaitOne(5000)) { Console.WriteLine("{\"connected\":false,\"timeout\":true}"); return; }
        socket.EndConnect(pending);Console.WriteLine("{\"connected\":true}");
      } catch(SocketException e) { Console.WriteLine("{\"connected\":false,\"winerror\":"+e.NativeErrorCode+"}"); }
    }
  }
}
'@
    Add-Type -TypeDefinition $probeSource -Language CSharp -OutputAssembly $probeExe -OutputType ConsoleApplication
    $networkBefore = (& $probeExe) | ConvertFrom-Json
    if (-not $networkBefore.connected) { throw 'Public network positive control unavailable before firewall test.' }
    $firewallProfiles = @(Get-NetFirewallProfile | Select-Object Name,Enabled)
    $activeProfiles = @(Get-NetConnectionProfile | ForEach-Object {
        if ($_.NetworkCategory.ToString() -eq 'DomainAuthenticated') { 'Domain' } else { $_.NetworkCategory.ToString() }
    } | Select-Object -Unique)
    if (-not $activeProfiles.Count -or @($firewallProfiles | Where-Object {
        $activeProfiles -contains $_.Name -and $_.Enabled.ToString() -ne 'True'
    }).Count -gt 0) { throw 'An active firewall profile is disabled or unknown; this run cannot claim outbound blocking.' }
    for ($index=0; $index -lt $blockedPrograms.Count; $index++) {
        New-NetFirewallRule -Name $ruleNames[$index] -DisplayName $ruleNames[$index] -Program $blockedPrograms[$index] `
            -Direction Outbound -Action Block -Enabled True -Profile Any | Out-Null
    }
    $ruleAudit = @(foreach ($name in $ruleNames) {
        $rule = Get-NetFirewallRule -PolicyStore ActiveStore -Name $name
        $filter = $rule | Get-NetFirewallApplicationFilter
        [ordered]@{name=$name; program=$filter.Program; enabled=$rule.Enabled.ToString()
            direction=$rule.Direction.ToString(); action=$rule.Action.ToString(); profile=$rule.Profile.ToString()}
    })
    if ($ruleAudit.Count -ne $blockedPrograms.Count -or @($ruleAudit | Where-Object {
        $_.enabled -ne 'True' -or $_.direction -ne 'Outbound' -or $_.action -ne 'Block' -or
        $blockedPrograms -notcontains $_.program
    }).Count -gt 0) { throw 'Application-specific outbound block rules did not match the requested scope.' }
    $networkBlocked = (& $probeExe) | ConvertFrom-Json
    if ($networkBlocked.connected -or $networkBlocked.winerror -ne 10013) { throw 'OS firewall probe did not produce native WSAEACCES 10013.' }
    Write-JsonFile (Join-Path $RunDirectory 'firewall-active.json') ([ordered]@{
        run_id=$runId; rules=$ruleAudit; profiles=$firewallProfiles; active_profiles=$activeProfiles
    })
    $powershellExe = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $taskArguments = @('-NoLogo','-NoProfile','-NonInteractive','-WindowStyle','Hidden','-ExecutionPolicy','Bypass',
        '-File',$PSCommandPath,'-ProjectRoot',$ProjectRoot,'-AppDir',$AppDir,'-Resources',$Resources,
        '-EvidenceRoot',$EvidenceRoot,'-Child','-RunDirectory',$RunDirectory,
        '-GpuMode',$GpuMode,'-GpuBackend',$GpuBackend,'-GpuRuntime',$GpuRuntime)
    if ($VerifyPageNumberRepair) { $taskArguments += '-VerifyPageNumberRepair' }
    if ($VerifyRebuildLayout) { $taskArguments += '-VerifyRebuildLayout' }
    $action = New-ScheduledTaskAction -Execute $powershellExe -Argument (($taskArguments | ForEach-Object { Quote-Argument $_ }) -join ' ')
    $taskPrincipal = New-ScheduledTaskPrincipal -UserId $principalIdentity.Name -LogonType Interactive -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Seconds 300) `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    Register-ScheduledTask -TaskName $taskName -Action $action -Principal $taskPrincipal -Settings $settings | Out-Null
    Start-ScheduledTask -TaskName $taskName
    $nextProgress = 0
    while ($parentTimer.Elapsed.TotalSeconds -lt 300) {
        $childResult = Read-JsonFile (Join-Path $RunDirectory 'child-complete.json')
        if ($childResult) { break }
        if ($parentTimer.Elapsed.TotalSeconds -ge $nextProgress) {
            $state = (Get-ScheduledTask -TaskName $taskName).State
            Write-Host ("GUI acceptance {0}: {1}, elapsed {2:n0}s" -f $runId,$state,$parentTimer.Elapsed.TotalSeconds)
            $nextProgress = $parentTimer.Elapsed.TotalSeconds + 10
        }
        Start-Sleep -Seconds 1
    }
    if (-not $childResult) { throw 'Interactive GUI acceptance did not produce completion evidence within 300 seconds.' }
    $taskLastResult = (Get-ScheduledTaskInfo -TaskName $taskName).LastTaskResult
    if (-not $childResult.success) { throw ('Interactive GUI acceptance failed: ' + $childResult.error) }
} catch {
    $failureMessage = $_.Exception.Message
} finally {
    foreach ($record in @(Read-JsonFile (Join-Path $RunDirectory 'processes.json'))) {
        try { Stop-ExactProcess $record } catch { $cleanupErrors.Add($_.Exception.Message) }
    }
    try {
        if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
            Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
            Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
        }
    } catch { $cleanupErrors.Add("Task cleanup: $($_.Exception.Message)") }
    foreach ($name in $ruleNames) {
        try {
            if (Get-NetFirewallRule -Name $name -ErrorAction SilentlyContinue) {
                Remove-NetFirewallRule -Name $name
            }
        } catch { $cleanupErrors.Add("Rule cleanup $name : $($_.Exception.Message)") }
    }
    if (Test-Path -LiteralPath $probeExe -PathType Leaf) {
        try { $networkAfter = (& $probeExe) | ConvertFrom-Json }
        catch { $cleanupErrors.Add("Restored network control: $($_.Exception.Message)") }
        if (-not $networkAfter.connected) { $cleanupErrors.Add('Public positive control did not recover after rule removal.') }
    }
    $remainingRules = @($ruleNames | ForEach-Object { Get-NetFirewallRule -Name $_ -ErrorAction SilentlyContinue })
    $remainingTask = @(Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue)
    $cleanup = [ordered]@{task_removed=($remainingTask.Count -eq 0); rules_removed=($remainingRules.Count -eq 0)
        remaining_rule_names=@($remainingRules | ForEach-Object {$_.Name}); errors=@($cleanupErrors)}
}
$success = (-not $failureMessage -and $childResult -and $childResult.success -and
    $cleanup.task_removed -and $cleanup.rules_removed -and $cleanupErrors.Count -eq 0)
$result = [ordered]@{
    success=[bool]$success; error=$failureMessage; run_id=$runId; run_directory=$RunDirectory
    started_utc=$parentStarted.ToString('o'); finished_utc=[DateTime]::UtcNow.ToString('o')
    parent_pid=$PID; parent_session_id=[Diagnostics.Process]::GetCurrentProcess().SessionId
    interactive_user=$principalIdentity.Name; task_name=$taskName; task_last_result=$taskLastResult
    outbound_rules=$ruleAudit; active_firewall_profiles=$activeProfiles; firewall_profiles=$firewallProfiles
    gpu_mode=$GpuMode; gpu_backend=$GpuBackend; gpu_runtime=$GpuRuntime
    verify_rebuild_layout=[bool]$VerifyRebuildLayout
    native_network_probe=[ordered]@{before=$networkBefore; blocked=$networkBlocked; after=$networkAfter; loopback_block_claimed=$false}
    cleanup=$cleanup; child=$childResult
    frozen_windows_interactive_passed=[bool]($childResult -and $childResult.success)
}
Write-JsonFile (Join-Path $RunDirectory 'parent-result.json') $result
$result | ConvertTo-Json -Depth 14
if ($success) { exit 0 } else { exit 1 }
