# 0.1.4 — 可重排 EPUB 的真实页眉页脚

本版为 Windows x64 / macOS arm64 的本地桌面应用增加可选页边显示。勾选“保留页眉、页脚和原页码（兼容阅读器）”，转换或重新导出后点击“兼容阅读器打开”。原版 Vivliostyle Viewer 2.45.2 在系统浏览器运行（已验证 Edge/Chromium，Safari 等其他浏览器待验），资源随应用提供，只连接本机随机端口；阅读时保持本地书坊运行。

## 实现与对应规则

- EPUB 保留页边原文及来源映射。每个重排阅读页采用其起始正文来源的页眉、页脚、印刷页码。调整字号、窗口后重新计算，不把原 PDF 强制拆成固定图片页。
- 同一原页可延伸为多个阅读页，同一阅读页可含多个原页。不是 PDF 页码与屏幕页序一一对应，也不会因阅读器翻页自动把印刷号码加一。
- 未勾选时不输出已识别页边文字及页码映射。原始提取、修订、坐标和历史记录仍留在任务中，不新增“原页信息附录”，不把页眉文字插进正文。
- 实际识别的号码优先。经用户确认且来源校验有效的补写号码也进入页脚；未确认建议、撤销记录、来源冲突和多候选不自动显示。确认值继续标为人工推算，置信度为空。
- 确定的跨页合并保留精确字符来源区间。任意改写导致跨页区间不可靠时清空该块页边并报告，下一可靠块恢复。来源未知、封面、报告均明确清空状态。
- 不改变 OCR 缓存标识；只升级文档整理阶段。页边开关、样式和重新导出不重跑 OCR。扫描小页码“2”“3”的 CPU 裁图识别继承 0.1.3。
- 修复同页多来源块合并造成脚注引用重复计数；跨页同名 `[1]` 按具体字符来源分别链接，不会把页眉中的 `[1]` 错替换成 HTML 链接。

EPUB 内为标准数据属性、空页码锚点与隐藏的标准 `page-list` 导航。页边 CSS 在阅读器外部配置中，避免 EPUBCheck 5.4.0 不支持页边盒语法导致 CSS-008。该配置依赖 Vivliostyle 的真实分页功能，没有修改第三方引擎，也没有用 JS 改写正文。

## 阅读器与实测

Flow 的固定定位探针在实际 Edge 窗口重现了用户截图的问题：A/B 正文却显示 C 页眉页脚，翻页后页边文字又消失。这只是能画固定元素，未实现正确对应；不作为支持方案。Apple Books 的既有运行元素探针也未通过。Calibre 官方的“Pages from paper edition”支持标准印刷页码显示，但没有据此把它宣称为任意原页眉的完整解决方案。

原版 Vivliostyle 配合本地页边样式通过真实浏览器与生产导出测试。曾发现空行内标记在阅读页首滞后一页，已修为块元素状态、章节起始状态与段内标记分工，并在实际 Edge 窗口复核：原页 4 开头正确显示 23；原页 3 尾段开头正确显示确认补写 22。

源码生产验收使用实际 GUI 产生的混合样本、用户确认 22 样本和关闭页边样本，各在四组字号/窗口条件下逐页检查。每轮共 133 个阅读页全部匹配实际可见正文的首个来源。第二轮把服务器、Node、Chromium 及子进程放进 macOS 系统网络沙箱：公网 socket 被 EPERM 拒绝，本机 HTTP 为 200，阅读仍通过。不是仅依赖“离线”开关或网络 mock。

证据：

- [生产阅读器总记录](../artifacts/production-reader/acceptance-summary.json)
- [系统断外网控制](../artifacts/production-reader/os-network-controls.json)
- [逐页渲染结果](../artifacts/production-reader/render-os.json)
- [60 项工程测试](../artifacts/release-0.1.4/tests.json)
- [来源与页首边界专项验证](../artifacts/production-reader/state-comparison-inline.json)

## 发布验收状态

两平台可运行包已构建并完成以下实际验收。Windows 新版已在用户桌面启动，旧任务和旧发布包保留。

| 项目 | Mac arm64 | Windows x64 |
| --- | --- | --- |
| 当前源码工程测试 | 60 项通过 | 60 项通过 |
| 冻结 GUI | 混合转换、取消恢复、修订、开关重导出、补写及撤销通过 | 同项通过，实际活动桌面 Session 1 |
| GUI/后台转换系统禁网 | macOS deny network* | 应用进程防火墙规则，公网探针阻断与恢复正控通过 |
| 冻结阅读器 | 实际 Edge 窗口；另 4 组字号/窗口共 38 页通过 | 独立 Edge 156：确认样本 40 页、中文混合样本 49 页，共 89 页通过 |
| EPUBCheck 5.4.0 | 检查的实际导出无错误警告 | 6 份实际导出均无错误警告 |
| 包与资源 | 严格 ad-hoc 签名、独立解压运行、阅读器及对应源码完整 | ZIP 681 文件逐个匹配，阅读器及对应源码完整，清洁 PATH 可运行 |

网络验证范围需分别理解：Windows 转换流程实际受操作系统防火墙阻断；Windows 独立阅读器验收使用单测试页请求拦截与正式 CSP，不声称已做 Windows 全浏览器系统禁网。Mac 阅读器源码流程有完整系统阻断外网且允许回环的 133 页验证；冻结包另外验证实际资源、界面和重排。

- [Windows 阅读器 89 页汇总](../artifacts/release-0.1.4/windows-reader-acceptance-summary.json)
- [Mac 冻结阅读器](../artifacts/release-0.1.4/macos-frozen-reader.json)、[实际 Edge 窗口核对](../artifacts/release-0.1.4/edge-reader.json)
- [Windows 冻结 GUI](../artifacts/release-0.1.4/windows-gui-result.json)、[补写与撤销](../artifacts/release-0.1.4/windows-repair-gui-result.json)、[EPUBCheck](../artifacts/release-0.1.4/windows-gui-epubcheck.json)
- [两平台指纹一致性](../artifacts/release-0.1.4/cross-platform-integrity.json)：21 个生产源码文件、39 个阅读器与对应源码文件完全一致，并与当前源码匹配。

## 发布包与试用

| 文件 | 字节数 | SHA-256 |
| --- | ---: | --- |
| LocalPDFtoEPUB-0.1.4-windows-x64.zip | 139391715 | `14859c9074906e6f847b4e8ff2733f7f57a8646a7a214335e314feca7d33e5ea` |
| LocalPDFtoEPUB-0.1.4-macos-arm64.zip | 97123968 | `a8a34c8a5866ee154d694df7418d946888da6acc13ff978571ca96ddfeef7d13` |
| LocalPDFtoEPUB-ocr-resources.zip（未变） | 14893179 | `f0fd040b276f227e73cbf1475d293f0de8097f34dbd0a6be3bca46d093b40f78` |

源码交付为 `LocalPDFtoEPUB-0.1.4-source.zip`，包括本记录、固定依赖、测试 PDF、构建脚本、阅读器运行资源及完整对应源码。最终 ZIP 摘要以同目录 `SHA256SUMS.txt` 为准，源码包内清单逐文件可核验；不把任务快照、模型缓存或私人 PDF 打进源码包。

Windows 路径：

- 启动：`D:\Projects\pdf-to-epub\启动本地书坊.cmd`，已指向 0.1.4，原启动器另存备份。
- 新版程序：`D:\Projects\pdf-to-epub\release-0.1.4\LocalPDFtoEPUB\LocalPDFtoEPUB.exe`。
- 独立试用任务：`D:\Projects\pdf-to-epub\trial-0.1.4\confirmed-page-number.task`；同目录 `confirmed-page-number.epub` 是已确认补写 22 的自制验证样本。确认仍可撤销，原页实际空白，不能把它当 OCR 准确率证据。
- 普通使用：勾选保留页边→转换/重新导出→兼容阅读器打开→A+/A− 调字号。保持应用运行。默认浏览器若不是已验证的 Edge/Chromium，请用已验证浏览器打开本机阅读地址；Safari 等其他浏览器待验。


Windows 干净无开发工具系统、Windows 10、其他 macOS 版本和 Mac Intel 仍待验证。软件未商业签名 / Apple Developer ID 公证。复杂竖排、手写、极低清晰度、复杂脚注与页边误分类仍可能需要人工核对。格式合法不能证明全文文字准确。

## 离线资源和许可

OCR 资源包保持不变。新阅读器运行文件与独立样式约 5.4 MB，完整对应源码材料约 9.1 MB，均随应用包提供；读取不需要 Node.js、Python 安装、账号或互联网。应用的本机 HTTP 只读服务仅提供当前 EPUB 和固定阅读器资源，随机授权路径、Host 校验、路径检查和 CSP 限制避免暴露项目文件或远程加载；带脚本的 EPUB 会明确拒绝。

原版 Viewer/Core 为 AGPL-3.0，独立页边样式为 MIT。完整对应源码和原始发布包在源码交付的 `vendor/sources/vivliostyle-2.45.2/`；应用内 Windows 为 `_internal/reader-sources/vivliostyle-2.45.2/`，Mac 为 `.app/Contents/Resources/reader-sources/vivliostyle-2.45.2/`。其余依赖与字体许可见 [第三方清单](THIRD_PARTY.md)。未宣称已经重建上游 Viewer，仅验证了固定官方发布包及其对应源码来源。

官方依据：[Vivliostyle 本地与 EPUB 输入](https://docs.vivliostyle.org/en/viewer/vivliostyle-viewer/)、[页边与运行字符串支持](https://docs.vivliostyle.org/en/reference/supported-css-features/)、[Calibre 纸书页码](https://manual.calibre-ebook.com/viewer.html)。
