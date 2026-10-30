0.1.6 的修复、实际验收进度和交付状态统一见 [0.1.6 记录](RELEASE_0.1.6.md)；上一版构建与验证见 [0.1.5 发布记录](RELEASE_0.1.5.md)。页边阅读器的既有实机记录见 [0.1.4 发布记录](RELEASE_0.1.4.md)。原版 Vivliostyle 阅读器由本机应用提供资源并在系统浏览器运行，只监听 127.0.0.1；不影响转换进程的完全禁网。浏览器需现代 Chromium / Edge。历史版本记录不代替本次验证。

# 平台、构建与验证状态

开发机器：macOS 27.2，Apple Silicon arm64，Apple M6，32 GB 统一内存。CPU 和 GPU 共享这部分内存，不能把它描述为 32 GB 独立显存。主应用自带 ONNX Runtime CPU 基线，不要求 CUDA、NVIDIA 驱动或 Apple Metal。0.1.5 的 Windows 新任务默认尝试 DirectML，可选 NVIDIA CUDA 或 CPU；Mac 默认 CPU。GPU 资源缺失或失败时明确回退 CPU，旧任务无设备字段时保持 CPU。GPU 组件在独立子进程运行，不开推理服务端口，详见 [GPU 运行包](GPU_RUNTIME.md)。

本文件记录平台前提、构建方法与历史验证边界。下表的已通过项目是既有版本记录，不能代替 0.1.5 最终源码和发布包的重新验收；本版进度、包摘要与证据统一见 [0.1.5 发布记录](RELEASE_0.1.5.md)。

| 目标 | 实现 | 构建与实际验证 |
| --- | --- | --- |
| macOS arm64 | Python 3.12、PySide6、PDFium、ONNX Runtime CPU | 既有版本源码、独立包的CLI/原生GUI已在本机内核禁网下通过；0.1.5状态见本版记录。未公证，其他Mac版本待验证 |
| Windows x64 | Windows 新任务默认 DirectML，可选独立 NVIDIA CUDA 或 CPU；PyInstaller目录包含GUI/CLI和CPU运行库 | 既有版本在Windows 11 Pro 10.0.26300实机构建、全新任务CLI与活动桌面GUI通过，真实进程防火墙阻断通过；0.1.5状态见本版记录。全新系统及Windows 10待验证，CI未执行 |
| macOS Intel | Python逻辑保留兼容；须为x86_64独立重建最小OpenCV并打包 | 未验证；不能将arm64包用于Intel，当前发布脚本也会拒绝未经审查的官方Mac OpenCV wheel |

## 开发者构建

构建机需要目标平台的 Python 3.12 和锁定依赖；普通目标用户仅需完整应用目录和资源 ZIP。PyInstaller 不能在 macOS 上交叉生成 Windows 可运行包。

安装命令必须在项目根目录执行。`requirements.lock` 在 macOS arm64 上直接选择 `vendor/wheels/opencv_python-4.11.0.86-cp312-cp312-macosx_14_0_arm64.whl`；Windows x64 使用单独审查的官方 OpenCV 版本。该相对路径和平台 marker 已用本机 pip 与 uv 的 dry-run 实际验证。Mac wheel 要求 CPython 3.12、arm64、macOS 14 及以上，SHA-256 为 `31dc606faece9c9fc22e47f7eefcdda98c6d21f6d7f39cd20b77d92efdd937a1`。源码再构建方法与固定输入见 `packaging/OPENCV_MINIMAL.md`。

Mac 构建脚本会检查实际安装的 `cv2/LOCAL_BUILD.json` 标识及原生依赖，防止普通 PyPI 安装将同版本号的官方视频构建重新带入发布包；缺少本地 wheel 时应复制该 wheel 或按记录重建，不能静默替换。

macOS：

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python scripts/build.py
```

Windows x64 PowerShell：

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.lock
.venv\Scripts\python.exe scripts\build.py
```

生成 `dist/LocalPDFtoEPUB.app`（Mac）或 `dist/LocalPDFtoEPUB/`（Windows），并生成归档、摘要和 `build-info.json`。Mac 使用 `ditto` 归档以保留 `.app` 内的框架符号链接。Windows 可复制整个目录运行 `LocalPDFtoEPUB.exe`，不能只复制一个 exe。macOS `.app/Contents/MacOS/` 和 Windows 目录中包含 `LocalPDFtoEPUB-CLI` 后台程序；GUI 调用该程序避免冻结 GUI 的标准输出限制。模型资源在用户选定位置导入，不从当前开发虚拟环境暗中寻找模型。

`.github/workflows/build-windows.yml` 提供 Windows Server 2022 x64 构建、独立模型包生成和冻结 CLI 的全部自制样本转换测试；测试移除开发路径并将 PATH 限为系统目录，不自动发布到公众。CI runner 仍装有开发工具，且此工作流尚未在远端执行，不能取代下列干净系统验收。

## 目标系统前提

- 64 位受支持操作系统：首版候选 Windows 10/11 x64；Mac arm64 最低版本受 Qt / Python / ONNX wheel 共同约束，构建声明为 macOS 14 及以上，低版本未实测。
- 系统提供基本图形桌面、中文系统字体、文件读写权限。应用不包含也不安装显卡驱动。
- Python、Qt、PDFium、ONNX Runtime、OpenCV 由应用目录提供。0.1.6 公开 Windows 包使用系统已安装的 VC++ v14 x64 运行库（14.44.35229 或更新），通过 `Start-LocalPDFtoEPUB.cmd` 启动；可从微软官网下载独立运行库安装器，无需 Visual Studio。此前本地包曾包含这些 DLL，其历史验证不能替代本次公开包验证。
- EPUBCheck 需要本地 Java 运行时；未随主包准备时，应用自己的结构检查与“EPUBCheck 未执行”必须分开报告。
- 当前包未使用 Apple Developer ID / Windows 代码签名证书，未进行 Apple 公证。下载后可能出现系统信任提示；不能通过关闭整个系统安全机制解决。

## Windows 验收边界

初版已在Windows实机完成构建、资源导入、8页样本转换、当时的10项工程测试、EPUBCheck、进程防火墙和原生桌面GUI流程，并记录已加载模块，见 [初版Windows测试记录](WINDOWS_TEST_REPORT.md)。这里的“10项”只对应初版，既不是当前测试总数，也不表示0.1.3已通过。后续 [0.1.1](RELEASE_0.1.1.md)、[0.1.2](RELEASE_0.1.2.md) 与 [0.1.3](RELEASE_0.1.3.md) 分别保存各版测试、GUI、包和部署证据。

该机器已有Anaconda，因此不称为“未安装开发工具的干净系统”。Windows Sandbox未启用，固件虚拟化报告关闭；没有为验收更改固件、系统虚拟化功能或卸载用户软件。仍需在真正无开发工具的Windows 10/11系统复制应用与资源ZIP进行最终清洁环境验收。Windows防火墙不保证阻断回环；本软件没有回环推理服务，证据使用已验证可达的公网TCP进行阻断正反对照。
