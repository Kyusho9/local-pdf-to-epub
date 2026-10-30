# Build on the target OS: PyInstaller does not cross-compile Windows from macOS.
from pathlib import Path
import sys
import tomllib
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules, copy_metadata

root = Path(SPECPATH).parent
version = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
datas = []
reader = root / 'vendor/reader/vivliostyle-2.45.2'
reader_sources = root / 'vendor/sources/vivliostyle-2.45.2'
if not (reader / 'manifest.json').is_file() or not (reader_sources / 'manifest.json').is_file():
    raise RuntimeError('发布需要本地兼容阅读器及其完整对应源码；不会自动下载。')
datas += [(str(reader), 'reader/vivliostyle-2.45.2'),
          (str(reader_sources), 'reader-sources/vivliostyle-2.45.2')]
datas += collect_data_files("local_epub", includes=["*.json", "model_licenses/*"])
# The optional embedded CUDA Python runs this standalone entry point. It never
# imports or replaces the CPU ONNX Runtime frozen into the main application.
datas += [(str(root / "src/local_epub/gpu_worker.py"), ".")]
datas += collect_data_files("cv2", includes=["LOCAL_BUILD.json", "LOCAL_LICENSES.txt"])
for package in ("rapidocr_onnxruntime", "pypdfium2", "pypdfium2_raw", "pdfminer"):
    # Models are installed/imported as a separately checksummed offline pack.
    datas += collect_data_files(package, excludes=["**/*.onnx", "*.onnx"])
for distribution in ("rapidocr-onnxruntime", "onnxruntime", "pypdfium2", "pdfplumber", "pdfminer.six", "pypdf", "numpy", "Pillow", "opencv-python"):
    datas += copy_metadata(distribution)
if (root / "packaging" / "licenses").is_dir():
    datas += [(str(root / "packaging" / "licenses"), "licenses")]
for document in ("THIRD_PARTY.md", "THIRD_PARTY_SOURCES.md", "PLATFORMS.md"):
    if (root / "docs" / document).exists():
        datas += [(str(root / "docs" / document), "docs")]

hiddenimports = collect_submodules("local_epub")
hiddenimports += collect_submodules("rapidocr_onnxruntime")
hiddenimports += ["onnxruntime.capi.onnxruntime_pybind11_state", "PIL._tkinter_finder"]
hiddenimports = [name for name in hiddenimports if name != "PIL._tkinter_finder"]
binaries = collect_dynamic_libs("onnxruntime")

a = Analysis(
    [str(root / "packaging" / "entry.py")],
    pathex=[str(root / "src")],
    binaries=binaries, datas=datas, hiddenimports=hiddenimports,
    hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "IPython", "pytest", "reportlab", "PyQt5", "PyQt6"],
    noarchive=False,
)
pyz = PYZ(a.pure)
gui = EXE(
    pyz, a.scripts, [], exclude_binaries=True, name="LocalPDFtoEPUB",
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    console=False, disable_windowed_traceback=False,
    argv_emulation=False, target_arch=None, codesign_identity=None, entitlements_file=None,
)
cli = EXE(
    pyz, a.scripts, [], exclude_binaries=True, name="LocalPDFtoEPUB-CLI",
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    console=True, argv_emulation=False, target_arch=None,
    codesign_identity=None, entitlements_file=None,
)
coll = COLLECT(gui, cli, a.binaries, a.datas, strip=False, upx=False, name="LocalPDFtoEPUB")
if sys.platform == "darwin":
    app = BUNDLE(
        coll, name="LocalPDFtoEPUB.app", icon=None,
        bundle_identifier="org.localpdf.epub",
        info_plist={
            "CFBundleDisplayName": "本地 PDF 转 EPUB",
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "14.0",
            "CFBundleShortVersionString": version,
            "CFBundleVersion": version,
            "NSHumanReadableCopyright": "See bundled third-party notices.",
        },
    )
