# 架构和阶段 A 选型

阶段A选型日期：2026-10-29。最初开发实机为 macOS 27.2、arm64、Apple M6（12 核 CPU/12 核 GPU）、32 GB 统一内存；没有独立显存数值，也没有 NVIDIA/CUDA。2026-10-30已补上用户提供的Windows x64实机，独立构建和验收记录见 `WINDOWS_TEST_REPORT.md`；未按Mac硬件推定Windows配置。

## 选型依据

- Python 3.12 + PySide6 Essentials 6.12.0：只带 QtCore/Gui/Widgets，减少分发体积。GUI 通过 QProcess 调用独立 worker，OCR 不占用界面线程。正常用户使用打包的应用，不需要 Python。
- pdfplumber 0.11.6 / PDFium 5.14.0：可靠文字层与基础表格直接提取，PDFium 负责原页与区域渲染；EPUB 写出完全独立。
- RapidOCR ONNX Runtime 1.4.4 + ORT 1.20.1：采用该固定版本的 PP-OCRv4 中英模型，显式传入三个本地模型路径；初始化会显式关闭 ONNX Runtime 的平台遥测，运行时检查实际 provider 为 CPU。首版不启用 GPU，界面不会声称正在使用 Apple GPU。
- EPUB 3 使用标准库 ZIP/XML 生成，避免另带 Pandoc 或 EPUB 作者库。EPUBCheck 独立校验，可使用本地 Java/JAR；未配置时如实显示未运行。

这不是声称轻量后端在复杂版面上优于 Docling。此次首先要解决跨平台、离线准备、数据可追溯和可打包的真实闭环；样本结果是支持范围的依据。复杂表格/公式/扫描图形检测只达到保守基线。

## 已核对的官方入口

- [Docling 离线配置](https://docling-project.github.io/docling/usage/advanced_options/) 支持预下载模型并配置 artifacts_path，远程服务须显式启用。引入它还需布局/表格模型与较大的推理依赖，首版保留 backend 接口后续实测替换。
- [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) 为中文 OCR 模型来源；首版通过 RapidOCR 的 ONNX 部署路径使用，不同时集成完整 Paddle 运行时。
- [pdf-craft](https://github.com/oomol-lab/pdf-craft) 面向扫描书籍，当前项目的模型/推理依赖需要另行验证；此次没有安装或做性能比较，不把它作为唯一跨平台基础。
- [RapidOCR v1.4.4 实际源码](https://github.com/RapidAI/RapidOCR/tree/v1.4.4/python/rapidocr_onnxruntime)：本地 det/rec/cls 路径，CUDA/DML 均关闭，rec 模型带字符表，不依赖下载词典。
- [Qt for Python](https://doc.qt.io/qtforpython-6/)；[EPUBCheck](https://www.w3.org/publishing/epubcheck/)；[PDFium Python](https://pypdfium2.readthedocs.io/)；[pdfplumber](https://github.com/jsvine/pdfplumber)。

## 数据边界

`source.pdf` 是输入副本，输入原件不改。`job.json` 保存摘要、参数、每页状态与实际设备。`raw/` 是原始提取；`pages/` 是自动整理稿；`edits.json` 保存人工改文与合并操作及历史；`document.json` 是当前合成结果；`history/` 保留重新识别前的稿件。

块保存来源页码、点单位左上原点 bbox（不可获得时 null）、OCR 引擎提供的真实识别分数，文本层没有编造置信度。合并段落保留全部来源；无法可靠重匹配修订时不自动套用旧文字，修订保留在报告与 EPUB 附录。

成功/失败/主动排除/未处理均单独记录；取消后完整页缓存保留，processing 页重启变为 pending。任务使用 OS 文件锁，防止并发覆写。缓存标识包含输入 SHA-256、固定依赖、后端/阶段版本、完整模型清单和解析参数。导出样式不参与 OCR 缓存。

0.1.1将可恢复的页眉页码识别放在文档组装层，并独立记录`document_stage_version`，不改变逐页OCR缓存版本。`blocks`保持完整，`decoration`记录证据，`exclude_from_export`控制重排预览与导出。人工保留/排除保存在`edits.visibility`，以原文和来源签名匹配；重新识别不匹配时安全保留当前内容，旧决定仍保存并报告冲突。`.report.json`保留原始结构化疑点，界面和EPUB用统一的`reporting.py`合并页码、区分待核对项和已执行的处理记录。

## 离线边界

转换代码无网络下载入口；worker 设置离线环境并拒绝 Python socket 连接。此防线不是网络隔离实测的替代。验收另用 macOS 内核 `sandbox-exec` 拒绝整个 GUI/worker 子进程树的网络操作，Windows则用程序路径对应的真实出站防火墙规则；两平台均用原生网络探针核实阻断，并记录应用/后台进程。资源下载只在用户点击/CLI resources download 时运行，下载固定 wheel 并校验 SHA-256，仅提取模型/许可证，不执行 wheel 代码、不发送文档内容。


## 0.1.5 统一 ONNX Runtime 执行层

主解析后端及模型保持不变，把 OCR 张量推理放入独立 Windows ONNX Runtime 子进程，统一选择 DirectML 或 CUDA。Windows 新任务默认 DirectML，CUDA 可选，Mac 默认 CPU；旧任务保留 CPU 行为。CPU ONNX Runtime 1.20.1 与 GPU ONNX Runtime 1.22.0 位于不同解释器/目录，避免互相覆盖。`gpu_resources.py` 只负责显式资源准备，`gpu_execution.py` 负责本地协议、生命周期及回退，独立 `gpu_worker.py` 负责模型执行与真实节点审计。GUI 与 EPUB 导出不导入 GPU 库。

任务记录请求设备和每页实际设备，区分文字层 CPU、CPU OCR、DirectML/CUDA OCR 与 CPU 辅助算子。GPU运行包内容、worker、引擎及实际设备身份进入GPU缓存指纹；旧CPU指纹保留兼容。任何GPU失败只在整页CPU重新识别后提交结果；取消不当作GPU故障回退。确定的人工修订保留，不匹配的继续进入冲突报告。
