# 验收记录（2026-10-29 至 2026-10-30）

当前源码、依赖、任务产物与原始日志均保留在项目中。自制短样本的成功不推导为任意 PDF 的准确率。

## 环境与样本

macOS 27.2，Apple M6 / arm64，32 GB 统一内存。OCR 实际 provider 为 CPUExecutionProvider；未使用 GPU、CUDA、本机HTTP推理服务或远端API。Python 3.12.15，pdfplumber 0.11.6，PDFium 5.14.0，RapidOCR ONNX Runtime 1.4.4，ORT 1.20.1。Qt 已升级到6.12.0，保留 Accessibility，并使用界面条目复用修复；最终依赖以 requirements.lock / dist/build-info.json 为准。

Windows实机为Windows 11 Pro 10.0.26300 x64，i5-13600KF，约32 GB RAM，RTX 4070 Ti（驱动查询12282 MiB显存）；实际OCR同样使用CPU。两平台分别构建，Windows发布解释器另用独立CPython 3.12.15，不依赖宿主Anaconda。完整记录见 [Windows验收](WINDOWS_TEST_REPORT.md)。

| 自制 PDF | 页数 | 核验内容 |
|---|---:|---|
| 01_chinese_crosspage.pdf | 2 | 中文、中英混排、书签、页边内容、跨页正文 |
| 02_mixed_bad_layer.pdf | 4 | 可靠文字、纯扫描、cid错误层、同页文字与扫描区域 |
| 03_columns_figures_notes.pdf | 2 | 双栏顺序、插图、脚注、4×3表格及空值、上标公式、矢量图 |

所有样本、人工真值JSON和原页预览在 tests/fixtures。固定依赖重新生成样本并逐页目检。最终最小OpenCV依赖下，60条指定片段与顺序全部匹配（只忽略空白、不忽略标点）；记录 artifacts/backend-final/summary.json。没有计算全文OCR准确率。

## 已实际通过

- 后端：首次OCR独立进程成功，处理方法为 text/text、text/ocr/ocr/mixed、text/text；没有输出cid坏层，也没有重复附带扫描正文截图。图表公式/脚注保留，表格空单元格保留。
- 工程集成：最终依赖在内核禁网条件下10个真实小PDF测试通过，artifacts/pytest-final.log，7.62秒。涵盖源文件不变、缺资源与失败页、逐页恢复、重新识别保留修订、参数缓存失效、同名异内容拒绝、取消再继续、部分结果阻止/显式导出、资源篡改和ZIP路径攻击拒绝、密码、手工跨页合并保留全部来源、拒绝覆盖含无关文件的资源目录、坏页面检查点识别和重试。
- GUI工程回归：artifacts/gui-stable-text/result.json 覆盖跨页合并保留全部来源，artifacts/offline-final/gui/result.json 覆盖拖放、取消恢复、编辑、重导出与重开。前者使用Qt offscreen平台，后者为实际Cocoa桌面；二者均使用真实QProcess和解析引擎，无OCR或网络mock。最终独立包另经原生界面操作，见下文。
- 系统禁网：scripts/offline_acceptance.py 在 macOS sandbox-exec 的 `(deny network*)` 规则下启动真实 Cocoa GUI，及继承规则的后台进程。公网1.1.1.1:443、回环127.0.0.1:443 的原生socket探针均返回EPERM；lsof进程树采样未观察到互联网套接字。最终依赖下真实GUI仍完成混合4页、取消恢复、人工修订及导出。证据 artifacts/offline-final/network-probe.json、process-audit.json、gui/result.json、run.log 和截图。
- 主动联网资源准备：实际调用固定URL下载，验证wheel与模型SHA后安装成功，artifacts/resource-download.log；本地ZIP导入同样通过。该下载不发送文档。
- 格式：最终独立包的各样本产物和GUI校对重导出文件均通过 EPUBCheck 5.4.0，零fatal/error/warning；记录 artifacts/packaged-final-utf8/epubcheck.json、artifacts/native-release-utf8/epubcheck.json 和 artifacts/offline-final/epubcheck.json。只代表格式符合，不保证文字正确。GUI不配置JAR时仍如实显示EPUBCheck未运行，本次由本地验收工具额外校验。
- 阅读器：通过 Apple Books 的真实UI导入，正文可选读；连续增大字体后句子重新换行；目录显示章节并成功跳转第二节；普通表格、空值、公式原区域与矢量图显示正常。操作证据在本次聊天的电脑操作记录；未把HTML静态检查当作阅读器实测。
- 最终独立包CLI：Mac arm64 `.app` 与ZIP已构建，移除PYTHONPATH/VIRTUAL_ENV并限制PATH为系统目录后，冻结CLI在内核禁网下成功转换全部8页。源码摘要与包内构建信息对应，ad-hoc签名验证通过；包内无FFmpeg/x264/x265视频库。证据 artifacts/packaged-final-utf8/summary.json 和 artifacts/package-integrity.json。发布ZIP另在隔离临时目录解压后启动成功，134个符号链接均无断链，签名检查通过，见 artifacts/archive-extraction-check.json。
- 最终独立包GUI：scripts/native-package-offline.py 以系统目录PATH、无开发Python/Qt路径启动最终.app，全程由macOS内核禁网，并强制初始 `PYTHONIOENCODING=ascii`。通过CUA实际操作缺模型提示→空目录导入资源ZIP→混合4页转换→原页回查→人工修订→重新导出→单页重新识别→修订仍在最终EPUB→重排预览→正常退出0。Accessibility始终启用；记录到GUI及冻结后台进程，无网络套接字。证据 artifacts/native-release-utf8/result.json、process-audit.json、network-probe.json 和最终格式校验记录。
- 最终交付补验：增加显式`gui-test`验收入口后，Mac最终包再次在Cocoa桌面和内核禁网下完成拖放、取消130、恢复0、校对重导出0、重开，原生socket公网/回环均EPERM；采到GUI及3个worker，未观察到网络套接字。证据 `artifacts/macos-gui-package/`，其process-audit记录最终ZIP摘要。所有正常业务代码与此前CUA验收一致；入口复用生产Window/QProcess，没有模拟OCR或网络。最终包的EPUBCheck、签名、全新临时目录解压与启动均通过。
- Windows源码与发布包：10项集成测试28.50秒通过，三份样本8页与60个片段核对通过，冻结CLI的三个输出通过本地EPUBCheck，GUI编辑输出也在Mac本地Java通过EPUBCheck。Windows Firewall对真实程序和独立原生Socket探针设置临时出站阻断，已确认公网端点先连通、阻断后10013、清理后恢复；冻结CLI照常完成混合PDF。发布ZIP解压后的GUI在活动桌面会话1完成真实拖放、取消、恢复、修订、重导出和重开，进程模块均来自应用内运行库，临时规则/计划任务已清理。详见 `artifacts/windows/` 及专门报告。

## 交付文件

| 文件 | 大小 | SHA-256 |
|---|---:|---|
| LocalPDFtoEPUB-0.1.0-windows-x64.zip | 128,684,659 字节 | f64ce4b75000a948fea37d7a36550fa799dc085f06ae8d6ae7995012dd4f6c5a |
| LocalPDFtoEPUB-0.1.0-macos-arm64.zip | 86,387,608 字节 | 4fd7464f8c426608af3b1b6ae9c8c5203a0d8933080788edc4d2c39123e18b97 |
| LocalPDFtoEPUB-ocr-resources.zip | 14,893,179 字节 | f0fd040b276f227e73cbf1475d293f0de8097f34dbd0a6be3bca46d093b40f78 |

源码与验收记录另装入 `LocalPDFtoEPUB-0.1.0-source.zip`，包含最小OpenCV的官方源码原包、本地wheel、构建补丁与许可；其摘要见 dist/SHA256SUMS.txt。应用包无需用户Python；EPUBCheck的Java/JAR未随主包分发。

Windows最终归档在GUI验收后仅更新两份随包文档；逐ZIP条目对比确认EXE、运行库及其他所有文件字节不变，见 `artifacts/windows/docs-refresh.json`。Windows发布目录再逐文件核对最终ZIP并复查CLI启动，见 `package-integrity.json`。早期验收记录中的原归档摘要因此保留作历史证据，不冒充最终摘要。

## 实际发现并处理的问题

1. RapidOCR 1.4.4 检测/分类组件使用 infer.session，识别使用 session.session；已按实际API修正，并仅在验证成功后写缓存，避免首个OCR页失败、后续页绕过检查。
2. 拆散上下标公式会产生错误文本顺序；改为原区域图片并标记降级，其他正文仍为文本。
3. 取消发生在任务创建前可能留下非空目录；取消文件移到任务目录旁，已回归。
4. 重新导出的元数据原先不能在重开任务后保留；现在保存到 job.metadata。
5. 首个标题前的页眉被单独拆成空旷章节；已修正分章逻辑。
6. 正常工具沙箱会阻止Qt的ARM NEON特征探测；实际GUI/构建用正常桌面权限，离线测试另以明确的内核网络沙箱约束，不把启动失败误报为软件可运行。
7. 初版包真实Accessibility操作中，在修订保存/重导出后出现 Cocoa AX 原生崩溃。修订与EPUB已经成功保存，崩溃栈为 objc_retain / libqcocoa / NSAccessibility。已改为条目复用和增量更新，并升级Qt6.12.0，不关闭Accessibility。修复版原生独立窗口先通过连续两次编辑/保存/重导出回归，最终包也重新完成相同关键流程，见 artifacts/native-release-utf8/result.json。
8. 发布审查发现官方Mac OpenCV wheel带GPL视频库；改用禁用视频、FFmpeg和GUI的本地最小图像处理wheel，保留官方源码原包、补丁、固定构建依赖、源码与wheel摘要。PNG、resize、contours及全部真实OCR样本已通过。Windows官方wheel另经审查，仅带LGPL FFmpeg。详见 packaging/opencv-*-audit.json 和 artifacts/opencv-minimal-validation.json。Mac本地wheel的SHA256为31dc606faece9c9fc22e47f7eefcdda98c6d21f6d7f39cd20b77d92efdd937a1。
9. 资源包覆盖目标含无关文件时原先可能删除该目录；现在只允许空目录或内容严格匹配的已验证资源目录，备份也只在本次临时目录内。已有无关文件及相邻 `.previous` 目录均有回归保护。
10. 页面检查点是合法JSON但缺少内容结构时，原先可能被当作成功缓存；现验证页码、状态和内容块结构，损坏页显示缺失并重新解析。
11. 非UTF-8终端输出环境可能导致中文JSONL报编码错误，进而让Windows GUI读取失败。CLI现在显式配置UTF-8标准输出和错误输出；真实混合PDF子进程测试在 `PYTHONIOENCODING=ascii` 下使用中文任务/导出路径并核验中文进度。后续Windows实机也已覆盖该环境和真实QProcess流程。

## 待验证

Windows发布包、实机GUI/CLI和防火墙测试已经完成；仍未在无开发工具的全新Windows系统验证。当前主机已有Anaconda，Windows Sandbox关闭且固件虚拟化未启用，没有擅自修改这些设置。Windows 10、macOS其他版本/Intel、长篇真实书籍、恶劣扫描和复杂版面尚未验证。两平台包未用发行者证书签名，Mac包未经Developer ID公证。

