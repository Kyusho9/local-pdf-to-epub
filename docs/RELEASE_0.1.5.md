# 0.1.5 — Windows DirectML / CUDA 统一 OCR 执行层

应用上层通过 ONNX Runtime 统一执行 OCR。Windows 新任务默认 DirectML，CUDA 为 NVIDIA 用户可选后端，CPU 仍可直接选择。两种 GPU 后端复用现有中文模型；默认允许 GPU 不可用时整页回退 CPU，也可关闭回退。Mac 默认 CPU。旧任务没有设备字段时继续使用 CPU，不会自动迁移。

## 已实现

- 「资源与设置」提供 CPU / DirectML / NVIDIA CUDA 三种设备选择；DirectML 与 CUDA 分别记住运行包目录。资源准备在后台线程完成，不阻塞主界面。
- GPU 运行库与主应用 CPU 库隔离。离线 ZIP 导入和主动官方下载均核对应用内固定的完整文件清单，目标电脑不用安装 Python、pip 或 CUDA Toolkit。转换过程不下载、不上传。
- 检测、方向分类、识别三模型采用相同的本地权重。记录首次实际 GPU 计算节点及后续同会话调用，CPU 辅助算子另列；不是只读取 provider 列表便声称加速。
- GPU 中途出错时，丢弃本页不完整 GPU 结果并整页重做 CPU；后续页沿用此次回退。关闭回退则明确记录失败页。取消不触发回退。
- 运行包、worker、模型、实际设备和参数进入缓存身份；旧 CPU 缓存保留兼容。重新识别不覆盖人工修订，无法匹配则报告冲突。只改排版重新导出不启动 OCR。
- Windows Job Object 约束 GPU 子进程生命周期；正常退出、取消和后台主进程强制终止均有清理路径。

## 已实际验证

- 旧 Mac 0.1.0 测试程序因精确路径对应的进程仍运行而占用。已退出该进程，通过系统废纸篓 API 将 `/Applications/LocalPDFtoEPUB.app` 移入废纸篓，保留项目、新版程序和任务。
- 用户提供的 Windows 11 Pro / i5-13600KF / RTX 4070 Ti 12 GB / 驱动 617.42 实机；未安装或修改全局 CUDA Toolkit、驱动或 Anaconda。
- 同 ORT 1.22.0、相同模型及三张 180 DPI 图像，共 45 次 CPU / DirectML / CUDA 调用，文字逐字一致。暖机 OCR 中位时间分别为 1.559 / 0.223 / 0.153 秒。这是小样本 OCR 阶段，不代表整书速度或准确率；生产 CPU 仍为 ORT 1.20.1。
- DirectML 与 CUDA 独立资源探针均在 Windows 真正的单程序出站防火墙阻断下完成 OCR。原生网络探针返回 10013，规则建立前后正控可连接；结束后规则清理，记录的 worker TCP 采样未见连接。
- 正式应用资源下载入口已在 Mac 实际下载两种完整运行包并通过所有文件校验。Windows 正式离线导入也通过，DirectML 2168 文件、CUDA 2169 文件。Mac 只准备 Windows 包，不冒充在 Mac 执行了 Windows GPU 推理。
- 资源完整性工程测试覆盖篡改、自改清单、后端混用、路径逃逸、符号链接、额外代码、保留无关目录以及原有资源的事务性保护。GUI 12 项设备与资源测试通过。

## 发布包最终验收

正式 DirectML 持久会话转换的 13 项功能阶段及 12 页三后端基准均通过：缓存复用、人工改文、只导出、CPU/GPU 切换、缺包回退/严格失败、页内第二次图像识别时取消续跑、强制结束 GPU 子进程后的整页 CPU 重做、强制结束父进程后的子进程退出及恢复。四页正文与 CPU 逐块一致。完整基准 CPU / DirectML / CUDA 为 34.584 / 12.360 / 14.315 秒，各测一次，包含全文件校验和导出。证据 `artifacts/gpu-0.1.5/formal-directml-v2-result.json`。

最终源码 Mac 工程测试 87 项通过，记录源码在测试期间未变化。测试发现并修正了 Memcpy 搬运算子误计为计算节点的问题；首次失败日志保留，修复后的实机计数复核另列。强制终止的本地任务可能留有未完成的临时目录；Windows Job Object 验证的是 GPU 进程退出，不宣称强制退出能执行所有磁盘清理。

Mac arm64 0.1.5 冻结包已在原生 Cocoa 桌面、`sandbox-exec` 内核拒绝全部网络的条件下通过混合 PDF 转换、取消/继续、修订/重开、页边导出开关及确认/撤销页码修补。两套验收共五个 EPUB 均经 EPUBCheck 5.4.0 检查，零错误、零警告。包解压后的深度严格 ad-hoc 签名、符号链接、CLI 清洁环境启动、资源与源码摘要一致性均通过。

Windows 源码工程测试 86 项通过，1 项跳过：`test_standalone_worker_rejects_other_platform_without_cloud` 只验证非 Windows 平台拒绝启动 Windows worker，因此在 Windows 跳过；实际 Windows 协议与 GPU 另有实机验收。冻结包 686 文件、reader 及对应源码清单均通过完整性检查，清洁 PATH 和 ASCII 环境的 CLI 启动通过；旧 0.1.4 的 681 文件保持逐字节一致。两平台包的全部 28 项生产源码/配置以及 39 项 reader/对应源码文件摘要一致。

Windows 冻结版在活动桌面 Session 1 实际运行四模式：DirectML、CUDA、缺包时 CPU 回退、缺包且禁用回退。每轮同时对 GUI、转换 CLI、对应 GPU Python 建立精确程序路径的临时出站防火墙规则；原生网络探针 10013、阻断前后正控、实际后代进程/已加载模块/TCP 采样及规则清理均通过。DirectML 与 CUDA 各为 3 页 GPU OCR + 1 页文字层；回退模式为 3 页 CPU OCR，严格模式追踪 3 个失败页且不生成 EPUB。三种成功模式各导出正文与页边版本，共 6 个 EPUBCheck 5.4.0 零错误、零警告。格式通过不代表文字准确。

最终 worker SHA 与两平台构建一致，修正后的真实 DirectML 三模型计算节点仍为 918 / 1023 / 465。完整计数复核及实际私有 DLL 来源在 `artifacts/gpu-0.1.5/onnx-final-review.json`；Windows 发布版概览在 `artifacts/release-0.1.5/windows-frozen-gpu-summary.json` 与 `windows-final-summary.json`，原始四模式记录分别保留。

## 使用与兼容边界

Windows 在「资源与设置」可改选 CUDA；先导入相应 GPU 运行包，OCR 模型包保持共用。当前验证的 DirectML 默认适配器为 RTX 4070 Ti；记录 DXGI index 0 与实际 LUID，不按显卡名字猜测设备。首版无多显卡选择器；AMD、Intel、其他 NVIDIA、低显存和旧驱动未实机验证。Mac 沿用 CPU 路径。

全新无开发工具的 Windows 系统仍待独立验证，本次通过私有运行库、清洁环境变量和实际加载模块证明没有依赖用户的 Python/Anaconda。Windows 10、Intel Mac、应用签名与 Apple 公证也未完成；没有以当前机器测试代替这些项目。

原有可重排页眉页脚、确认补写页码和本地兼容阅读器继续保留，规则和阅读器限制见 [0.1.4](RELEASE_0.1.4.md)。加速更换的是推理设备，不会修复阅读器对页眉页脚的兼容问题。

详细离线准备、精确版本、组件许可与数据见 [GPU 运行包](GPU_RUNTIME.md)、[平台说明](PLATFORMS.md)、[第三方清单](THIRD_PARTY.md)。

## 交付与启动

Windows 已部署到 `D:\Projects\pdf-to-epub\release-0.1.5\LocalPDFtoEPUB`，根目录 `启动本地书坊.cmd` 指向新版，旧启动器留有备份，旧发布目录与任务未改。已在用户桌面打开 0.1.5 原生窗口。DirectML 运行目录为 `directml-runtime`，CUDA 为 `gpu-runtime`；两运行包及共用 OCR 模型已准备。新任务默认 DirectML，CUDA 在「资源与设置」中选择，旧任务仍按保存的设备继续。

源码、锁定依赖、构建脚本、通用实机验收脚本、第三方许可及明确选取的测试证据收入 `LocalPDFtoEPUB-0.1.5-source.zip`，不收入用户 PDF、任务缓存或整个开发环境。最终文件摘要另见 `dist/SHA256SUMS-0.1.5.txt`。

| 交付文件 | 字节数 | SHA-256 |
| --- | ---: | --- |
| `LocalPDFtoEPUB-0.1.5-windows-x64.zip` | 139855549 | `4d7c0ce56638cdd597be5d4413e4ee0f9b3bce449d63a2b98653b7cb54753ced` |
| `LocalPDFtoEPUB-0.1.5-macos-arm64.zip` | 97602913 | `3b8bbf18ff028e7fb48f2fa22a38c8c8a542aae691c68e2e507576c328b09a4a` |
| `LocalPDFtoEPUB-directml-runtime-win-x64-1.zip` | 88314354 | `87b85e225b7c34e3a4d5534f5be995f807d9416dd753b86ddf3a7df57fd1cfd0` |
| `LocalPDFtoEPUB-cuda-runtime-win-x64-1.zip` | 1891618837 | `c3dd0f9de1caf4fb116f204153862e7f72c3a9c2da5ad1c63ea10854b9ac488f` |
| `LocalPDFtoEPUB-ocr-resources.zip` | 14893179 | `f0fd040b276f227e73cbf1475d293f0de8097f34dbd0a6be3bca46d093b40f78` |
