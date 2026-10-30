# 0.1.3：撤掉原页附录，修复扫描页小页码漏检

日期：2026-10-30。本版 Windows x64 与 macOS arm64 包已构建并完成实际冻结 GUI、系统网络阻断、两平台完整回归与 EPUBCheck。Windows 普通用户窗口已启动，旧任务副本已重识别并生成试用产物。源码归档在文档定稿后生成，所有交付文件以 `dist/SHA256SUMS.txt` 校验。历史版本记录保留不改，不用旧包通过代替本版验证。

## 需求纠正与当前行为

用户要求原 PDF 页眉、页脚在重排后的阅读页顶部、底部正确显示。0.1.2 把这些内容移到“原页信息”附录，未满足该需求。本版撤掉该附录、来源页 `page-list`、`pagebreak` 锚点，以及校对区用于页边去重的单块“自动／保留／排除”入口。正文中的普通标题不再仅因视觉样式而强制拆章，上一版已修复的书签目录定位和人工改名行为继续保留。

目前没有验证通过的阅读器方案能满足原页页边与重排页面的正确对应，因此保留页眉页脚功能尚未完成：

| 输出参数 | 当前行为 |
| --- | --- |
| `retain_page_margins=False`，默认 | 在导出副本中省略可靠识别的页眉、页脚、页码；保留可重排正文。转换报告仅列省略数量，不另建页边文本附录。 |
| `retain_page_margins=True` | 明确报不支持；不生成替代附录，不插进普通正文，不转固定版式。源码和两平台冻结 GUI 均禁用该选项并说明原因，实际验收已核对。 |
| 旧 `include_page_info=True` | 拒绝旧选项，说明附录已移除。 |
| 旧 `include_page_info=False` | 无害兼容，不产生任何附录或来源页导航。 |

页边分类、输出省略、原始稿是三个独立步骤。原始提取、自动整理、来源坐标与人工修订完整保留；旧页边的单块保留/排除决定留作历史，不覆盖新输出策略。有效的旧正文人工决定仍受原文与来源签名约束，重识别后不匹配时保留冲突，不沿用旧块 ID 静默丢字。任务输出选项升级为 schema 2。

## 阅读器机制实测与资料

测试只使用自制 EPUB。实测范围是 Apple Books 原生应用，以及上游 epub.js 0.3.93 / Chromium 151.0.7922.34。后者不是完整 Flow；Flow 使用自己的 `@flow/epubjs` 分支，因此不能把上游测试冒称 Flow 已完整验收。epub.tools 完整网站也没有在本轮完成阅读器验收。

| 机制 | Apple Books | epub.js / Chromium |
| --- | --- | --- |
| CSS 运行元素 `position: running()`，配合页边框 | 正文显示，运行页眉页脚未出现。 | `position: running()` / `string-set` 支持检查为 false；首屏、下一屏、160% 字号均未显示运行页边。常量 `@page` 页边框也未显示。 |
| `position: fixed` | A/B/C 来源的页眉、页脚叠在同一屏；翻页后部分覆盖正文，另一阅读页没有相应页边。 | A 正文屏出现 C 的页眉页脚，三个来源位置重叠；翻页和放大后仍错配。 |

可核查证据：[研究汇总](../artifacts/running-margins-probe/research-result.json)、[Apple Books 观察与样本摘要](../artifacts/running-margins-probe/apple-books-result.json)、[epub.js 页面位置、样式与本地请求记录](../artifacts/running-margins-probe/epubjs-result.json)。测试截图与合成 EPUB 位于同一目录。只据此认定所测机制没有达到要求，不断言所有阅读器永远无法实现。

官方依据及边界：

- EPUB 3.2 变更记录已移除旧 `oeb-page-head` / `oeb-page-foot`；旧教程中的声明不能作为当前阅读器支持保证。[W3C EPUB 3.2 Changes](https://www.w3.org/publishing/epub32/epub-changes.html)
- `running()` / `element()` 属于 CSS Generated Content for Paged Media 工作草案；同一页多次赋值还需选择 first/start/last 等策略，并没有自动解决原 PDF 页与重排屏幕页的对应。[W3C GCPM 3](https://www.w3.org/TR/css-gcpm-3/#running-elements)
- epub.js 的分页路径使用 CSS columns；多栏屏幕布局不会因写入 `@page` 就自动变成打印页面框。[epub.js 0.3.93 源码](https://github.com/futurepress/epub.js/blob/v0.3.93/src/layout.js)。Readium 官方也说明其分页使用多栏，并由阅读器控制容器尺寸与外边距；这里只引用文档，没有声称实跑 Readium/Thorium。[Readium 分页说明](https://readium.org/css/docs/CSS03-injection_and_pagination.html)
- Flow 使用项目内分支，上游相同版本号不足以代表完整应用行为。[Flow 的 EPUB 包声明](https://github.com/pacexy/flow/blob/main/packages/epubjs/package.json)

固定定位测试 EPUB 通过 EPUBCheck 5.4.0 仍会显示错误；格式合法不等于排版正确。三个含 `@page` 页边框的探针各出现两项 CSS-008，这是本次校验器的兼容性结果，不能单凭此宣称相关 CSS 草案语法非法。具体日志为同目录的 `*.epubcheck.json`。

## 小页码 OCR 修复

混合样本第 2、3 页原图确实印有“2”“3”。整页检测在这些区域的概率响应过低，没有生成候选框；降低最小候选尺寸仍无法恢复。这是实际漏识别，与阅读器重排和 CPU/CUDA 无关。

后端升级为 `pdfplumber-rapidocr-v4`，继续使用相同本地 RapidOCR 模型和 CPU。对上下各 8% 的原页裁图补跑检测，接受有可见墨迹、位置和大小合理、未与已有识别重叠、识别分数至少 0.95 的短数字。坐标映射回原 PDF，真实识别分数及 `page-margin crop` 来源保存在中间结果中。分数是模型输出，不是识别准确率；此路径不读取 PDF 页序来猜数字。

实际小样本验证：第 2 页读出“2”（0.999379），第 3 页读出“3”（0.999204）；原有整页 OCR 文字和分数逐项未改，正文真值顺序保持、源 PDF 摘要不变。内存对照图“9”“17”“88”读出对应文字，空白、点、横线、I/O 和正文中央数字未被补成页码。专用测试为 [test_margin_ocr.py](../tests/test_margin_ocr.py)，当前本机 6 项通过；这不代表所有字体、扫描质量或页码形式均已解决。

**旧任务只重新导出不会运行 OCR，也不会修补旧缓存里的漏字。** 应使用 GUI 的继续任务，或 `resume JOB --resources DIR`。后端版本进入缓存身份，继续任务会按新版本重识别；人工修订记录继续保存，内容或来源变化时显示待确认冲突。样式和输出设置变化仍不需要重做 OCR。

## 页码推算与人工确认

对于仍无可靠页码的页面，建议机制只接受前后两端唯一、实际识别的正整数页码，要求格式一致、数值与物理页距连续，中间最多 3 页。缺页、失败页、冲突、未分类页边内容或人工推算值不作为可跨越的证据。建议可能对应原书故意留空的页码，不能自动补写。

用户必须回查原页后明确确认，也可以撤销。确认记录写入独立人工修订字段，来源为 `user_confirmed_inference`，置信度为空；不进入原始 OCR、正文块或实际识别页码列表，也不作为后续推算证据。当前不保留页边的输出模式不会把确认值写进正文，报告明确称其为人工推算、非 OCR，并逐条列出待确认建议的原页序、建议值和说明。

确认绑定源摘要、前后实际证据及目标页 raw/automatic 检查点摘要。确认和导出入口都会重新计算任务内 `source.pdf` 的真实 SHA-256，与任务记录核对；源快照被替换、变动或丢失时拒绝操作，不只信任 JSON 中保存的旧摘要。原人工记录保留，仍可撤销。

重新打开任务会恢复有效确认；证据或检查点改变时原记录保留为冲突，撤销保留审计历史。专用 [page-number 测试](../tests/test_page_number_repairs.py) 当前本机 8 项通过，包含自制原书故意不印中间页码的真实 PDF，以及源快照改变后的确认拒绝与历史保留。另用旧混合任务副本实际验证：保存人工确认后升级至 v4 并继续任务，四页页码均真实识别，旧推算转为冲突，没有写回 OCR。

源码原生 Cocoa GUI 已完成自制五页 PDF 的真实转换、主动确认、导出、重开及撤销。未确认时故意留空的印刷页码保持为空；确认后身份仍为人工推算，原页来源和实际提取页码不变；撤销后建议恢复，源文件和 10 个 raw/pages 检查点未改。旧正文人工排除可单独恢复，同时保留人工文字修订；旧附录开关不能开启不支持的页边模式。两个真实后台进程均退出 0，界面说明经原生目检，证据见 [源码 Cocoa GUI 记录](../artifacts/page-number-repair-gui/result.json)。这项验证不等同冻结包、Windows 或系统断网验收。

## 原页预览与最终源码流程

原页预览现在先写入临时 PNG，完整生成后再替换正式缓存，避免界面读到尚未写完的文件。重新使用缓存前检查图片是否可解码；发现损坏缓存时从任务内实际 PDF 重新渲染。预览属于可重建的派生文件，修复不会修改源 PDF、原始识别或人工修订。

已在最终源码 Cocoa 流程中主动制造损坏的预览 PNG，确认其在页码确认前从真实 PDF 重建。自制 `04_page_number_gap.pdf` 的第 3 页故意不印页码，两端实际印刷“21”“23”，建议值为“22”，与物理 PDF 页序“3”不同：只有点击确认才保存人工推算；重开仍保留身份，点击撤销后恢复未确认建议，再导出没有确认值，正文和实际页码不变。

该次流程还实际执行了拖入 PDF、开始、取消、检查点恢复、人工文字修订、三次重新导出与任务重开。转换子进程取消退出 130；恢复及三次导出均退出 0，导出没有重跑解析，列表对象保持稳定。证据为 [最终源码 GUI 记录](../artifacts/page-number-harness-final/gui/result.json)，其中 `frozen=false`、`platform_plugin=cocoa`；这不是冻结应用包或系统断网结果，也不以该故意留空页码样本验证扫描页 OCR。

## 验收和发布状态

| 项目 | 状态 |
| --- | --- |
| 核心源码行为 | 已实现；OCR 专用 6 项、页码修订 8 项、章节 4 项在开发环境通过。 |
| 阅读器机制验证 | 已完成上述 Apple Books 与 epub.js/Chromium 探针；所测运行页边方案未通过，因此不启用。 |
| 完整工程回归 | 两平台各 43 项通过：Mac 37.29 秒，Windows 120.71 秒。见 [tests.json](../artifacts/release-0.1.3/tests.json)、[Windows 日志](../artifacts/release-0.1.3/windows-pytest.log)。Windows 首轮 3 项失败由测试读取中文 JSON 未显式指定 UTF-8 引起，修复仅涉及测试文件；产品源码未因该问题改动。 |
| 0.1.3 源码 Cocoa GUI 页码确认/撤销/重开 | 已通过真实后台转换、导出、确认与撤销、历史正文恢复；最终源码另通过损坏预览缓存重建。 |
| 0.1.3 源码 Cocoa GUI 取消与检查点恢复 | 已通过，取消退出 130、恢复退出 0；见最终源码 GUI 记录。 |
| macOS arm64 冻结 GUI 与系统断网 | 实际 Cocoa GUI 和后台程序继承内核 `deny network*`，公网及回环探针均被拒绝；观察不到已连接套接字。混合样本 5.1 秒，含取消恢复、实际 OCR 页码、人工修订和重开；页码缺口样本另用 1.63 秒验证确认/撤销及损坏预览重建，该轮未单独请求取消。见 [混合 GUI](../artifacts/release-0.1.3/macos-gui/gui/result.json)、[页码 GUI](../artifacts/release-0.1.3/macos-page-number-repair-gui/gui/result.json) 及两目录中的 `process-audit.json`、`network-probe.json`。 |
| macOS 独立解包及运行时 | 新临时目录解包、临时签名完整性、CLI 启动和 ASCII 环境下 UTF-8 检查通过，134 个框架符号链接有效，无意外视频库。见 [包完整性](../artifacts/release-0.1.3/macos-package-integrity.json)、[独立解包](../artifacts/release-0.1.3/macos-archive-extraction-check.json)。 |
| Windows x64 冻结 GUI 与防火墙 | Windows 11 活动桌面会话 1 实际运行冻结 GUI/worker；混合样本 9.45 秒、页码缺口样本 3.09 秒，两套均经过取消恢复、重开及各自功能验证。GUI 和 worker 使用临时出站阻断规则，验收后规则与任务全部清理。见 [混合防火墙验收](../artifacts/release-0.1.3/windows-gui.json)、[页码防火墙验收](../artifacts/release-0.1.3/windows-repair-gui.json)、对应 `windows-*-gui-result.json`、模块与 TCP 采样。Windows 防火墙未被当作回环阻断保证，软件不使用回环推理。 |
| 两平台构建一致性 | Windows 构建及独立发布目录解压成功，旧发布目录保留；归一化路径分隔符后，两平台构建记录的 20 个源码/资源条目摘要一致。见 [Windows 包记录](../artifacts/release-0.1.3/windows-package.json)、[跨平台完整性](../artifacts/release-0.1.3/cross-platform-integrity.json)。 |
| EPUBCheck | 两平台各两套最终冻结 GUI 产物，以及 Windows 旧任务副本的新版试用产物，均通过 EPUBCheck 5.4.0，零错误、零警告。Mac 证据见两目录中的 `epubcheck.json`；Windows 见 [GUI 格式汇总](../artifacts/release-0.1.3/windows-gui-epubcheck.json)、[旧任务试用记录](../artifacts/release-0.1.3/windows-cached-export.json)。格式通过不代表文字准确。 |
| 实际阅读器抽查 | Apple Books 实际打开冻结 CLI 导出的本版 EPUB：原生与扫描正文、人工修订可见；第 4 原页主次标题连续，目录均定位到同一阅读页；放大字号后中英文真实换行，随后恢复字号；目录无旧原页附录。见 [Apple Books 记录](../artifacts/release-0.1.3/apple-books.json)。限该自制混合样本，不代表 Flow/epub.tools 通过；阅读器顶栏和屏幕页数不是原 PDF 页眉页脚。 |
| Windows 旧任务升级试用 | 已复制旧任务，用冻结 CLI `resume` 触发 v4 缓存失效并实际重跑 OCR；原图读出 2/3，无推算值。原任务全部快照与修订未动，副本人工修订未改且无冲突，后续纯导出不改解析缓存。见 [升级记录](../artifacts/release-0.1.3/windows-cached-export.json)。 |
| Windows 普通用户窗口启动 | 已在活动桌面会话 1 启动日常使用窗口，PID 31960，标题含 0.1.3，窗口响应正常，使用既有本地资源目录；旧 0.1.2 窗口未动。见 [启动记录](../artifacts/release-0.1.3/windows-new-started.json)。 |
| 源码归档与统一摘要 | 源码包在文档定稿后生成，交付时按 `dist/SHA256SUMS.txt` 核验；不在源码包内自引该包自身摘要。两平台应用 ZIP 的已核验摘要见下节。 |
| 无开发环境全新 Windows、其他系统版本/Intel Mac、商业签名/公证 | 仍待验证或未提供，未因本次改动消除。 |

系统阻断针对真实 GUI 及其后台进程，没有用仅修改 Python 网络函数代替。进程采样有时间间隔，短时 worker 可能未被某次采样捕获；Mac 所有子进程继承内核禁网，Windows 规则按两个程序路径生效，实际 worker 列表由 GUI 验收记录补充。

## 发布包与试用

| 文件（位于 `dist/`） | 字节 | SHA-256 |
| --- | ---: | --- |
| `LocalPDFtoEPUB-0.1.3-macos-arm64.zip` | 86,495,583 | `9756faf0d865bc68f8031ff98e44d820a29541277c8e44a755525d83d340165c` |
| `LocalPDFtoEPUB-0.1.3-windows-x64.zip` | 128,792,031 | `c41acaf1c08a764b1e2cdf865cb6fe1df18b07948b0e5ae790b7a5bb4fdcce09` |

上述包包含本版源码构建出的运行时，模型仍通过原有 `LocalPDFtoEPUB-ocr-resources.zip` 导入。源码归档交付名为 `LocalPDFtoEPUB-0.1.3-source.zip`，应用包、源码包、资源包的最终摘要统一记录在 `dist/SHA256SUMS.txt`。

Windows 普通使用窗口已从 `D:\Projects\pdf-to-epub\release-0.1.3\LocalPDFtoEPUB` 启动，使用 `D:\Projects\pdf-to-epub\resources` 的现有模型；根目录 `启动本地书坊.cmd` 已指向 0.1.3，并保留 `启动本地书坊-0.1.2.cmd`。旧安装目录和旧 0.1.2 窗口保留。此次普通窗口启动与上述自动验收窗口分别记录，没有把测试进程冒充日常使用窗口。

Windows 试用任务副本为 `D:\Projects\pdf-to-epub\trial-0.1.3\mixed-corrected.task`，新版文件为 `D:\Projects\pdf-to-epub\source\tests\fixtures\02_mixed_bad_layer-0.1.3.epub`，SHA-256 为 `f084bb1ef8f73a7d75bdce67dee66ee70273f25a2dbd55f623763f4a14207c48`。正文有 4 个章节文件，页边输出省略计数为 8，人工修订保留；4 个章节不意味着阅读器必须只显示 4 屏。原任务和原文件均保留。

本版未更换模型、依赖或第三方二进制，第三方许可清单与既有发布记录保留。平台文档只澄清初版“10 项测试”的历史范围，打包后的平台与许可文件已纳入摘要核验。锁定依赖、离线资源准备及构建步骤仍见主 README 和对应文档；本版验证证据集中在本记录。
