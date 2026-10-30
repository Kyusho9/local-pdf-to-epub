本地兼容阅读资源：Vivliostyle Viewer 2.45.2

这里的 viewer/ 完整保留官方 npm @vivliostyle/viewer@2.45.2 的 lib/ 文件，未修改引擎、UI、字体或脚本。profiles/source-margins.css 是本项目独立 MIT 样式；它不打进 EPUB，只在此阅读器内作为附加样式加载。

入口示例（端口和授权路径由应用服务器决定）：
`http://127.0.0.1:PORT/viewer/#src=/book/&bookMode=true&spread=false&allowScripts=false&style=/profiles/source-margins.css`

本地服务器把 /viewer/ 指向 viewer/，/book/ 指向安全解包的 EPUB，/profiles/ 指向 profiles/。只监听 127.0.0.1；不要把解包目录和项目父目录整体公开，不允许路径越界、远程资源或文档脚本。生产入口应设置只允许本机来源的 CSP。资源准备完后不需要 Node.js、账号、API Key 或网络；普通用户由桌面程序启动服务器与浏览器。阅读器自身需要系统浏览器。

标准 EPUB 使用 data-srcpage、data-srchead、data-srcfoot 三个属性。章节 body 初始化为首段真实来源；段落/标题块在 offset 0 直接设置状态。该处空 pagebreak span 可保留审计属性，但须加 data-margin-anchor="true"，此样式会排除它，避免重复赋值引起页首滞后。段内 offset>0 的空来源 span 正常赋值。样式显示当前重排阅读页起始正文来源的印刷页码、页眉与普通页脚；跨两张原页时不会假称阅读页与原 PDF 一一对应。属性空值用于未知来源、封面、报告，不能省略状态重置。人工确认的推算页码可作为 data-srcpage 显示，其人工来源必须在任务与报告保留，不能称为 OCR。未确认建议不得传入属性。

官方 npm 的 MathJax CDN script 已在原始 index.html 中注释，未开启。不要取消注释；公式使用应用导出的本地图片。所有运行资源和字体均在 viewer/ 内。对外文档链接属于用户主动导航，核心本地阅读不依赖这些链接。

已实际测试：标准 EPUBCheck 5.4.0 零错误/警告；本地 Chromium 中字号 16→24、视口 900×800→650×650 使页数 6→9→11，全部 26 页来源和页边一致；空 inline span、跨 XHTML、封面/未知/报告清空均通过。探针不是生产导出验收，证据在 artifacts/vivliostyle-running-margins/standard-result.json。无头测试使用正常 Chrome UA 来避开上游明确的 Headless 打印纸回退；实际浏览器复核由任务主控另外记录。

许可与源码：Viewer/Core 为 AGPL-3.0；本项目 profile 为 MIT。组件许可原文见 licenses/。Font Awesome 5.15.4 的字体为 OFL-1.1、图标为 CC-BY-4.0、代码为 MIT；Knockout、i18next-ko、i18next 为 MIT，fast-diff 为 Apache-2.0。原版程序保留自身署名；不应把阅读器标为本项目原创或全体 MIT。

匹配发布的完整上游源码、构建配置/锁文件、原始 npm 归档和直接捆绑依赖归档随源码分发放在 ../../sources/vivliostyle-2.45.2/。发布桌面包时应同时交付这些对应源码，并提供本地可找到的入口，不仅提供联网源码链接。若修改 AGPL 阅读器，保留改动说明并一并提供相应源码。本目录只有独立样式新增；官方 Viewer 字节未变。

官方资料：
- https://docs.vivliostyle.org/en/viewer/vivliostyle-viewer/
- https://docs.vivliostyle.org/en/reference/supported-css-features/
- https://github.com/vivliostyle/vivliostyle.js/tree/8f31c04f2dd3a3d992016cec21cf69acf221a821
