# Windows DirectML / NVIDIA CUDA 加速

0.1.5 使用统一 ONNX Runtime 执行接口。Windows 新任务默认尝试 DirectML，CUDA 为 NVIDIA 用户可选后端，也可直接选 CPU；Mac 默认 CPU。旧任务没有记录设备时仍按原 CPU 方式恢复。GPU 不改变 PDF 文字层提取、书籍结构整理、页眉页脚映射或 EPUB 排版规则，也不会替换现有中文模型。

## 使用与离线准备

在「资源与设置」选择 DirectML 或 NVIDIA CUDA，并导入对应的 Windows x64 GPU 离线运行包。也可主动联网从固定官方组件准备，再导出运行包复制到另一台电脑。目标电脑无需安装 Python、pip 或 CUDA Toolkit；系统仍须提供兼容的图形设备和驱动：DirectML 要求支持 DirectX 12 的设备，CUDA 要求 NVIDIA 显卡。普通 OCR 模型包保持独立、两种设备共用。

默认允许 GPU 不可用时回退 CPU，界面和任务记录会写明原因。关闭回退后，GPU 失败会明确报错，不冒充 GPU 成功。转换过程中不会自动下载驱动、模型、DLL 或调用在线推理。仅重新导出 EPUB 不会启动 GPU。

GPU 运行目录与默认 CPU 应用目录分开。不要将 GPU 的 `onnxruntime` 文件覆盖进主应用，也不要将主应用指向已安装的任意 Python 环境。运行包包含可执行组件，因此只接受与当前应用内置清单完全匹配的文件；仅修改 ZIP 自带的校验清单不能绕过检查。

## DirectML 与 CUDA 的选择

两种后端使用相同 PP-OCR 模型、检测与排版流程，不引入 MAA 的游戏文本专用模型。DirectML 可覆盖受支持的 NVIDIA、AMD、Intel 等 DirectX 12 设备，运行资源通常更小；CUDA 使用 NVIDIA 专用库。是否更快、文本是否一致由本应用样本实测决定，不能只按后端名称判断。

DirectML 官方当前仍提供支持，但新功能开发已转向 WinML。本版固定离线可运行的 ONNX Runtime DirectML 发行，不引入运行时自动下载执行组件。

## 固定组成与构建方式

两种运行包均使用独立 CPython 3.12.10 Windows embeddable distribution、RapidOCR 1.4.4 和 ONNX Runtime 1.22.0。DirectML 包固定 `onnxruntime-directml==1.22.0`，实际 DLL 为 DirectML 1.15.4；CUDA 包固定 `onnxruntime-gpu==1.22.0`、CUDA 12.8 和 cuDNN 9.8。官方 Python 3.12.10 是 3.12 系列最后提供此类官方二进制的版本；主应用的 CPython 3.12.15 与其隔离，不降级主程序。GPU worker 不接受网络请求，不解包任意文档归档，仅处理父转换进程提供的本地栅格图和固定模型。

CUDA 的下载地址、组件版本、官方压缩包摘要和选取规则见 `src/local_epub/gpu_runtime_catalog.json`；最终允许的文件、字节数和摘要见 `gpu_runtime_manifest.json`。DirectML 对应 `directml_runtime_catalog.json` 和 `directml_runtime_manifest.json`，两种运行目录和文件校验完全分开，不混装 ORT。仅提取运行所需内容及许可，不分发 CUDA SDK 开发头文件、静态库或系统显卡驱动。清单是固定版本校验机制，不是发行者数字签名。

`python312._pth` 限定解释器搜索目录；运行时关闭字节码写入，不加载用户 site-packages。主进程与 GPU worker 通过本地标准输入输出通信，不开推理服务端口。取消与强制终止还需清理 GPU 子进程，具体实现与验收记录见版本记录。

## 实测与边界

2026-10-30 在用户提供的 Windows 11、i5-13600KF、RTX 4070 Ti（12282 MiB 显存，驱动 617.42）上实测。采用同一组模型、同一批 180 DPI 图像，DirectML、CUDA、CPU 对照均固定 ORT 1.22.0；每种后端对三个样本分别预热一次、重复四次，共 45 次识别。每张图的文字在所有次数和后端间逐字一致，关键原文和阅读顺序检查通过。这是小样本一致性，不能推算全文准确率。生产 CPU 保留 ORT 1.20.1，不能把此公平对照写成生产 CPU 的精确用时。

| 后端 | 暖机后 OCR 中位用时 | 官方组件下载 | 运行目录大小 |
| --- | ---: | ---: | ---: |
| DirectML | 0.223 秒 | 121 MB | 262 MB |
| CUDA | 0.153 秒 | 2.16 GB | 2.95 GB |
| CPU 对照 | 1.559 秒 | 使用已准备的运行环境 | — |

正式持久会话执行层另用同一份 12 页、其中 9 页 OCR 的 PDF，从 CLI 启动到 EPUB 导出完整计时：CPU 34.584 秒、DirectML 12.360 秒、CUDA 14.315 秒（各一次）。包含运行包全文件校验、模型启动、首次执行记录、页面渲染和导出，GPU 会话在页面间复用。DirectML 完整任务更快，与 CUDA 单次暖推理更快并不矛盾；不能将这次样本的 2.80 / 2.42 倍当作所有书籍的承诺。记录：`artifacts/gpu-0.1.5/formal-directml-v2-result.json`。

此计时不包含完整 PDF 处理、资源校验、冷启动或 EPUB 导出。已记录 det/cls/rec 三个模型的实际 GPU 计算节点，rec 另有 CPU 辅助算子；不能仅凭 provider 名称判断是否用了 GPU。证据：`artifacts/gpu-0.1.5/dml/comparison.json`。文字版 PDF 主要直接提取，GPU 收益可能很小。

DirectML 采用 DXGI 的默认适配器 index 0，记录实际名称、LUID、内存及是否软件适配器；首版不提供多显卡选择器。AMD、Intel 属于框架可覆盖范围，但本次未在这两类硬件上验证。显卡型号、驱动、显存、分辨率与文字密度都会影响表现。其他 NVIDIA 型号、低显存和旧驱动同样待实机验证；Mac 保持 CPU。

资源探针已分别在 Windows 原生出站防火墙阻断下完成三页实际 OCR，且有阻断前后正控与进程连接采样。正式转换、GUI、取消恢复、缓存和发布包状态统一见 [0.1.5 发布记录](RELEASE_0.1.5.md)，不以独立引擎探针代替完整验收。

## 许可

应用自己的适配代码为 MIT。CPython、ORT、RapidOCR 和数值图像依赖分别保留各自许可；NVIDIA CUDA/cuDNN、Microsoft DirectML 与 VC Runtime 不因应用开源而变为 MIT。此 CUDA 附件仅供本地书坊使用，不作为通用 CUDA SDK 分发。包内保留每个组件的原始许可、第三方声明和来源。

公开再分发 NVIDIA 或 Microsoft 组件应满足各自条款；VC REDIST 再分发条件与适用 Visual Studio 许可有关，不宣称无条件可再分发。官方下载与私有运行目录不会安装或修改全局 CUDA 环境。

官方依据：[ONNX Runtime CUDA 配置](https://onnxruntime.ai/docs/execution-providers/CUDA-ExecutionProvider.html)、[cuDNN 9.8 支持矩阵](https://docs.nvidia.com/deeplearning/cudnn/backend/v9.8.0/reference/support-matrix.html)、[CUDA 12.8.1 EULA](https://docs.nvidia.com/cuda/archive/12.8.1/eula/index.html)、[cuDNN 9.8 许可](https://docs.nvidia.com/deeplearning/cudnn/backend/v9.8.0/reference/eula.html)。

DirectML 官方依据：[ONNX Runtime DirectML](https://onnxruntime.ai/docs/execution-providers/DirectML-ExecutionProvider.html)、[MAA 的 DirectML 分发说明](https://github.com/MaaAssistantArknights/MaaAssistantArknights#directml-支持说明)。

## CLI 准备与设备切换

普通用户在 GUI 操作即可；开发者可执行以下命令（Windows 将解释器路径替换为项目环境或发布版 CLI）：

```sh
PYTHONPATH=src .venv/bin/python -m local_epub gpu-resources download --backend directml --dir directml-runtime
PYTHONPATH=src .venv/bin/python -m local_epub gpu-resources export --backend directml --dir directml-runtime --pack directml-runtime.zip
PYTHONPATH=src .venv/bin/python -m local_epub gpu-resources import --backend directml --dir directml-runtime --pack directml-runtime.zip
PYTHONPATH=src .venv/bin/python -m local_epub gpu-resources check --backend directml --dir directml-runtime
```

CUDA 对应参数为 `--backend cuda`，建议目录为 `gpu-runtime`。转换命令使用 `--ocr-device directml|cuda|cpu`、`--gpu-runtime PATH` 和 `--gpu-fallback` / `--no-gpu-fallback`。继续旧任务时未指定设备则沿用原选择；纯导出只使用已保存正文，不启动模型。缺失运行包可直接选择 CPU，不必为试用 GPU 重新导入原 PDF。

## 开发者复现实机验收

在 Windows 的锁定开发环境、已准备模型与运行包的前提下，可运行 `scripts/windows-onnx-acceptance.py --help`。用 `--resources`、`--directml-runtime`、`--cuda-runtime` 指定本地目录，用 `--output` 指定新的空输出位置；`--backend directml` 选择恢复/故障矩阵，`--benchmark-devices cpu directml cuda` 执行同样本三后端完整计时。脚本会故意终止自己创建并核实身份的进程，不按名称结束用户其他进程；不修改驱动或下载资源。

`python scripts/review-onnx-acceptance.py --run <验收目录>` 可只读复核正文、顺序、实际计算节点及会话复用。整理后的复核脚本已用本次实际结果验证通过。实机脚本由本次通过的验收脚本参数化整理，新的通用参数入口仅做了帮助/编译检查，不伪称又重跑一轮完整实机矩阵。两者均不代替 `scripts/windows-gpu-gui-acceptance.ps1` 的发布版 GUI 与系统防火墙验收，也不代替 EPUBCheck。
