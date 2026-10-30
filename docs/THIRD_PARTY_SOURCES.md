# 第三方对应源码与库替换（0.1.6）

普通用户运行软件不需要下载或编译这些源码。它们供希望检查、修改或重新链接第三方库的接收者使用。

与两平台应用一起发布的 [LocalPDFtoEPUB-0.1.6-third-party-sources.zip](https://github.com/Kyusho9/local-pdf-to-epub/releases/download/v0.1.6/LocalPDFtoEPUB-0.1.6-third-party-sources.zip) 由本项目提供下载，包含下列精确版本的完整上游源档、许可、构建脚本和来源校验清单。应用自身的完整源码、固定依赖和 PyInstaller spec 在同一 [Release](https://github.com/Kyusho9/local-pdf-to-epub/releases/tag/v0.1.6) 的 `LocalPDFtoEPUB-0.1.6-source.zip` 中。不能只保留本文的上游网址而撤下对应源码附件；转发二进制时也应同时提供相应源码及许可安排。

## 实际包含的组件

| 发布物中的组件 | 对应源档 / 精确版本 | 范围与依据 |
| --- | --- | --- |
| Qt Core、Gui、Widgets、Network；Mac 另有 DBus | `qt/qtbase-everywhere-src-6.12.0.tar.xz` | 两平台实际动态库、平台、样式、TLS 等插件；包含 Qt Base 捆绑的第三方源码 |
| Qt Svg 与 SVG 图像/图标插件 | `qt/qtsvg-everywhere-src-6.12.0.tar.xz` | 两平台所带 Svg 库与插件 |
| 额外图像格式插件 | `qt/qtimageformats-everywhere-src-6.12.0.tar.xz` | ICNS、TGA、TIFF、WBMP、WebP 等；GIF、ICO、JPEG 来自 Qt Base |
| 随包 99 个 Qt 翻译文件 | `qt/qttranslations-everywhere-src-6.12.0.tar.xz` | 保留完整 `.ts` 翻译源和构建配置；另附 `qttools-everywhere-src-6.12.0.tar.xz` 供构建 Linguist/lrelease 工具 |
| PySide6 Essentials / Shiboken6 | `pyside/pyside-setup-everywhere-src-6.12.0.tar.xz` | 同一完整源档包含绑定、生成器、构建脚本及配置；不需要无关 Qt 模块的源码全家桶 |
| GEOS | `geos/geos-3.14.1.tar.bz2` | Mac 库名与 Windows 实际 DLL 字符串均为 GEOS 3.14.1 / CAPI 1.20.5；也覆盖 GPU 附件的同版库 |
| Shapely | `shapely/shapely-2.2.0.tar.gz` | BSD 许可；另行附上，方便把 Python 扩展重新链接到修改后的 GEOS |
| Windows OpenCV FFmpeg DLL | `ffmpeg/` | FFmpeg 4.4.5、libvpx 1.15.0、AOM 3.11.0、OpenH264 1.8.0 API 头文件及完整 OpenCV 固定提交源码与配方；不含 OpenH264 编解码 DLL |

Qt / PySide 源档的 SHA-256 与 Qt 官方下载站提供的校验值逐一匹配；Shapely 与官方 PyPI 元数据匹配。GEOS 从 OSGeo 官方源站取得，记录实测 SHA-256（未声称该站另有发布同值的 SHA-256 文件）。FFmpeg 额外核对了官方 Git blob 与发布 DLL 的对应关系。每项地址、摘要、字节数均在源包 `manifest.json`、`upstream-sources.json` 和各组件的 provenance 文件中，源档不作删改。

本项目未修改这些 Qt / PySide / GEOS / FFmpeg 上游代码，也没有遗漏待应用的私有补丁。Mac 自建的最小 OpenCV wheel 是另一项明确记录的构建：其完整 `opencv-python` 官方源包、构建配置及脚本已经包含在应用源码 ZIP 的 `vendor/` 和 `packaging/`，Mac 不带 FFmpeg。

## 重建 Qt / PySide

在目标平台的独立开发目录操作，并保留原应用备份。需要对应平台的 C/C++ 编译器、系统 SDK、CMake、Ninja、Python 3.12，以及 PySide 生成器所需的 LLVM/libclang。以上是开发工具，不要求普通应用用户安装；具体受支持的编译器与依赖由源档内 README / CMake 配置规定。

先解包 Qt Base，在独立构建目录调用其 `configure`（Windows 为 `configure.bat`）：

```text
/path/to/qtbase-everywhere-src-6.12.0/configure -prefix /path/to/qt-install -opensource -confirm-license -release -shared -nomake tests -nomake examples
cmake --build . --parallel
cmake --install .
```

Windows 使用 x64 开发者终端和 Windows 路径；Mac 使用 arm64 工具链。逐个为 qtsvg、qtimageformats、qttools、qttranslations 建立新的构建目录，使用同一 Qt 的 `qt-configure-module`（Windows 为 `.bat`），再执行 `cmake --build . --parallel` 和 `cmake --install .`。先构建 qttools 的 Linguist 工具再构建翻译；不必启用 QDoc、Designer 或其他不需要的工具。不要把不同架构、不同 Qt 版本的库和插件混在一起。

在 PySide 源目录依照 `README.md` / `README.pyside6.md` 设置 `LLVM_INSTALL_DIR`，然后使用已构建的 Qt：

```text
python setup.py install --qtpaths=/path/to/qt-install/bin/qtpaths6 --ignore-git --module-subset=Core,Gui,Widgets,Network,Svg
```

Mac 需要 DBus 时将 `DBus` 加入同一模块列表。源码包内 `setup.py` 与 `build_scripts/` 是上游原始配方；Windows 的 `qtpaths6.exe` 使用实际安装路径。准备好本地构建依赖后可离线执行，不要让项目固定依赖重新覆盖刚编译的 PySide。

推荐在装有修改版库的独立 Python 环境中运行应用源码，然后按项目 `docs/DEVELOPMENT.md` 使用 `python scripts/build.py` 重建发布目录。若直接替换，Windows 的 Qt DLL 在 `_internal/PySide6/`，插件在其插件子目录，PySide / Shiboken 扩展也须使用兼容版本；Mac 在 `.app/Contents/Frameworks/`，资源与 framework 符号链接须一起保持有效。Mac 修改库会使原签名失效，使用项目构建脚本重新生成 ad-hoc 签名；不需要关闭系统安全功能。

## 重建 GEOS / 重新链接 Shapely

GEOS 完整源档含 `CMakeLists.txt`、`src/`、`capi/`、头文件及 LGPL 许可。常规共享库构建示例：

```text
cmake -S /path/to/geos-3.14.1 -B geos-build -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=ON -DCMAKE_INSTALL_PREFIX=/path/to/geos-install
cmake --build geos-build --config Release --parallel
cmake --install geos-build --config Release
```

再依据 Shapely 源包 `README.rst` / `docs/installation.rst`，在其源目录用 `python -m pip wheel --no-deps --no-build-isolation . -Csetup-args="-Dcmake_prefix_path=/path/to/geos-install"` 构建 Shapely 2.2.0 wheel 并装入独立环境。构建前须备好 `pyproject.toml` 列出的 Cython、meson-python、NumPy 等本地依赖；Windows 运行时可用 `GEOS_LIBDIR` 指定 GEOS DLL 目录。2.2 已不支持旧的 `GEOS_INCLUDE_PATH` / `GEOS_LIBRARY_PATH` 发现方式。先确认 `shapely.geos_version_string` 和实际加载的库路径，再重建应用。这样可以重新链接改变过 ABI 的库；不能仅改一个 DLL 的文件名就认为链接兼容。

原应用中，Windows 共享库位于 `_internal/Shapely.libs/`，文件名含 wheel 生成的摘要；Mac 为 `Contents/Frameworks/libgeos.3.14.1.dylib` 与 `libgeos_c.1.20.5.dylib` 及其对应链接。兼容 ABI 的替换须同时满足原库名、架构、运行时和所有依赖；重建 Shapely/应用可避免手工处理 wheel 的依赖重命名。

GPU 附件中的 GEOS 位于 `packages/Shapely.libs/`。原版应用会校验整个可执行附件的摘要，因此手工更改原运行包会被拒绝。自建版本可在完整公开源码中更新 `src/local_epub/{directml,gpu}_runtime_catalog.json` 的自建 wheel 来源、大小、SHA-256、成员映射，同时更新对应 `*_runtime_manifest.json` 的文件大小、SHA-256，再用 `scripts/build-gpu-runtime.py --catalog ... --manifest ... --cache ... --output ... --zip ...` 生成自建附件，并重新构建使用同一清单的应用。这不需要项目方的私钥或在线授权；应保留第三方许可和修改说明。只修改 ZIP 内的 manifest 不能改变原版应用的受信任清单。

## Windows FFmpeg

实际 DLL 的 SHA-256 为 `f0460320ac08c074bdf6d5a19f5c13d204e585e85030fdd40e20a3c0b3adeb67`。OpenCV-python 4.11.0.86 的 CMake 指向官方二进制提交 `d63d7c154c57242bf2283be61166be2bd30ec47e`；官方对应源码提交 `02a08baa7b8d330364c35477ba50524db8e4d335` 的父提交正是它。wrapper 采用 OpenCV `09892c9d1706f40342bda0bc404580f63492d9f8`，完整源码也已附上，未用新版 FFmpeg 冒充对应源码。

按源包 `ffmpeg/README-corresponding-sources.md` 执行其 `prepare_sources.py`，再使用所附 Ubuntu/MinGW Docker 配方。源码展开与 wrapper 逐文件一致性已实测；编译容器可禁用网络。新 DLL 在保持 Windows x64 / OpenCV 4.11 videoio ABI 后，可替换 `_internal/cv2/opencv_videoio_ffmpeg4110_64.dll`，应用不为该 DLL 设置额外替换限制。上游未固定 Docker 基础镜像和 apt 包摘要，因此不承诺逐字节重现二进制。

## 验证范围与许可

本次实际完成：核对发布物所用版本、下载对应完整源码、校验源档、检查构建配置、验证 FFmpeg 精确提交链和源码展开、生成并逐项校验源 ZIP。没有重新编译 Qt、PySide、GEOS 或 FFmpeg，也没有把源码交付验证写成修改版库的运行验收；自行修改后仍应在目标系统验证 GUI、OCR、加载库和导出。

Vivliostyle 2.45.2 的完整 AGPL 对应源码已经在主应用 `reader-sources/` 和应用源码 ZIP 的 `vendor/sources/` 中。各上游许可按源档原样保留；本项目 MIT 不覆盖它们。用户可依据相应许可修改、替换、重新链接库，并为调试库修改进行必要的逆向工程，本应用不添加相反限制。完整组件许可见 [第三方清单](THIRD_PARTY.md)。

维护者重新打包本源码附件可用 `python packaging/build_third_party_sources.py --source-root <已准备源码目录> --output dist/LocalPDFtoEPUB-0.1.6-third-party-sources.zip`。脚本只收录固定清单中的文件并校验 SHA-256，不会扫入开发缓存或用户文档，也不自动联网下载。
