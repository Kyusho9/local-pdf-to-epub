# 离线资源与准备

正常转换不下载任何文件。应用包带 Python、Qt、PDFium、ONNX Runtime 等运行库；中文 OCR 模型为单独约 14 MB 的跨平台 ZIP。应用和模型准备好后可断网使用。

## 推荐：复制应用和离线资源 ZIP

1. 在另一台可联网设备取得本版本应用包与 `LocalPDFtoEPUB-ocr-resources.zip`。
2. 将两个文件复制到目标电脑，解压应用并启动。Windows 使用 `Start-LocalPDFtoEPUB.cmd`，并需已安装 VC++ v14 x64 运行库；可从[微软官网](https://learn.microsoft.com/cpp/windows/latest-supported-vc-redist)单独取得安装器后复制到离线电脑安装，无需 Visual Studio。
3. 打开「资源与设置」，选择资源存储目录，点击导入资源包；软件检查版本、文件路径和 SHA-256。
4. 资源检查显示可用后导入 PDF、选输出并转换。目标电脑无需联网或安装 Python。

模型包目录：`manifest.json`、`det.onnx`、`rec.onnx`、`cls.onnx`、`licenses/`。manifest 包含格式/模型版本、用途、逐文件字节数与 SHA-256、模型来源及许可。它检测损坏与版本混用，不构成发行者数字签名；请从可信渠道取得应用和资源包。

## 主动联网准备

GUI 中点击「联网下载资源」后，程序只请求固定版本的 PyPI wheel。URL 和官方 SHA-256 固定于 `src/local_epub/resource_catalog.json`，下载后只提取模型和许可。网络失败会报错，可继续改用本地 ZIP；绝不上传文档内容。

开发 CLI（普通用户无需使用）：

```sh
PYTHONPATH=src .venv/bin/python -m local_epub resources download --dir resources
PYTHONPATH=src .venv/bin/python -m local_epub resources import --dir resources --pack dist/LocalPDFtoEPUB-ocr-resources.zip
PYTHONPATH=src .venv/bin/python -m local_epub resources check --dir resources
```

开发环境已安装固定 OCR wheel 时可使用 `resources prepare-installed --dir resources`，再使用 `resources export --dir resources --pack dist/LocalPDFtoEPUB-ocr-resources.zip` 导出。模型读取只发生在需要 OCR 的页；没有模型的纯文字 PDF 仍可用 CLI 处理，需 OCR 的页面标失败并列出缺项，不会冒充成功。

## 运行库与平台

Mac 与 Windows 应用包分别构建，不能互换。资源包模型可共用。首版 CPU 路径不要求 GPU 驱动或 NVIDIA 显卡。Windows 目标为 x64，Mac 目标为 Apple Silicon；实际验证状态见 PLATFORMS.md 与 TEST_REPORT.md。

EPUBCheck 是可选的额外格式校验组件。完整校验需要本地 Java 17+ 和 EPUBCheck；本项目验收使用 Java 21 与 EPUBCheck 5.4.0。未配置时不会自动联网下载，也不会把内置 ZIP/XML 检查标成 EPUBCheck 通过。

导入目标应为新建空目录或经完整校验的本应用专用资源目录；若目录含无关文件，软件拒绝覆盖并保留原文件。已有损坏资源建议导入到新的空目录。


## 0.1.4 页边兼容阅读器

完整应用包已含固定 Vivliostyle 2.45.2 运行文件、独立页边样式、许可和对应源码。普通用户无需另装 Node 或运行服务器；点击“兼容阅读器打开”即可使用本机浏览器。阅读时保持本地书坊运行，应用关闭后当前本机服务退出。

资源读取前逐文件核对字节数和 SHA-256。缺失或损坏时明确报错，应重新解压完整应用包，不会自动下载、转云端或改用其他阅读器。系统浏览器由操作系统提供，实际验证的桌面版本见0.1.4发布记录。

本机服务只监听127.0.0.1随机端口，不是互联网推理服务；随机路径隔离所选书与静态资源。转换流程仍可全部禁网，阅读器只需允许本机回环，外网可以继续阻断。CSS、字体、图像和脚本均来自本地，无账号或CDN。


## DirectML 与 NVIDIA CUDA 运行包

0.1.6 公开 Release 不上传历史 GPU 整合 ZIP（其中包含微软 VC DLL）。下文记录现有 GPU 功能及本地包的组成；已准备的匹配版本仍可导入，或由用户主动从官方来源下载准备。应用不会在转换时补下载。

CPU 方式无需 GPU 包。Windows 新任务默认尝试 DirectML，也可选择 CUDA 或 CPU；缺少 GPU 包时默认明确回退 CPU。模型仍使用同一个 OCR 资源包。CUDA 运行包约下载 2.16 GB、落地 2.95 GB，带私有 Python、GPU 推理库、NVIDIA runtime 和 VC Runtime，不要求系统安装 Python 或 CUDA Toolkit。驱动必须由系统提供。可以在联网电脑准备后导出 ZIP，离线电脑只需复制并导入。DirectML 独立包官方下载约 121 MB、落地约 262 MB，离线 ZIP 约 88 MB（`LocalPDFtoEPUB-directml-runtime-win-x64-1.zip`）；不需要 NVIDIA CUDA 库；GPU 选择和资源选择必须匹配。详见 [GPU 准备、许可与实测范围](GPU_RUNTIME.md)。

应用自身携带固定官方组件下载清单及逐文件预期摘要。离线包和主动下载的结果通过相同校验；转换不自动联网。GPU 子进程只通过本机标准输入输出交换结果，不使用回环或互联网推理服务。资源缺失、校验不符或驱动不可用时明确说明，按用户选择回退 CPU 或报告失败。
