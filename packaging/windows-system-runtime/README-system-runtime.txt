LocalPDFtoEPUB 0.1.6 — Windows x64 系统运行库版

1. 完整解压到个人可写的普通本地文件夹，例如 D:\Applications\LocalPDFtoEPUB。
2. 双击 Start-LocalPDFtoEPUB.cmd。无需安装 Python 或 Visual Studio。
3. 在应用资源设置中导入 OCR 离线资源包，再选择 PDF 转换。

前置条件：Windows 10 / 11 x64；Microsoft Visual C++ v14 x64 运行库，
版本至少 14.44.35229.0。若启动器提示缺项，请自行从微软官网下载并安装：
https://aka.ms/vc14/vc_redist.x64.exe
官方说明：https://learn.microsoft.com/cpp/windows/latest-supported-vc-redist
离线电脑可使用另一台电脑下载的官方安装器。安装器许可由用户自己确认。
本 ZIP 不包含微软 VC/UCRT DLL 或运行库安装器，也不安装 Visual Studio。

启动器只检查本机 System32 文件的版本与 SHA-256。普通 VC DLL 直接由系统提供；
Shapely 使用特殊文件名，因此启动器将用户本机已安装的 MSVCP140.dll 按原字节复制到
_internal\Shapely.libs\msvcp140-0fa7eb792d3fbcf2233e4ea47e9144b9.dll。
该本地副本仍含原文件许可/签名，启动时会与当前系统文件核对并按需更新。
首次启动应使用 CMD，直接打开 EXE 可能因缺少此本地副本而失败。
复制记录保存在 system-runtime.local.json，仅在本机生成，不随发布 ZIP 提供。
这是用户在同一电脑上使用已安装运行库；不要把准备后的微软 DLL 副本作为本项目
原始发行包再次分发。微软运行库许可：https://visualstudio.microsoft.com/license-terms/vs2022-cruntime/

启动器无需管理员权限，不下载、不安装、不修改系统 DLL/注册表/全局 PATH；
PowerShell 的执行设置仅限本次启动进程。系统安全策略禁止脚本时请联系管理员。
应用尚未商业代码签名。请只使用项目官方发布文件，遵循系统安全提示。
此包依然可在资源准备后离线转换；GPU 运行包为独立可选资源。
CLI 调试前可运行：powershell -NoProfile -ExecutionPolicy Bypass -File .\Prepare-System-Runtime.ps1 -PrepareOnly
然后运行 LocalPDFtoEPUB-CLI.exe。

软件与第三方许可见 _internal\docs\THIRD_PARTY.md 和 THIRD_PARTY_SOURCES.md。
