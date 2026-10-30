[CmdletBinding()]
param([switch]$PrepareOnly)
# Local preparation only: no downloads, installation, elevation, registry writes,
# global PATH changes, or system DLL modifications. See README-system-runtime.txt.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$utf8 = [Text.UTF8Encoding]::new($false)
$minimum = [Version]'14.44.35229.0'
$officialDownload = 'https://aka.ms/vc14/vc_redist.x64.exe'
try {
    if (-not [Environment]::Is64BitOperatingSystem -or -not [Environment]::Is64BitProcess) {
        throw '需要 Windows 10 / 11 x64。请使用 Start-LocalPDFtoEPUB.cmd 启动。'
    }
    if ([Environment]::OSVersion.Version.Major -lt 10) { throw '此包仅支持 Windows 10 / 11 x64。' }
    $systemDir = [Environment]::SystemDirectory
    $required = @('vcruntime140.dll','vcruntime140_1.dll','msvcp140.dll','msvcp140_1.dll','msvcp140_2.dll')
    $checked = @()
    $missing = @()
    foreach ($name in $required) {
        $path = Join-Path $systemDir $name
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { $missing += $name; continue }
        $info = [Diagnostics.FileVersionInfo]::GetVersionInfo($path)
        $version = [Version]::new($info.FileMajorPart,$info.FileMinorPart,$info.FileBuildPart,$info.FilePrivatePart)
        if ($version -lt $minimum) { $missing += ($name + '：' + $version + '，需要至少 ' + $minimum); continue }
        $checked += [ordered]@{name=$name; path=$path; version=$version.ToString(); sha256=(Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()}
    }
    if (-not (Test-Path -LiteralPath (Join-Path $systemDir 'ucrtbase.dll') -PathType Leaf)) {
        throw '缺少 Windows 系统 UCRT（ucrtbase.dll）。请修复或更新 Windows；本程序不会修改系统文件。'
    }
    if ($missing.Count) {
        throw ('缺少或过旧的 Microsoft Visual C++ x64 运行库：' + ($missing -join '；') + "`n请自行安装微软官方 v14 x64 运行库（无需 Visual Studio）：" + $officialDownload + "`n离线电脑可从另一台电脑下载官方安装器后复制安装。此启动器不会联网下载或安装。")
    }
    $internalDir = Join-Path $PSScriptRoot '_internal'
    $libraryDir = Join-Path $internalDir 'Shapely.libs'
    $localCopy = Join-Path $libraryDir 'msvcp140-0fa7eb792d3fbcf2233e4ea47e9144b9.dll'
    foreach ($directory in @($PSScriptRoot,$internalDir,$libraryDir)) {
        if (-not (Test-Path -LiteralPath $directory -PathType Container)) { throw ('应用解压不完整：' + $directory) }
        if ((Get-Item -LiteralPath $directory).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw '请把应用解压到普通本地文件夹，不能通过目录链接准备本机运行库。' }
    }
    if ((Test-Path -LiteralPath $localCopy) -and ((Get-Item -LiteralPath $localCopy).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw '应用私有运行库位置是链接，无法安全准备；请重新解压应用到新文件夹。'
    }
    $systemCopy = Join-Path $systemDir 'msvcp140.dll'
    $systemHash = (Get-FileHash -LiteralPath $systemCopy -Algorithm SHA256).Hash.ToLowerInvariant()
    $copyNeeded = -not (Test-Path -LiteralPath $localCopy -PathType Leaf)
    if (-not $copyNeeded) { $copyNeeded = (Get-FileHash -LiteralPath $localCopy -Algorithm SHA256).Hash.ToLowerInvariant() -ne $systemHash }
    if ($copyNeeded) {
        Write-Host '正在将本机已安装的 MSVCP140.dll 复制到此应用的 Shapely 私有目录；不修改系统。'
        $temporaryCopy = Join-Path $libraryDir ('runtime-' + [Guid]::NewGuid().ToString('N') + '.tmp')
        try {
            Copy-Item -LiteralPath $systemCopy -Destination $temporaryCopy
            if ((Get-FileHash -LiteralPath $temporaryCopy -Algorithm SHA256).Hash.ToLowerInvariant() -ne $systemHash) { throw '本机运行库副本校验失败。' }
            Move-Item -LiteralPath $temporaryCopy -Destination $localCopy -Force
        } finally { if (Test-Path -LiteralPath $temporaryCopy) { Remove-Item -LiteralPath $temporaryCopy -Force } }
    }
    if ((Get-FileHash -LiteralPath $localCopy -Algorithm SHA256).Hash.ToLowerInvariant() -ne $systemHash) { throw '本机运行库副本与系统文件不一致。' }
    $receipt = [ordered]@{schema=1; minimum_version=$minimum.ToString(); system_runtime=$checked; copied_from=$systemCopy; copied_to=$localCopy; copied_sha256=$systemHash; prepared_utc=[DateTime]::UtcNow.ToString('o'); network_used=$false}
    [IO.File]::WriteAllText((Join-Path $PSScriptRoot 'system-runtime.local.json'), (($receipt | ConvertTo-Json -Depth 6) + "`n"), $utf8)
    Write-Host ('本机 VC++ x64 运行库检查通过（至少 ' + $minimum + '）。')
    if (-not $PrepareOnly) {
        $executable = Join-Path $PSScriptRoot 'LocalPDFtoEPUB.exe'
        if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) { throw '未找到 LocalPDFtoEPUB.exe，请完整解压应用。' }
        Start-Process -FilePath $executable -WorkingDirectory $PSScriptRoot | Out-Null
    }
    exit 0
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    [Console]::Error.WriteLine('如应用目录不可写或文件正在使用，请退出此目录的应用，或解压到个人可写的新文件夹后重试。')
    exit 1
}
