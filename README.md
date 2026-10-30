# 本地书坊 · PDF 转 EPUB

一款离线桌面工具，将中文 PDF 转成可调字号、随屏幕重排的 EPUB。

## 功能

- 支持文字版、扫描版和混合型 PDF，自动选择文字提取或本地 OCR。
- 恢复段落、目录和原图，提供原页回查、校对与重新导出。
- 保存任务进度和人工修订，支持取消、继续和局部重试。
- 资源准备后完全离线使用，无需账号，不上传文档。

## 下载

**[下载最新版本](https://github.com/Kyusho9/local-pdf-to-epub/releases/latest)**

| 平台 | 应用包 |
| --- | --- |
| Windows 64 位 | [Windows x64](https://github.com/Kyusho9/local-pdf-to-epub/releases/download/v0.1.6/LocalPDFtoEPUB-0.1.6-windows-x64.zip) |
| macOS · Apple Silicon | [Mac arm64](https://github.com/Kyusho9/local-pdf-to-epub/releases/download/v0.1.6/LocalPDFtoEPUB-0.1.6-macos-arm64.zip) |

首次使用还需下载 [OCR 资源包](https://github.com/Kyusho9/local-pdf-to-epub/releases/download/v0.1.6/LocalPDFtoEPUB-ocr-resources.zip)。完整项目源码与第三方对应源码也在 Release 页面提供。

## 快速开始

1. 解压应用，Windows 双击 `Start-LocalPDFtoEPUB.cmd`，Mac 打开 `.app`。保留完整应用目录，无需安装 Python。
2. 在「资源与设置」导入 OCR 资源包。应用和资源包可以复制到离线电脑使用。
3. 选择或拖入 PDF，设置输出位置并转换；核对结果后保存 EPUB。

Windows 需已安装 [VC++ x64 运行库](https://learn.microsoft.com/cpp/windows/latest-supported-vc-redist)，不需要 Visual Studio。默认尝试 DirectML，缺少 GPU 运行包时明确回退 CPU；也可选择 NVIDIA CUDA。Mac 使用 CPU。详见 [使用指南](docs/USAGE.md)。

优先支持普通横排中文及中英混排。复杂版式仍需校对；保留原页眉、页脚时，请通过软件中的「兼容阅读器打开」查看。

## 文档

- [使用指南](docs/USAGE.md)
- [离线资源与 GPU 加速](docs/OFFLINE_RESOURCES.md)
- [开发、CLI 与构建](docs/DEVELOPMENT.md)
- [更新记录与测试范围](docs/RELEASE_0.1.6.md)
- [全部文档](docs/index.md)

## 许可

应用代码采用 [MIT](LICENSE) 许可；随附 Vivliostyle 阅读器采用 AGPL-3.0，并提供对应源码。其他组件见 [第三方许可](docs/THIRD_PARTY.md)。
