# 本地书坊 · PDF 转 EPUB

默认离线的简体中文桌面软件。选择或拖入 PDF，导入本地 OCR 资源，选择输出，转换为可调字号、可重排、带目录与原图的 EPUB 3。

项目主页：[Kyusho9/local-pdf-to-epub](https://github.com/Kyusho9/local-pdf-to-epub)。本轮 GitHub 公开源码，预编译应用 ZIP、离线资源及完整源码 ZIP 在本机交付；尚未上传 GitHub Release 附件。资源准备与构建方法见下文。

**当前版本为 0.1.6，Windows / Mac 独立包均已通过本机实际验收。** 本轮改进扫描封面与正文小图保留、倾斜扫描段落整理和重复网址页脚识别。两平台已完成完整工程测试、冻结 GUI 断网转换、人工修订和版面重整；Windows 使用真实 DirectML，Mac 使用 CPU。具体证据、包摘要和验证边界见 [0.1.6 记录](docs/RELEASE_0.1.6.md)。

Windows 新任务默认尝试 DirectML，NVIDIA 用户可选 CUDA；macOS 默认 CPU。GPU 组件独立准备，缺失或失败时默认明确回退 CPU。需要页眉、页脚和原页码时勾选保留，并用软件内的“兼容阅读器打开”。正文始终可以调整字号和重排。

## 普通用户启动

1. Windows 解压 `LocalPDFtoEPUB-0.1.6-windows-x64.zip` 并运行完整目录中的 `LocalPDFtoEPUB.exe`；Mac 解压 `LocalPDFtoEPUB-0.1.6-macos-arm64.zip` 后打开 `.app`。程序含运行时，不需要 Python，不能只复制 EXE。
2. 在资源设置导入 `LocalPDFtoEPUB-ocr-resources.zip`，或主动下载。Windows 使用 GPU 时另导入对应的 DirectML / CUDA 运行包；默认 DirectML，缺包默认明确回退 CPU。离线目标机复制应用和资源包即可。页边阅读器已包含在应用包内，不会在读书时下载资源。
3. 选 PDF 和输出位置。需要原页边文字时勾选“保留页眉、页脚和原页码（兼容阅读器）”；默认不勾选。不确定是否属于页边的文字仍保留待核对。
4. 校对中可改标题、段落、确认或撤销页码推算、回查原页、重试失败页。保存并导出后，点击“兼容阅读器打开”；保持本地书坊运行，在已验证的 Edge/Chromium 浏览器用 A+ / A− 调字号（其他浏览器待验）。浏览器只连接本机随机端口，不使用云端服务。
5. 输出同时生成质量报告。缺页必须明确允许部分结果；格式通过和内容正确分别报告。

包未经商业签名 / Apple Developer ID 公证，其他系统的正常信任流程仍需处理。平台前提见 [平台说明](docs/PLATFORMS.md)。

## 页眉页脚与重排

勾选后，EPUB 写入页边原文、精确来源切换标记和标准印刷页码导航；这些标记不插入可见正文，也不新增原页信息附录。原版 Vivliostyle Viewer 的本地附加样式把它们显示到真实页边。EPUB 自身不含会被 EPUBCheck 拒绝的页边 CSS，仍是标准可重排 EPUB。

每个阅读页采用其**起始正文来源**的页眉、页脚和原页码。同一个原页因字号变化可能铺成几个阅读页，一个阅读页也可能容纳几个原页；不能同时保证自由重排和 PDF 原分页一一对应。无法确定人工跨页改文的来源边界时清空该块页边并报告，下一可靠来源恢复。

Flow 的固定定位测试实际出现 A/B 正文配 C 页边，翻页后页边文字消失；这不符合正确对应。Apple Books 也未通过此前运行元素探针。不要使用这两个阅读器判断本版页边效果。Calibre 官方支持标准印刷页码页脚，但不据此承诺任意原页眉文字。完整页边请使用应用的兼容阅读器入口。

取消勾选只省略输出中的已识别页边文字和号码，原稿、修订和来源仍在任务里。改开关或样式不重跑 OCR。扫描样本“2”“3”由页边裁图 OCR 实际识别（随本次选择的推理设备执行）；缺口推算只在邻页证据充分时建议，必须用户确认。确认值保留“人工推算、非 OCR”身份，启用页边后进入真正页脚，撤销或来源冲突后不再显示。

## 支持与限制

支持普通横排中文及中英混排、文字/扫描/失效文字层混合、基础双栏、插图、普通表格、书签目录、可靠脚注链接、原页回查、修订和取消恢复。复杂公式和图形可用原区域图片保留，正文仍为文本。竖排、手写、复杂跨页脚注、极低清晰度和复杂表格需要核对。文字封面暂不支持自选图片；没有联网推理、遥测、翻译或生成式正文补写。

页边显示由指定阅读器及其附加样式提供，不能保证所有 EPUB 阅读器支持。Windows 新任务默认选择 DirectML，也可选择 CPU 或 NVIDIA CUDA；macOS 使用 CPU。旧任务未记录设备时按原 CPU 方式恢复。页边显示仍由来源映射和阅读器分页实现，GPU 不改变对应规则。Windows 干净系统和其他 Mac 版本的验证边界见版本记录。

## 开发运行

Python 3.12（本次两平台发布运行时均为3.12.15）；直接及传递依赖固定于 `requirements.lock`。Mac arm64 使用源码包内 `vendor/wheels/` 的最小 OpenCV wheel，禁用不需要的视频组件；必须在项目根目录执行安装并保留该文件。Windows 专有依赖以平台标记固定，已在Windows x64安装和构建通过。

公开 Git 源码树保留已审查的 Mac OpenCV wheel、构建脚本和许可，省略约 95 MB 的上游源码压缩包。正常开发运行不用重编译 OpenCV。需要重建该 wheel 时，从完整离线源码 ZIP 复制 `vendor/sources/opencv-python-4.11.0.86.tar.gz`，或主动从 `packaging/opencv-source.json` 记录的官方 URL 下载到相同位置；`packaging/build_minimal_opencv.py` 会核对固定 SHA-256 后才构建。完整离线源码 ZIP 始终包含该上游源码；Vivliostyle 对应源码两种交付均保留。

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
PYTHONPATH=src .venv/bin/python -m local_epub resources prepare-installed --dir resources
LOCAL_EPUB_RESOURCES="$PWD/resources" PYTHONPATH=src .venv/bin/python -m local_epub
```

Windows PowerShell 对应环境变量：`$env:PYTHONPATH='src'`，`$env:LOCAL_EPUB_RESOURCES=(Join-Path (Get-Location) 'resources')`；Python 路径为 `.venv\Scripts\python.exe`。正常用户不需要这些步骤。

## CLI 与任务恢复

```sh
PYTHONPATH=src .venv/bin/python -m local_epub convert tests/fixtures/02_mixed_bad_layer.pdf --job artifacts/my-task --resources resources --output artifacts/my-book.epub
PYTHONPATH=src .venv/bin/python -m local_epub resume artifacts/my-task --resources resources --retry-pages 2 --output artifacts/my-book.epub
PYTHONPATH=src .venv/bin/python -m local_epub export artifacts/my-task --output artifacts/restyled.epub --font-size 1.2 --line-height 1.9
PYTHONPATH=src .venv/bin/python -m local_epub export artifacts/my-task --output artifacts/body-only.epub --no-retain-page-margins
PYTHONPATH=src .venv/bin/python -m local_epub export artifacts/my-task --output artifacts/with-margins.epub --retain-page-margins
PYTHONPATH=src .venv/bin/python -m local_epub gui --open-reader artifacts/with-margins.epub
PYTHONPATH=src .venv/bin/python -m local_epub status artifacts/my-task
```

0.1.6 增加 `rebuild-layout`，从已有的原始识别结果重新整理段落和图片位置；不重新运行 OCR，也不会把这个操作称为提高了识别准确率。GUI 对应“重新整理版面”。保留完整任务目录；遇到来源变化的人工修订会留作冲突提示，不能按旧块编号直接覆盖。示例：

```sh
PYTHONPATH=src .venv/bin/python -m local_epub rebuild-layout artifacts/my-task --output artifacts/reflowed.epub
```

仅调整页边开关或排版样式仍使用 `export`，无需重新整理或识别。重复推广网址页脚的标记可直接应用于已有页面缓存；内容属于正文的网址不会仅因存在网址便被省略。

`--ocr-device cpu|directml|cuda`、`--gpu-runtime PATH`、`--no-gpu-fallback`、`--mode auto|text|ocr`、`--dpi 180`、`--password-file FILE`、`--cancel-file FILE`、`--allow-partial`、`--epubcheck JAR` 可用。密码不写入任务 JSON。CLI stdout 为 JSONL 进度；完整成功退出 0，不完整退出 2，格式校验失败退出 3，取消退出 130，其他错误退出 1。不要删除任务目录内的 `source.pdf/raw/pages/assets/edits.json`，它们用于回查与恢复。

`convert`、`resume`、`export` 默认 `--no-retain-page-margins`。请求 `--retain-page-margins` 将输出供兼容阅读器使用的页边及页码映射。旧 `--include-page-info` 已拒绝；旧 `--no-include-page-info` 仅作无害兼容，不生成附录。任务输出选项使用 schema 2；历史单块可见性决定保留供迁移和审计，不作为新的页边输出开关。

## 验证与构建

```sh
PYTHONPATH=src .venv/bin/python -m pytest -q
PYTHONPATH=src .venv/bin/python scripts/offline_acceptance.py
.venv/bin/python scripts/build.py
.venv/bin/python scripts/build-smoke.py --resources resources --deny-network
```

上述 `offline_acceptance.py` 和 `build-smoke.py --deny-network` 的禁网验证限 macOS，需要允许系统建立子进程沙箱。构建必须在目标操作系统执行，Mac 不能替代 Windows 实机验证。Windows 构建脚本为 `scripts/build-windows.ps1`，CI 为 `.github/workflows/build-windows.yml`。

Windows GPU 发布版四模式（DirectML / CUDA / 缺包回退 / 严格失败）使用 `scripts/windows-gpu-gui-acceptance.ps1`，同时阻断主 GUI、转换 CLI 和 GPU Python 的外连并审计进程。

Windows 实际防火墙和交互桌面验收分别使用 `scripts/windows-offline-acceptance.ps1` 与 `scripts/windows-gui-acceptance.ps1`，需要管理员权限；只创建临时应用规则并清理，不断开网卡。包内 `gui-test` 是显式开发验收入口，必须提供样本、资源、输出和证据目录，正常启动不会运行它。

EPUBCheck 5.4.0 与 Java 21 已用于项目验收；Java/JAR 尚未捆绑进主应用。应用明确区分内置检查、EPUBCheck 未运行/通过/失败，以及需要人工校对的内容疑点。格式通过从不代表文字准确。

- [0.1.5 统一 ONNX Runtime GPU 执行与实际验收](docs/RELEASE_0.1.5.md)
- [0.1.6 扫描图片、段落与网址页脚改进及实际验收](docs/RELEASE_0.1.6.md)
- [Windows DirectML / CUDA 运行包准备与限制](docs/GPU_RUNTIME.md)
- [0.1.4 真正页边显示与兼容阅读器](docs/RELEASE_0.1.4.md)
- [0.1.3 页边需求纠正、OCR 修复与验收状态](docs/RELEASE_0.1.3.md)
- [0.1.2 原页信息附录（历史记录，已被本版移除）](docs/RELEASE_0.1.2.md)
- [0.1.1 页眉页码与报告改进（历史记录）](docs/RELEASE_0.1.1.md)
- [首版实际测试记录](docs/TEST_REPORT.md)
- [Windows实机验收与目录说明](docs/WINDOWS_TEST_REPORT.md)
- [架构与选型](docs/ARCHITECTURE.md)
- [离线资源准备/导入](docs/OFFLINE_RESOURCES.md)
- [平台和构建](docs/PLATFORMS.md)
- [第三方代码、模型和二进制许可](docs/THIRD_PARTY.md)
- [已知限制与后续任务](docs/ROADMAP.md)

应用代码与独立页边样式 MIT；原版 Vivliostyle Viewer/Core 为 AGPL-3.0，完整对应源码随包；Qt、PDFium、模型等各自许可见第三方清单。自制 PDF 无第三方书籍正文，不包含随应用分发的商业字体文件。

公开源码前运行 `scripts/prepare-public-source.py` 预检，只从明确的源码目录创建独立待审查副本；该命令不创建 Git 仓库、不上传。所有本机 `artifacts`、用户 PDF、扫描图、提取正文及任务缓存均不进入公开源码树。本次公开源码 ZIP 使用 `scripts/package-source.py --no-evidence`，同样不包含任何机器特定验收记录，但保留完整上游 OpenCV 源码、构建依赖和许可。原始验收记录留在本机工作区，公开文档说明范围与结果；私有调查目录即使被显式指定也会被拒绝。不要在开发工作区直接执行 `git add .`。
