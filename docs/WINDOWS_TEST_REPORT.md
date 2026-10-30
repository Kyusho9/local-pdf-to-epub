# Windows x64 实机交付记录

日期：2026-10-30（Asia/Shanghai）。通过用户指定SSH连接实际Windows电脑，项目文件统一放在 `D:\Projects\pdf-to-epub`。

## 可直接运行的位置

- 应用：`D:\Projects\pdf-to-epub\release\LocalPDFtoEPUB\LocalPDFtoEPUB.exe`。
- 离线资源ZIP：`D:\Projects\pdf-to-epub\LocalPDFtoEPUB-ocr-resources.zip`。普通用户从应用“资源与设置”导入；无需Python或命令行。
- 开发源码：`D:\Projects\pdf-to-epub\source`；构建用独立运行时：`D:\Projects\pdf-to-epub\runtime`；构建环境：`source\.venv-release`。
- 本次验收导入的模型位于 `source\resources`，不依赖系统模型缓存。原有Anaconda未改动。

## 环境与实现

Windows 11专业版，10.0.26300，AMD64；i5-13600KF，14核/20线程，内存34,104,016,896字节。最终nvidia-smi查询报告RTX 4070 Ti、12282 MiB显存、驱动617.42（`hardware-details.json`）。OCR实际仍为CPUExecutionProvider，没有使用CUDA或GPU推理。

宿主原有Python 3.12.7和Oracle Java 21.0.9。发布构建另外通过uv 0.13.1获取Python 3.12.15独立运行时，不注册系统Python、不改变用户PATH；应用包携带该运行时。依赖按requirements.lock安装。Python独立分发依据：[uv Python版本说明](https://docs.astral.sh/uv/concepts/python-versions/)。

## 已实际验证

1. 源码闭环：新发布运行时10项集成测试通过，28.50秒；三份自制PDF共8页，60个原文片段及顺序全部匹配。没有将其当作通用OCR准确率。记录 `artifacts/windows/pytest.log`、`backend-summary.json`。
2. 冻结CLI：从应用目录启动，移除Python/Qt开发路径，仅保留系统PATH；三份PDF全部转换成功。输出全部通过Windows本地Java运行的EPUBCheck 5.4.0，零错误、零警告。记录 `packaged-summary.json`、`epubcheck.json`。
3. 真实防火墙：临时规则只针对本次GUI、CLI和独立探针。公网223.5.5.5:443先连接成功，启用规则后原生Socket返回10013/AccessDenied；被冻结CLI在此期间完整转换混合PDF；规则移除后探针连接恢复。未更改网卡、全局出站策略或SSH规则。记录 `offline-cli.json`。
4. 独立包GUI：从发布ZIP解压到release目录，在活动Windows桌面会话1启动GUI，包内gui-test驱动真实Qt事件和真实QProcess。全程应用出站规则启用；取消worker退出130，恢复worker退出0，重新导出退出0；人工修订和任务重开校验通过。`frozen=true`、平台插件`windows`、`stdout_available=false`。记录 `gui-result.json`、`gui-offline.json`、`gui-worker.log`与`gui-complete.png`。
5. 运行库：实际采样GUI及OCR worker的已加载模块，Python、Qt、VC++、PDFium、ONNX Runtime和OpenCV均来自解压后的应用目录，没有Anaconda或venv模块依赖。记录 `process-modules.json`、`binaries.json`。
6. 网络与清理：GUI验收按PID取得9次TCP采样，无建立的网络连接；原生进程规则适用于全部协议，采样本身仅覆盖TCP且可能漏过短命进程。临时交互计划任务及本次防火墙规则均删除并复核。没有把采样当成完整网络抓包。

GUI人工校对后的EPUB另外下载到Mac，以本地Java/EPUBCheck 5.4.0检查，零错误、零警告，并核验“人工校对验收标记”仍在正文；记录 `gui-epubcheck.json`，明确区分产物与校验主机。最终归档只补入更新的两份随包文档，所有EXE和其他条目与验收版本字节一致，记录 `docs-refresh.json`；发布目录所有文件与最终ZIP再次逐一校验。

## 尚未验证

这不是干净系统测试：主机原先安装了开发工具。已清除运行环境路径并核查DLL来源，但Windows Sandbox处于关闭状态，固件虚拟化报告关闭；本次没有更改这些系统设置。仍需另一台无开发环境机器，或用户另行准备的干净Windows虚拟机。

Windows 10、其他Windows版本、低内存配置、长篇真实书籍及恶劣扫描未覆盖。Windows应用尚未代码签名。EPUBCheck使用本机现有Java进行验收，Java/JAR未捆绑进主应用，未配置时软件如实显示“EPUBCheck未运行”。实际阅读器字号重排、目录和图像检查此前在Mac Apple Books完成，未声称已在Windows阅读器中实测。
