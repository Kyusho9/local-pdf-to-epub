# 0.1.2：可选原页信息，正文保持可重排

日期：2026-10-30。用户选择继续使用可调字号的重排 EPUB，同时希望按需保留原 PDF 的页眉、页脚和页码。本轮只调整这个导出选项及相关章节组织，未引入固定版式或 GPU 路径。

## 已实现

- 转换页「输出与排版」增加「附带原页信息（页眉、页脚、原页码）」，默认开启。状态保存于任务的 `export_options.include_page_info`，重新打开任务会恢复。旧任务未记录此项时默认开启。
- 开启时，已识别的原页信息保存在 `EPUB/page-info.xhtml` 附录，按原 PDF 页序列出，并附正文定位链接与 EPUB `page-list` 来源页导航。正文中的 `pagebreak` 标记表示来源页边界，不是要求阅读器强制分页；跨页合并段落的来源边界不确定时，定位说明会指出是段落起点。
- 关闭时，不生成原页信息附录、来源页导航及相关边界标记。正文始终可重排；校对区的单块「正文处理」保留/排除功能继续独立生效。原始稿、原页和人工修订保留。
- GUI 的真实后台进程显式接收 `--include-page-info` 或 `--no-include-page-info`；CLI 的 `convert`、`resume`、`export` 同样支持这两个选项。切换后重新导出即可，不重跑 OCR。
- 标题的目录锚点与独立章节文件分开处理。同一原页内的普通标题不再仅因标题样式拆成新的章节文件；与 PDF 一级书签页码和标题匹配的顶层标题仍可分章，人工改名后保留该书签对应关系。无法可靠定位到正文的书签不凭空强分空章节，保留定位疑点供核对。标题仍可从目录定位。

## 阅读方式与识别边界

原 PDF 页序用于原页回查；印刷页码来自页面中实际识别出的文字；阅读器的屏幕页数由字号、行距、屏幕尺寸和排版决定。这三者不能互相替代。改变字号后屏幕页数变化正常，原页信息附录不保证每个阅读屏出现固定页眉或页脚，也不把正文改为整页图片。阅读器是否提供单独的来源页导航入口取决于它对 `page-list` 的支持，附录中也提供正文定位链接。

混合样本 `02_mixed_bad_layer.pdf` 的扫描第 2、3 页原图确实包含印刷数字「2」「3」，目前 OCR 未提取出这两个页码。附录如实显示未识别到可靠内容，数据中保留空列表，不按 PDF 第 2、3 页的序号补写。这一版没有宣称解决 OCR 漏字，仍需对照原页检查。

目录、附录及分章由导出逻辑决定，切换 CPU/CUDA 不会修复这些结构问题。本轮没有更换识别模型、启用 GPU 或修改逐页 OCR 缓存版本；本地 CPU 路径继续可用。

## 已实际验证

| 项目 | 当前状态与证据 |
| --- | --- |
| 源码回归测试 | 最终 25 项在两平台全部通过：Mac 16.02 秒，Windows 49.66 秒，包含无法定位书签及人工改名回归。记录 `artifacts/release-0.1.2/tests.json`、`tests.log`、`windows-pytest.log`。 |
| 原生 GUI 开关与后台导出 | 已在 macOS Cocoa 窗口完成开→关→开，真实 QProcess 参数与产物对应，全部退出 0；重新打开任务分别恢复开启和关闭。记录 `artifacts/page-info-gui/result.json`。 |
| 缓存与人工修订 | 同一混合任务三次导出，8 个原始/页面检查点的内容摘要及修改时间均未变；正文人工修订仍保留。相同开启选项的两次 EPUB 摘要一致。 |
| 界面布局 | 1220×840 默认窗口和 960×680 最小窗口中，选项可通过表单滚动完整显示；说明明确正文重排及原页附录，不承诺固定阅读器页眉。 |
| 源码 GUI 系统禁网回归 | 已在原生 Cocoa 与系统网络阻断下完成真实后台流程及开关切换。记录 `artifacts/release-0.1.2/source-gui/`。 |
| 实际阅读器 | Apple Books 原生界面实测：原 PDF 第 4 页的主、次标题连续显示，两项目录均定位到同一阅读页，子标题跳转可用；原页附录可见、链接可返回正文；增大字号后文本实际重排，测试后恢复字号。记录 `artifacts/release-0.1.2/apple-books.json`，限本次自制混合样本，不代表其他阅读器已验证。 |
| EPUB 格式 | 两平台最终包的验收产物通过 EPUBCheck 5.4.0，零错误、零警告；Windows 冻结 CLI 生成的带原页信息、仅正文两个试用文件也通过。记录 `macos-gui/epubcheck.json`、`windows-epubcheck.json`、`windows-cached-export.json`。格式通过不代表 OCR 文字无误。 |
| Windows x64 0.1.2 发布包 | 已构建并在活动桌面会话 1 完成冻结 GUI 的实际出站防火墙阻断验收：取消、恢复、人工修订、导出、原页信息开关与重开全部通过；临时规则和验收计划任务已清理。记录 `windows-package.json`、`windows-gui.json`、`windows-gui-result.json`。 |
| macOS arm64 0.1.2 发布包 | 已构建并在真实 Cocoa 与内核网络阻断下完成相同 GUI 流程；原生公网/回环探针被拒绝。独立新目录解压、临时签名完整性和 CLI 启动通过，134 个框架符号链接有效。记录 `macos-package-integrity.json`、`macos-gui/`、`macos-archive-extraction-check.json`。 |

表中未写目录前缀的证据均位于 `artifacts/release-0.1.2/`。两平台构建记录中的 18 个源文件与资源条目摘要一致，并与当前交付源码匹配；不以旧版包的验收记录代替本版证据。

## 发布与试用

| 发布包 | 字节 | SHA-256 |
| --- | ---: | --- |
| LocalPDFtoEPUB-0.1.2-windows-x64.zip | 128745957 | 98a4da673d2d54674d50b5110b727984aea22f65ae21d1519781beab521fe7f6 |
| LocalPDFtoEPUB-0.1.2-macos-arm64.zip | 86448458 | 6971b123c28b714f29679dacac1d2737fbe503cd4f5bda0c75c2620be22adebb |

文件位于 `dist/`。源码归档为 `LocalPDFtoEPUB-0.1.2-source.zip`；源码包和资源包摘要统一见 `dist/SHA256SUMS.txt`。模型包与锁定依赖不变。

Windows 新版独立部署在 `D:\Projects\pdf-to-epub\release-0.1.2\LocalPDFtoEPUB`，已以普通用户权限在活动桌面会话 1 启动，窗口标题显示 0.1.2。根目录「启动本地书坊.cmd」已指向该版本，并使用 `D:\Projects\pdf-to-epub\resources` 中已校验的现有模型。旧应用目录、运行窗口及原任务保留。

原混合任务复制到 `D:\Projects\pdf-to-epub\trial-0.1.2\mixed-corrected.task` 后，使用本版冻结 CLI 导出两种选择，文件均位于 `D:\Projects\pdf-to-epub\source\tests\fixtures`：

- `02_mixed_bad_layer-0.1.2-with-page-info.epub`：附带原页信息。
- `02_mixed_bad_layer-0.1.2-body-only.epub`：不附带原页信息。

产物正文为 4 个章节文件，同页普通主、次标题保持连续；这不代表阅读器必须显示 4 屏。两份产物都通过 EPUBCheck；原任务、人工修订和副本中的 `raw/pages` 检查点未改变，没有重跑 OCR。完整记录见 `artifacts/release-0.1.2/windows-cached-export.json`。

## 保留的验证边界

历史 [0.1.1 记录](RELEASE_0.1.1.md) 和 [首版测试记录](TEST_REPORT.md) 保留不变。Windows 全新无开发工具系统、Windows 10、其他 Mac 系统版本/Intel、代码签名与 Apple 公证等既有待验证项没有因本轮导出修改而消失。扫描复杂版式、低清晰度、竖排、手写及复杂脚注仍需人工核对。
