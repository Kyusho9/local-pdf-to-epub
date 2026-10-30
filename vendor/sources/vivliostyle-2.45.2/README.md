Vivliostyle 2.45.2 对应源码与原始发布归档

viewer-npm-metadata.json 记录官方 @vivliostyle/viewer@2.45.2 的 gitHead、dist URL、完整性值。原始发布包 SHA-512 已与 npm dist.integrity 实际核对。viewer-2.45.2.tgz、core-2.45.2.tgz 均为原样保存。

vivliostyle.js-8f31c04f2dd3a3d992016cec21cf69acf221a821.tar.gz 是 npm gitHead 对应的完整上游源码快照，含 LICENSE、CONTRIBUTING.md、packages/core、packages/viewer、构建配置与 yarn.lock，不依赖日后联网才能取得源码。

上游源码构建方法：安装 Node.js >=20 和 Yarn Classic，解包源码，执行 yarn install --frozen-lockfile，再执行 yarn build。当前交付验证的是官方发布运行包，未声称已经重建此上游包；构建依赖的完整离线 Yarn 缓存不包含在内（与普通用户离线运行无关）。直接运行官方 Viewer 仅需本机 HTTP 服务器和浏览器。

fast-diff-1.3.0、i18next-ko-3.0.1、i18next-3.5.2、knockout-3.5.3、fontawesome-free-5.15.4 的原始 npm 包随附；版本来自同提交 yarn.lock，直接导入与捆绑代码可在 Viewer source map 检查。完整 Font Awesome 包含字体、图标源文件与许可证，随 Viewer 使用的 woff2 已逐字节确认匹配。

本地 profile 源码为 ../../reader/vivliostyle-2.45.2/profiles/source-margins.css（MIT），不改动 AGPL 引擎。应把本目录与 Reader 程序一起提供给接收者，保留 AGPL、依赖许可证、版权声明、来源说明及任何后续改动记录。
