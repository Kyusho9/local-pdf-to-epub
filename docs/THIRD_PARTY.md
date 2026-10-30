# 第三方代码、模型与二进制清单

核查日期：2026-10-29 至 2026-10-30。发布采用可替换共享库的目录式包，源码随项目交付；模型独立成包。最终构建环境中的精确版本、项目地址、随 wheel 提供的许可文本和文件摘要，由 `packaging/collect_licenses.py` 收集到 `packaging/licenses/inventory.json`。该库存包括开发工具，不能简单等同于最终二进制实际包含的所有文件；PyInstaller 的分析记录保存在 `build/local_epub/`。

两平台实际发布解释器均为 CPython 3.12.15。Windows 构建另用项目目录中的独立 CPython，不再分发宿主的 Anaconda；对应 Python LICENSE 已收入包内。Windows 的40项分发库存另存 `artifacts/windows/licenses-inventory.json`，实际运行库加载证据在 `artifacts/windows/process-modules.json`；Mac 库存保留在 `packaging/licenses/`。uv 仅用于开发时取得运行时，不是桌面软件运行依赖。

| 组件 | 首版用途 | 许可与再分发要点 | 官方依据 |
| --- | --- | --- | --- |
| CPython 3.12 | 随应用打包的解释器 | PSF License，保留其完整许可和版权声明；底层依赖有各自许可 | [Python license](https://docs.python.org/3.12/license.html) |
| PySide6 / Shiboken6 / Qt 6.12.0 | 原生桌面界面，实际包含 Core、Gui、Widgets、Network、Svg；Mac 另带 DBus | 开源版 PySide6 为 LGPLv3 / GPLv3 路线，也有商业许可。所用 Qt 模块采用 LGPLv3；保留通知和许可，允许用户替换兼容 Qt/PySide 库，不禁止为调试库修改而逆向。对应源码随 Release 独立提供，含图像插件和翻译源 | [Qt for Python licenses](https://doc.qt.io/qtforpython-6/licenses.html)，[对应源码与替换](THIRD_PARTY_SOURCES.md) |
| pdfplumber 0.11.6 | 文字位置、表格线、图片区域与版式读取 | MIT，保留版权和许可 | [pdfplumber](https://github.com/jsvine/pdfplumber/blob/v0.11.6/LICENSE.txt) |
| pdfminer.six | PDF 文本解释，pdfplumber 依赖 | MIT，保留版权和许可 | [pdfminer.six](https://github.com/pdfminer/pdfminer.six/blob/master/LICENSE) |
| pypdfium2 5.14.0 | 本机 PDF 渲染和原页回查 | 包装层使用 Apache-2.0 / BSD-3-Clause 组合；PDFium 及 bundled 第三方的许可单独保留。本版本 wheel 的 `.dist-info/licenses/` 下含 18 份许可文件，全部复制入包 | [pypdfium2 licensing](https://github.com/pypdfium2-team/pypdfium2#licensing)，[PDFium license](https://pdfium.googlesource.com/pdfium/+/refs/heads/main/LICENSE) |
| RapidOCR ONNX Runtime 1.4.4 | 中文检测、方向分类、识别调用 | Apache-2.0，保留版权、许可及随包 NOTICE；不修改上游代码 | [固定版本许可](https://github.com/RapidAI/RapidOCR/blob/v1.4.4/LICENSE) |
| PP-OCRv4 det / rec；PP-OCR mobile v2 cls ONNX 权重 | 本地 OCR 资源包（文件在 manifest 内列名和 SHA-256） | 来自 RapidOCR 1.4.4 wheel 的 PaddleOCR 转换模型。上游 PaddleOCR / RapidOCR 项目为 Apache-2.0；必须保留两者来源和许可，不宣称对训练数据拥有权利。新增或替换模型要重新记录其许可 | [固定模型配置](https://github.com/RapidAI/RapidOCR/blob/v1.4.4/python/rapidocr_onnxruntime/config.yaml)，[PaddleOCR license](https://github.com/PaddlePaddle/PaddleOCR/blob/release/2.7/LICENSE) |
| ONNX Runtime 1.20.1 | CPU 推理 | MIT，附带第三方组件说明须保留 | [ONNX Runtime license](https://github.com/microsoft/onnxruntime/blob/v1.20.1/LICENSE)，[ThirdPartyNotices](https://github.com/microsoft/onnxruntime/blob/v1.20.1/ThirdPartyNotices.txt) |
| OpenCV Python、NumPy、Pillow、Shapely、PyClipper、PyYAML | 图像和 OCR 数值运算 | OpenCV Apache-2.0；NumPy BSD-3-Clause；Pillow HPND；Shapely BSD-3-Clause 及 GEOS LGPL-2.1；PyClipper MIT（Clipper BSL-1.0）；PyYAML MIT。以本次 wheel 中附带的完整声明为准，保留 GEOS 可替换共享库 | [OpenCV license](https://opencv.org/license/)，[NumPy](https://numpy.org/doc/stable/license.html)，[Pillow](https://pillow.readthedocs.io/en/stable/about.html#license)，[Shapely](https://github.com/shapely/shapely/blob/main/LICENSE.txt) |
| pypdf 5.4.0 | PDF 书签与加密信息读取 | BSD-3-Clause，保留版权和许可 | [pypdf license](https://github.com/py-pdf/pypdf/blob/5.4.0/LICENSE) |
| PyInstaller 6.12.0 | 构建工具和启动器 | GPL-2.0 加打包例外，部分文件 Apache-2.0；打包例外允许以符合依赖许可的方式发布应用。保留库存便于追踪 | [PyInstaller license](https://pyinstaller.org/en/stable/license.html) |
| ReportLab 4.3.1 / pytest | 仅生成测试样本与开发测试 | BSD 类许可 / MIT；不包含进应用入口依赖 | [ReportLab source](https://github.com/MrBitBucket/reportlab-mirror)，[pytest license](https://github.com/pytest-dev/pytest/blob/main/LICENSE) |
| EPUBCheck | 独立 EPUB 格式验证（如已准备本地 Java 与工具） | BSD-3-Clause；其 Java 依赖另有许可。未准备时报告“未执行”，不能冒称通过 | [EPUBCheck license](https://github.com/w3c/epubcheck/blob/main/LICENSE) |

## 字体与测试样本

应用和导出 EPUB 默认使用系统/阅读器字体，不下载字体，不嵌入商业字体。自制测试 PDF 使用 ReportLab 的 `STSong-Light` 标准 CID 字体引用，没有复制 macOS 系统字体二进制。扫描测试页由本机 PDFium 将此 PDF 渲染成像素后嵌入；文字内容、插图和地名均为本项目自制。测试脚本只为开发者准备，普通用户运行应用不需要 ReportLab。

## 目录包与源码

`scripts/build.py` 在构建前复制 wheel 附带的 LICENSE、COPYING、NOTICE、版权文件，输出机器可读库存；应用包内 `licenses/` 保留这些文件。第三方共享库保持独立，不静态嵌入自有二进制；源程序、固定依赖和构建 spec 一并交付，便于重新链接或替换 LGPL 组件。兼容替换库后可能需重新执行平台签名或重建应用。

实测 PySide6 Essentials 6.12.0 wheel 未带独立许可文本，因此额外从 Qt `v6.12.0` 官方源仓库取得 LGPL-3.0、GPL-3.0 与 Qt GPL exception 文本，保存于 `packaging/licenses/Qt-6.12.0/` 并随应用打包；这部分不伪装为 wheel 原有文件。

0.1.6 的 [Release 对应源码附件](https://github.com/Kyusho9/local-pdf-to-epub/releases/download/v0.1.6/LocalPDFtoEPUB-0.1.6-third-party-sources.zip) 由本项目直接提供：Qt Base / SVG / Image Formats / Translations / Tools 6.12.0、PySide / Shiboken 6.12.0、GEOS 3.14.1、Shapely 2.2.0，以及 Windows 实际 FFmpeg DLL 对应的完整源码和构建配方。上游归档未修改，来源 URL、版本、字节数、SHA-256 和二进制关联在包内记录。详情及替换/重新链接方法见 [THIRD_PARTY_SOURCES.md](THIRD_PARTY_SOURCES.md)。主应用自身源码与 Vivliostyle 对应源码另随应用源码 ZIP 交付；这些附件和通知应随二进制发布保留，不能只留下上游网址。当前包不包含开发者证书、公证或商业 Qt 许可。


## OpenCV 的实物二进制审查

本机最初安装的 `opencv-python==4.11.0.86` macOS arm64 wheel 自带 FFmpeg。直接调用其中 `avutil_license()` 返回 `GPL version 3 or later`，配置含 `--enable-gpl`、x264 和 x265。对官方 4.10.0.84、4.12.0.88 与 4.11.0.86 headless wheel 的远程 ZIP / 原生库只读审查也发现相同情况。该事实不能用 OpenCV README 的笼统 LGPL 描述覆盖；[FFmpeg 官方许可说明](https://ffmpeg.org/doxygen/trunk/md_LICENSE.html)明确区分启用 GPL 组件后的构建。审计脚本和原始结果保存在 `packaging/audit_opencv_wheel.py` 与同目录的审查 JSON 中。

Mac 发布改用本项目从固定官方源码构建的最小图片处理 wheel，保留 `opencv-python` 包名以满足 RapidOCR 的真实依赖；只构建所需图片模块并禁用 FFmpeg、视频和 GUI 组件。源码原包、SHA-256、构建脚本、构建日志和 wheel 放在 `vendor/` / `packaging/` 对应记录中。`scripts/build.py` 会检查实际 `cv2.getBuildInformation()` 与原生库清单；如果 Mac 环境再次装入视频编解码依赖，构建立即失败。Windows wheel 独立审查，不由 Mac 审计结果代替。

FlatBuffers 的 wheel 同样未带完整许可文本，另从对应 `v25.12.19` 官方源码补入 Apache-2.0 文本，并通过 `supplemental_license_files` 与 wheel 自有许可分开记录。

Windows x64 官方 `opencv-python==4.11.0.86` wheel 已做静态原生 DLL 审查：FFmpeg 字符串为 LGPL-2.1-or-later，没有 `--enable-gpl` 或 x264/x265；详细官方 wheel SHA 和审查结果在 `packaging/opencv-windows-4.11-audit.json`。许可实物检查与运行验收分开记录；Windows 原生 GUI、OCR 和系统防火墙验收见 `docs/WINDOWS_TEST_REPORT.md`。

0.1.6 进一步核对该 DLL 为 FFmpeg 4.4.5；实际 DLL 与 OpenCV 官方二进制提交 `d63d7c154c57242bf2283be61166be2bd30ec47e` 的 Git blob 一致。同次官方源码提交 `02a08baa7b8d330364c35477ba50524db8e4d335`、OpenCV wrapper 的完整对应提交 `09892c9d1706f40342bda0bc404580f63492d9f8`、libvpx / AOM / OpenH264 API 头文件及原始构建配方全部收入上述第三方源码附件。不分发 Cisco OpenH264 编解码 DLL。已验证源码关联与展开，未宣称重新编译或二进制逐字节复现。


## 0.1.4 的独立本地阅读器

新增原版 Vivliostyle Viewer/Core 2.45.2（AGPL-3.0），完整保留官方 npm 发布物，未修改其引擎与 UI；独立 `source-margins.css` 为本项目 MIT。精确上游提交 `8f31c04f2dd3a3d992016cec21cf69acf221a821`，运行文件清单和 SHA 在 `vendor/reader/vivliostyle-2.45.2/manifest.json`。字体 Font Awesome 5.15.4 为 OFL-1.1，图标 CC-BY-4.0、代码 MIT；Knockout、i18next-ko、i18next 为 MIT，fast-diff 为 Apache-2.0。完整许可原文随 reader/licenses 保留。

完整对应源码、构建配置/锁文件、原始 npm 包及直接捆绑依赖包位于 `vendor/sources/vivliostyle-2.45.2/`，同时收入应用的 `reader-sources/vivliostyle-2.45.2/` 与源码 ZIP，接收者无需联网取得本次对应源码。源码重建需要 Node >=20 与 Yarn，具体见该目录 README；当前验证官方发布包，未宣称已从源码重建 Viewer。桌面 Python 应用与此独立浏览器程序通过本机静态 HTTP 交互，不把第三方代码标成 MIT。分发后若修改 AGPL 程序，应同时提供修改与对应源码。

Mac 对应目录在 `.app/Contents/Resources/reader-sources/`；Windows 在 `_internal/reader-sources/`。本地页面仅开放所选 EPUB 和固定资源，不开放项目文件夹。内置字体与 CSS 无 CDN，原版可选 MathJax CDN 代码保持注释，普通流程不会启用。官方资料：[Viewer](https://docs.vivliostyle.org/en/viewer/vivliostyle-viewer/)、[支持的 CSS](https://docs.vivliostyle.org/en/reference/supported-css-features/)。

## 0.1.5 的 Windows DirectML / CUDA 附件

主应用的 CPU 路径继续使用既有固定运行库；Windows 新任务默认尝试 DirectML，缺少资源时按设置明确回退 CPU。可选 GPU 附件使用独立 CPython 3.12.10 官方 Windows embeddable distribution，采用 PSF 许可；主应用解释器仍为 3.12.15。GPU 侧 ONNX Runtime 1.22.0 为 MIT，RapidOCR 1.4.4 为 Apache-2.0，模型仍来自原有独立模型包。CPU/GPU 两种 ORT 包不混装，不覆盖主程序共享库。

NVIDIA CUDA 12.8（runtime 12.8.90、NVRTC/NVJitLink 12.8.93、cuBLAS 12.8.4.1、cuFFT 11.3.3.83、cuRAND 10.3.9.90）和 cuDNN 9.8.0.87 采用 NVIDIA 对应专有许可。只提取允许随应用分发且运行需要的 DLL 与许可；不提取 SDK 开发头文件、静态库或系统显卡驱动。附件仅供本地书坊使用，不作为通用 SDK 分发，不宣称 NVIDIA 背书。保留各 wheel 中的原许可、cuDNN 第三方 acknowledgements，以及 RapidOCR/FlatBuffers 缺失的完整补充许可。依据：[CUDA 12.8.1 EULA](https://docs.nvidia.com/cuda/archive/12.8.1/eula/index.html)、[cuDNN 9.8 EULA](https://docs.nvidia.com/deeplearning/cudnn/backend/v9.8.0/reference/eula.html)、[cuDNN acknowledgements](https://docs.nvidia.com/deeplearning/cudnn/backend/v9.8.0/reference/acknowledgements.html)。

DirectML 包固定 `onnxruntime-directml==1.22.0`，其中原始 `DirectML.dll` 版本为 1.15.4，采用 Microsoft 专有许可，不能套用 ORT 的 MIT。另从官方 Microsoft.AI.DirectML 1.15.4 NuGet 包保留完整 LICENSE、LICENSE-CODE 与 ThirdPartyNotices：NuGet 与 wheel DLL 的证书区字节不同，六个 PE 节逐字节相同；不替换或修改 wheel DLL。来源和实物比对见 `artifacts/gpu-0.1.5/dml/{nuget-license-provenance,directml-binary-provenance}.json`。DirectML 不包含 NVIDIA 库，文件清单在 `directml_runtime_catalog.json` / `directml_runtime_manifest.json`。官方依据：[DirectML](https://github.com/microsoft/DirectML)、[ONNX Runtime DirectML](https://onnxruntime.ai/docs/execution-providers/DirectML-ExecutionProvider.html)。

历史本地 GPU 运行包中的 Microsoft VC Runtime 14.44.35112 来自固定官方 VSIX 的 x64 release CRT 目录，不包含 debug_nonredist。VC Runtime 不是 MIT；[微软说明](https://learn.microsoft.com/cpp/windows/redistributing-visual-cpp-files)要求公开再分发者满足适用 Visual Studio 许可和 REDIST 条件。

**0.1.6 公开附件不捆绑 Microsoft VC/UCRT DLL，也不上传历史 DirectML / CUDA 整合 ZIP。** Windows 应用使用目标系统已安装的 Visual C++ v14 x64 运行库（14.44.35229 或更新）。用户从[微软官方下载页](https://learn.microsoft.com/cpp/windows/latest-supported-vc-redist)取得运行库安装器，可复制到离线电脑自行安装；本项目不镜像该安装器。`Start-LocalPDFtoEPUB.cmd` 仅检查系统组件，并将用户本机的 `MSVCP140.dll` 复制到应用私有的 Shapely 依赖位置；该生成副本不属于公开 ZIP，使用者不应把它误当作本项目授权再分发的文件。应用和使用者均不需要安装 Visual Studio。没有为本次发布安装 Visual Studio、修改系统 DLL 或调整全局 CUDA 环境。

其余 GPU 数值/图像依赖与主应用采用相同固定版本；OpenCV Windows 的未使用 FFmpeg DLL 不收入该 GPU 附件。Shapely/GEOS 等共享库和完整许可仍单独保留。GPU 资源里的可执行组件不因本应用 MIT 而改变原许可。运行包所有来源 URL、原包摘要、精确提取成员和最终文件摘要分别在 `gpu_runtime_catalog.json` 与 `gpu_runtime_manifest.json`，GPU 附件内独立保留许可；主应用既有开发环境库存不冒充 GPU 库存。

配置、构建和实测边界见 [GPU 运行包](GPU_RUNTIME.md) 与 [0.1.5 发布记录](RELEASE_0.1.5.md)。
