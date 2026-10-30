# macOS 图片处理专用 OpenCV 构建

本项目的 macOS arm64 发布使用自制 `opencv-python==4.11.0.86` wheel。它只包含 OCR 需要的 `core`、`imgproc`、`imgcodecs` 与 Python 绑定，不是完整的通用 OpenCV 分发。Windows 使用单独审查的官方 wheel；本配方未在 Windows 执行。

原因有实物证据：已读取的官方 macOS 4.10.0.84、4.11.0.86、4.12.0.88 及 headless 4.11.0.86 wheel 自带 FFmpeg 含 `--enable-gpl`，二进制许可字符串为 GPL version 3 or later。图片 OCR 不需要这些视频组件。审查数据位于同目录的 `opencv-*-audit.json`；本地构建关闭 FFmpeg、videoio、video、highgui、Qt、GStreamer、OpenCL、IPP、LAPACK，以及不需要的图像格式库。

## 已固定的输入

- 官方源码：`opencv-python-4.11.0.86.tar.gz`，95,171,956 bytes。
- SHA-256：`03d60ccae62304860d232272e4a4fda93c39d595780cb40b161b310244b736a4`。
- 原始 URL 记录于 `opencv-source.json`；`vendor/sources/` 保留同哈希源归档，可断网重建。
- Python 3.12；CMake 3.31.6；Ninja 1.11.1.4；NumPy 1.26.4；其余构建依赖在 `opencv-build-requirements.txt`。
- 目标为 `arm64`、macOS 14.0，CPU 硬基线仅 NEON。NEON FP16/DOTPROD 使用运行时分派，不把开发机 M6 的扩展能力作为硬前提。

## 构建

仅开发者需要 Python 和 Apple Command Line Tools；最终用户使用打包应用，不运行这些命令。先准备独立构建环境，不能让构建工具改变应用 `.venv`：

```sh
uv venv --python python3.12 .build-opencv-venv
uv pip install --python .build-opencv-venv/bin/python -r packaging/opencv-build-requirements.txt
.build-opencv-venv/bin/python packaging/build_minimal_opencv.py --deny-network
```

资源准备阶段允许主动下载；本次真正的 CMake/Ninja 编译和 wheel 组装均在 macOS 内核 `deny network*` 下执行。重做干净构建时使用新的工作副本或先移走本配方的 `build/opencv-source/` 缓存。脚本每次先校验源归档摘要，并自动应用可审查的 `opencv-local.patch`。

包装层只有两类修改：包含 `LOCAL_BUILD.json` / `LOCAL_LICENSES.txt`；容许缺失依赖未启用 Feature2D 模块的可选 Python 类型提示。没有修改 OpenCV 算法源代码。`LOCAL_LICENSES.txt` 包含本次静态组件的原始许可，特别补齐 Carotene 的 NVIDIA BSD 版权声明；上游宽泛的第三方声明仍另行保留。

## 最终产物与验证

`vendor/wheels/opencv_python-4.11.0.86-cp312-cp312-macosx_14_0_arm64.whl`（5,150,874 bytes）

SHA-256：`31dc606faece9c9fc22e47f7eefcdda98c6d21f6d7f39cd20b77d92efdd937a1`。

`opencv-minimal-build.json` 保存实际构建参数、源码和 wheel 摘要。应用的 Mac 依赖必须直接引用本地 wheel 并核对该摘要，不能仅依靠相同版本号从 PyPI 回退。内部 `LOCAL_BUILD.json` 用于识别这个本地变体，并参与 OCR 缓存指纹。

已在独立构建环境强制重装最终 wheel，验证 PNG 编解码、resize、轮廓提取。`VideoCapture` 与 `imshow` 不存在。`otool -L` 仅显示系统 AppKit、libc++、libSystem；没有 FFmpeg、外部编解码库、Homebrew 或外部 Python 链接。`LC_BUILD_VERSION` 确认最低 macOS 14.0。OpenCV 的全局配置仍报告平台 Cocoa 可用，但未构建 highgui 模块，不提供 GUI API。

证据位于 `artifacts/opencv-minimal-validation.json`、`opencv-minimal-buildinfo.txt`、`opencv-minimal-linkage.txt` 和 `opencv-minimal-build.log`。完整 PDF/OCR、GUI 与最终发布包验收由主项目的测试记录另行记录；基本库操作通过不能代替整条产品链路验收。
