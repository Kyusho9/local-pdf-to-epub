# 开发、CLI 与构建

[返回首页](../README.md) · [全部文档](index.md)

普通用户使用 Release 中的应用包即可。以下步骤供开发者调试和构建。

## 开发环境

使用 Python 3.12；直接与传递依赖固定于 [`requirements.lock`](../requirements.lock)。本次两平台发布运行时为 Python 3.12.15。安装命令应在项目根目录执行。

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
PYTHONPATH=src .venv/bin/python -m local_epub resources prepare-installed --dir resources
LOCAL_EPUB_RESOURCES="$PWD/resources" PYTHONPATH=src .venv/bin/python -m local_epub
```

Windows PowerShell 对应环境变量为 `$env:PYTHONPATH='src'` 和 `$env:LOCAL_EPUB_RESOURCES=(Join-Path (Get-Location) 'resources')`；Python 路径为 `.venv\Scripts\python.exe`。Windows 专有依赖通过平台标记固定。

Mac arm64 使用 `vendor/wheels/` 的最小 OpenCV wheel，禁用不需要的视频组件，安装时应保留该文件。公开 Git 树包含已审查的 wheel、构建脚本和许可，省略约 95 MB 的上游源码压缩包。正常开发运行不需要重编译 OpenCV。

需要重建 wheel 时，从完整离线源码 ZIP 复制 `vendor/sources/opencv-python-4.11.0.86.tar.gz`，或从 [`packaging/opencv-source.json`](../packaging/opencv-source.json) 记录的官方 URL 主动下载到相同位置。构建脚本会核对固定 SHA-256；具体步骤见 [OpenCV 构建说明](../packaging/OPENCV_MINIMAL.md)。完整离线源码 ZIP 保留该上游源码，Vivliostyle 对应源码在 Git 树和源码 ZIP 中均提供。

## CLI

```sh
PYTHONPATH=src .venv/bin/python -m local_epub convert tests/fixtures/02_mixed_bad_layer.pdf --job artifacts/my-task --resources resources --output artifacts/my-book.epub
PYTHONPATH=src .venv/bin/python -m local_epub resume artifacts/my-task --resources resources --retry-pages 2 --output artifacts/my-book.epub
PYTHONPATH=src .venv/bin/python -m local_epub export artifacts/my-task --output artifacts/restyled.epub --font-size 1.2 --line-height 1.9
PYTHONPATH=src .venv/bin/python -m local_epub export artifacts/my-task --output artifacts/body-only.epub --no-retain-page-margins
PYTHONPATH=src .venv/bin/python -m local_epub export artifacts/my-task --output artifacts/with-margins.epub --retain-page-margins
PYTHONPATH=src .venv/bin/python -m local_epub gui --open-reader artifacts/with-margins.epub
PYTHONPATH=src .venv/bin/python -m local_epub status artifacts/my-task
```

`rebuild-layout` 复用原始识别结果重新整理段落、图片与页边，不重新运行 OCR。可使用 `--pages 1,3` 限定页面；省略时处理已有识别结果的页面。

```sh
PYTHONPATH=src .venv/bin/python -m local_epub rebuild-layout artifacts/my-task --output artifacts/reflowed.epub
```

仅调整页边开关或排版样式时使用 `export`。重复网址页脚的文档级分类也可直接应用于已有页面缓存；正文网址不会仅因包含 URL 就被省略。

常用参数包括 `--ocr-device cpu|directml|cuda`、`--gpu-runtime PATH`、`--no-gpu-fallback`、`--mode auto|text|ocr`、`--dpi 180`、`--password-file FILE`、`--cancel-file FILE`、`--allow-partial`、`--epubcheck JAR`。各子命令支持的参数以 `--help` 为准。密码不写入任务 JSON。

CLI stdout 为 JSONL 进度。退出码：完整成功 `0`，不完整 `2`，格式校验失败 `3`，取消 `130`，其他错误 `1`。

`convert`、`resume`、`export` 默认 `--no-retain-page-margins`；启用 `--retain-page-margins` 会写入兼容阅读器使用的来源与页边映射。旧 `--include-page-info` 已拒绝，旧 `--no-include-page-info` 仅作无害兼容，不生成原页信息附录。任务输出选项使用 schema 2，历史单块可见性决定保留供迁移和审计。

任务目录中的 `source.pdf/raw/pages/assets/edits.json` 用于原页回查、缓存与修订恢复，不应删除。结构变化后的人工修订按来源核对，无法可靠匹配时保留为冲突，不按旧块编号直接覆盖。

## 测试与构建

```sh
PYTHONPATH=src .venv/bin/python -m pytest -q
PYTHONPATH=src .venv/bin/python scripts/offline_acceptance.py
.venv/bin/python scripts/build.py
.venv/bin/python scripts/build-smoke.py --resources resources --deny-network
```

`offline_acceptance.py` 和 `build-smoke.py --deny-network` 的禁网验证限 macOS，需要允许系统建立子进程沙箱。应用应在目标操作系统构建，Mac 构建不能替代 Windows 实机验证。Windows 构建脚本为 [`scripts/build-windows.ps1`](../scripts/build-windows.ps1)，CI 为 [Windows 构建工作流](../.github/workflows/build-windows.yml)。平台前提、依赖和打包细节见 [平台说明](PLATFORMS.md)。

Windows 实际防火墙与交互桌面验收使用 `scripts/windows-offline-acceptance.ps1`、`scripts/windows-gui-acceptance.ps1`，需要管理员权限；测试只创建临时应用规则并清理，不断开网卡。GPU 四模式（DirectML / CUDA / 缺包回退 / 严格失败）使用 `scripts/windows-gpu-gui-acceptance.ps1`，分别检查主 GUI、转换 CLI 与 GPU 子进程。

包内 `gui-test` 是显式验收入口，需要指定样本、资源、输出和证据目录，正常启动不会运行它。版面重整验收使用 `--verify-rebuild-layout`，检查真实非空缓存快照及人工修订。

本项目使用 Java 21 与 EPUBCheck 5.4.0 验收格式。Java / JAR 未捆绑进主应用；内置检查、EPUBCheck 结果与内容疑点分开报告。各版实测结果见 [版本记录目录](index.md#版本与验证记录)。

## 源码与发布包

公开源码前使用 `scripts/prepare-public-source.py` 检查明确的源码白名单，必要时生成独立待审查目录；该脚本不创建 Git 仓库或上传内容。

```sh
.venv/bin/python scripts/prepare-public-source.py
.venv/bin/python scripts/package-source.py --no-evidence
```

`--no-evidence` 生成可公开共享的完整源码 ZIP，包含上游 OpenCV 源码、构建脚本、锁定依赖清单与许可，排除全部机器特定验收记录。用户 PDF、扫描图、提取正文、任务缓存及本地 `artifacts` 均不进入公开源码。私有调查目录即使显式指定也会被拒绝。

Windows CI 同样只归档剔除微软运行库后的公开候选及模型；原始完整包不上传为 Actions 附件。

Windows 公开附件另用 `packaging/build_windows_system_runtime.py` 从已构建目录包移除微软 VC/UCRT DLL 并添加启动脚本；使用者需已安装官方 x64 运行库。该步骤不改变转换代码，具体参数见脚本 `--help`。第三方完整源码附件由 `packaging/build_third_party_sources.py` 按固定清单制作，替换说明见 [THIRD_PARTY_SOURCES.md](THIRD_PARTY_SOURCES.md)。

原始验收记录留在本机工作区，公开文档说明验证范围与结果。发布前检查文件清单、包摘要、许可证和目标平台记录；不要在包含用户材料的开发工作区直接执行 `git add .`。
