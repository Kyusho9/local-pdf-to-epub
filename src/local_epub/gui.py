"""Local EPUB desktop UI. OCR and export always run in a child process."""
from __future__ import annotations

import html
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from datetime import datetime

from PySide6.QtCore import Qt, QProcess, QProcessEnvironment, QThread, Signal, Slot, QUrl, QTimer
from PySide6.QtGui import QDesktopServices, QPixmap, QFont
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QLineEdit, QPushButton, QFileDialog, QComboBox, QDoubleSpinBox,
    QSpinBox, QProgressBar, QPlainTextEdit, QTextBrowser, QTabWidget,
    QListWidget, QListWidgetItem, QSplitter, QCheckBox, QMessageBox, QGroupBox,
    QScrollArea, QStackedWidget, QAbstractItemView, QDialog, QDialogButtonBox,
)

from . import __version__


KINDS = {"paragraph": "正文段落", "heading": "章节标题", "footnote": "脚注", "image": "图片", "table": "表格"}
DECORATIONS = {"header": "页眉", "footer": "页脚", "page_number": "页码"}
PROJECT_URL = "https://github.com/Kyusho9/local-pdf-to-epub"
# Set only after the release owner verifies the published repository. Opening
# the About dialog must never probe the network or offer an unverified link.
PROJECT_URL_VERIFIED = True
_RENDER_LOCK = threading.Lock()
STATES = {"pending": "未处理", "processing": "处理中", "success": "完成", "failed": "失败", "excluded": "主动排除", "cancelled": "已取消", "complete": "完成", "partial": "部分完成", "incomplete": "未完整完成", "running": "正在运行"}


class CallThread(QThread):
    completed = Signal(object)
    failed = Signal(str)
    progress = Signal(str)

    def __init__(self, function, parent=None, with_progress=False):
        super().__init__(parent)
        self.function = function
        self.with_progress = with_progress

    def run(self):
        try:
            self.completed.emit(self.function(self.progress.emit) if self.with_progress else self.function())
        except Exception as exc:
            self.failed.emit(str(exc))


class LocalBrowser(QTextBrowser):
    """The preview cannot navigate to an Internet URL."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setOpenExternalLinks(False)
        self.setOpenLinks(False)
        self.anchorClicked.connect(self._anchor)

    def _anchor(self, url):
        if not url.scheme() and url.fragment():
            self.scrollToAnchor(url.fragment())

    def loadResource(self, resource_type, url):
        if url.scheme() not in ("", "file", "qrc", "data"):
            return None
        return super().loadResource(resource_type, url)


class Window(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"本地书坊 · PDF 转 EPUB · {__version__}")
        self.resize(1220, 840)
        self.setMinimumSize(960, 680)
        self.setAcceptDrops(True)
        self.job_dir: Path | None = None
        self._loaded_job_root: Path | None = None
        self._preview_html = None
        self._original_display_path = None
        self.document = {}
        self.selected_block = None
        self._loading_editor = False
        self._dirty = False
        self._stdout = b""
        self._stderr = ""
        self._password_file: Path | None = None
        self._threads = []
        self._render_generation = 0
        self._running_action = ""
        self._last_output: Path | None = None
        self._last_report: Path | None = None
        self._last_format = {}
        self._last_error = ""
        self._last_status = ""
        self._last_event = {}
        self._reader_opening = False
        self._reader_result = None
        self._worker_busy = False
        self._resource_busy = False
        self._gpu_state = None
        self._last_device_event = {}
        self._device_events = []
        self._about_dialog = None
        self.process = QProcess(self)
        self.process.readyReadStandardOutput.connect(self._read_stdout)
        self.process.readyReadStandardError.connect(self._read_stderr)
        self.process.finished.connect(self._finished)
        self.process.errorOccurred.connect(self._process_error)
        self._build_ui()
        self._style()
        self.retain_page_margins_check.toggled.connect(self._update_preview)
        self.check_resources()

    def show_about(self):
        if self._about_dialog is not None:
            self._about_dialog.raise_()
            self._about_dialog.activateWindow()
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("关于本地书坊")
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.setMinimumWidth(430)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(14)
        title = QLabel(f"本地书坊 · PDF 转 EPUB\n版本 {__version__}")
        font = title.font()
        font.setPointSize(17)
        font.setBold(True)
        title.setFont(font)
        layout.addWidget(title)
        for value in (
            "默认离线。PDF、正文、截图和识别结果均在本机处理，不上传文档，也不使用遥测。转换不会自动下载模型。",
            "应用代码与独立阅读样式采用 MIT 许可。原版 Vivliostyle Viewer/Core 采用 AGPL-3.0，完整对应源码随包提供；Qt、PDFium 和模型等许可详见随附第三方清单。",
            "只有主动下载资源，或点击下方项目按钮打开浏览器时才会访问互联网。",
        ):
            label = QLabel(value)
            label.setWordWrap(True)
            layout.addWidget(label)
        address = QLabel(PROJECT_URL + ("" if PROJECT_URL_VERIFIED else "\n项目地址待核验，当前未启用打开链接。"))
        address.setWordWrap(True)
        address.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(address)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.button(QDialogButtonBox.Close).setText("关闭")
        project = buttons.addButton("打开开源项目", QDialogButtonBox.ActionRole)
        project.setEnabled(PROJECT_URL_VERIFIED)
        project.setToolTip("由系统浏览器打开项目地址；此操作会联网。")
        project.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(PROJECT_URL)))
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        self._about_dialog = dialog
        dialog.finished.connect(lambda _result: setattr(self, "_about_dialog", None))
        dialog.open()

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(28, 20, 28, 22)
        outer.setSpacing(14)
        top = QHBoxLayout()
        brand = QLabel("本地书坊")
        brand.setObjectName("brand")
        top.addWidget(brand)
        subtitle = QLabel("PDF → 可重排 EPUB")
        subtitle.setObjectName("subtitle")
        top.addWidget(subtitle)
        top.addStretch()
        badge = QLabel("●  默认离线 · 本地识别")
        badge.setObjectName("badge")
        top.addWidget(badge)
        self.about_button = QPushButton("关于")
        self.about_button.setToolTip("版本、离线说明与开源许可")
        self.about_button.clicked.connect(self.show_about)
        top.addWidget(self.about_button)
        outer.addLayout(top)
        self.tabs = QTabWidget()
        outer.addWidget(self.tabs, 1)
        self._build_convert()
        self._build_review()
        self._build_settings()
        progress_box = QGroupBox("任务状态")
        progress_layout = QVBoxLayout(progress_box)
        row = QHBoxLayout()
        self.status_label = QLabel("选择 PDF，检查资源后即可开始。支持把 PDF 拖入窗口。")
        self.status_label.setWordWrap(True)
        row.addWidget(self.status_label, 1)
        # Keep the main action visible even on smaller screens; the form above
        # may scroll, while task actions stay in a fixed footer.
        row.addWidget(self.start_button)
        self.tabs.currentChanged.connect(lambda index: self.start_button.setVisible(index == 0))
        self.cancel_button = QPushButton("取消任务")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_task)
        row.addWidget(self.cancel_button)
        self.open_result_button = QPushButton("打开 EPUB")
        self.open_result_button.setEnabled(False)
        self.open_result_button.clicked.connect(self.open_result)
        row.addWidget(self.open_result_button)
        self.compatible_reader_button = QPushButton("兼容阅读器打开")
        self.compatible_reader_button.setEnabled(False)
        self.compatible_reader_button.setToolTip("在本机浏览器打开内置 Vivliostyle 阅读器；无需联网，阅读时请保持本软件运行。")
        self.compatible_reader_button.clicked.connect(self.open_compatible_reader)
        row.addWidget(self.compatible_reader_button)
        progress_layout.addLayout(row)
        self.actual_device_label = QLabel("实际设备：尚未识别。Windows 新任务默认 DirectML；Mac 默认 CPU，NVIDIA 用户可在 Windows 选择 CUDA。")
        self.actual_device_label.setWordWrap(True)
        self.actual_device_label.setToolTip("GPU OCR 仍可能包含 CPU 辅助算子；PDF 解析、排版和 EPUB 导出在 CPU 运行。只有完成页面的实际执行证据才计为 GPU OCR。")
        progress_layout.addWidget(self.actual_device_label)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        progress_layout.addWidget(self.progress_bar)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(82)
        self.log.setPlaceholderText("阶段、页数和真实错误会显示在这里。")
        progress_layout.addWidget(self.log)
        outer.addWidget(progress_box)

    def _build_convert(self):
        page = QWidget()
        page.setObjectName("conversionPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(22, 22, 22, 18)
        layout.setSpacing(12)
        intro = QLabel("把书放进来，带着结构读下去。")
        intro.setObjectName("intro")
        layout.addWidget(intro)
        note = QLabel("文字页优先提取，扫描页在本机识别。转换不会上传 PDF，也不会自动下载模型。")
        note.setWordWrap(True)
        layout.addWidget(note)
        source_box = QGroupBox("1  选择 PDF")
        form = QFormLayout(source_box)
        self.source_edit = QLineEdit()
        self.source_edit.setPlaceholderText("拖入 PDF，或点击右侧选择文件")
        choose = QPushButton("选择 PDF…")
        choose.clicked.connect(self.choose_pdf)
        form.addRow("输入文件", self._row(self.source_edit, choose))
        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.Password)
        self.password_edit.setPlaceholderText("仅加密 PDF 需要；不会写入任务文件")
        form.addRow("文档密码", self.password_edit)
        layout.addWidget(source_box)
        result_box = QGroupBox("2  输出与排版")
        form = QFormLayout(result_box)
        self.output_edit = QLineEdit()
        choose_output = QPushButton("另存为…")
        choose_output.clicked.connect(self.choose_output)
        form.addRow("EPUB 文件", self._row(self.output_edit, choose_output))
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("留空时使用 PDF 元数据或文件名")
        form.addRow("书名", self.title_edit)
        self.author_edit = QLineEdit()
        self.author_edit.setPlaceholderText("可选")
        form.addRow("作者", self.author_edit)
        style = QHBoxLayout()
        self.font_size = QDoubleSpinBox()
        self.font_size.setRange(0.7, 2.0)
        self.font_size.setSingleStep(0.1)
        self.font_size.setValue(1.0)
        self.font_size.setSuffix(" em")
        self.line_height = QDoubleSpinBox()
        self.line_height.setRange(1.1, 2.5)
        self.line_height.setSingleStep(0.1)
        self.line_height.setValue(1.8)
        style.addWidget(QLabel("初始字号"))
        style.addWidget(self.font_size)
        style.addSpacing(24)
        style.addWidget(QLabel("行距"))
        style.addWidget(self.line_height)
        style.addStretch()
        style.addWidget(QLabel("阅读器中仍可自由调整"))
        form.addRow("基本样式", style)
        self.retain_page_margins_check = QCheckBox("保留页眉、页脚和原页码（兼容阅读器）")
        self.retain_page_margins_check.setChecked(False)
        self.retain_page_margins_check.setToolTip(
            "Vivliostyle 兼容阅读器支持真实页边显示；Flow 和 Apple Books 未支持。"
            "每个阅读页采用起始来源页的页眉、页脚，页脚显示该起始来源的原页码。"
            "正文仍可调字号重排，不承诺与原 PDF 分页相同；不插入正文，也不另建附录。"
        )
        form.addRow("页边内容", self.retain_page_margins_check)
        margin_notice = QLabel("勾选后请用「兼容阅读器打开」查看页边；不勾选时，已识别页眉、页脚和原页码不进入 EPUB。")
        margin_notice.setWordWrap(True)
        form.addRow("", margin_notice)
        layout.addWidget(result_box)
        row = QHBoxLayout()
        self.resource_summary = QLabel("正在检查本地资源…")
        self.resource_summary.setWordWrap(True)
        row.addWidget(self.resource_summary, 1)
        settings = QPushButton("资源与高级选项")
        settings.clicked.connect(lambda: self.tabs.setCurrentIndex(2))
        row.addWidget(settings)
        layout.addLayout(row)
        actions = QHBoxLayout()
        reopen = QPushButton("重新打开任务…")
        reopen.clicked.connect(self.choose_job)
        actions.addWidget(reopen)
        actions.addStretch()
        self.start_button = QPushButton("开始转换")
        self.start_button.setObjectName("primary")
        self.start_button.setMinimumWidth(180)
        self.start_button.clicked.connect(self.start_conversion)
        actions.addWidget(self.start_button)
        layout.addLayout(actions)
        layout.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidget(page)
        self.tabs.addTab(scroll, "①  转换")

    def _build_review(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 16, 16, 16)
        row = QHBoxLayout()
        self.job_label = QLabel("转换后可逐块校对，也可以重新打开已保存的任务。")
        self.job_label.setWordWrap(True)
        row.addWidget(self.job_label, 1)
        self.resume_button = QPushButton("继续未完成任务")
        self.resume_button.clicked.connect(self.resume_job)
        self.resume_button.setEnabled(False)
        row.addWidget(self.resume_button)
        layout.addLayout(row)
        split = QSplitter(Qt.Horizontal)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 6, 0)
        self.page_combo = QComboBox()
        self.page_combo.currentIndexChanged.connect(self._filter_blocks)
        left_layout.addWidget(self.page_combo)
        self.block_list = QListWidget()
        self.block_list.setMinimumWidth(210)
        self.block_list.setWordWrap(True)
        self.block_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.block_list.currentItemChanged.connect(self._select_block)
        left_layout.addWidget(self.block_list, 1)
        self.retry_button = QPushButton("重新识别当前页")
        self.retry_button.clicked.connect(self.retry_page)
        self.retry_button.setEnabled(False)
        left_layout.addWidget(self.retry_button)
        self.rebuild_layout_button = QPushButton("重新整理版面（复用识别）")
        self.rebuild_layout_button.setToolTip("复用已有 OCR/文字行，不重新识别、不加载模型。处理上方所选页；选择「全部页面」则整理已有成功识别结果及上次整理失败的页面。先保存人工修订，原稿保留；缺失识别结果不能补造，无法匹配的修订会列为冲突供回查。")
        self.rebuild_layout_button.clicked.connect(self.rebuild_layout_current)
        self.rebuild_layout_button.setEnabled(False)
        left_layout.addWidget(self.rebuild_layout_button)
        self.exclude_button = QPushButton("主动排除选中页")
        self.exclude_button.clicked.connect(self.exclude_page)
        self.exclude_button.setEnabled(False)
        left_layout.addWidget(self.exclude_button)
        split.addWidget(left)
        mid = QWidget()
        mid_layout = QVBoxLayout(mid)
        mid_layout.setContentsMargins(6, 0, 6, 0)
        self.source_label = QLabel("来源页码与区域将在此显示。")
        self.source_label.setWordWrap(True)
        self.source_label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.source_label.setStyleSheet("background: #ffffff;")
        source_scroll = QScrollArea()
        source_scroll.setWidgetResizable(True)
        source_scroll.setFrameShape(QScrollArea.NoFrame)
        source_scroll.viewport().setStyleSheet("background: #ffffff;")
        source_scroll.setMinimumHeight(84)
        source_scroll.setMaximumHeight(132)
        source_scroll.setWidget(self.source_label)
        mid_layout.addWidget(source_scroll)
        kind_row = QHBoxLayout()
        self.kind_combo = QComboBox()
        for key, label in KINDS.items():
            self.kind_combo.addItem(label, key)
            if key in ("image", "table"):
                self.kind_combo.model().item(self.kind_combo.count() - 1).setEnabled(False)
        self.kind_combo.currentIndexChanged.connect(self._mark_dirty)
        kind_row.addWidget(self.kind_combo)
        kind_row.addWidget(QLabel("标题级别"))
        self.heading_level = QSpinBox()
        self.heading_level.setRange(1, 6)
        self.heading_level.valueChanged.connect(self._mark_dirty)
        kind_row.addWidget(self.heading_level)
        mid_layout.addLayout(kind_row)
        self.editor = QPlainTextEdit()
        self.editor.setMinimumHeight(120)
        self.editor.setPlaceholderText("选择左侧内容块。在这里修改文字，随后保存修订。")
        self.editor.textChanged.connect(self._mark_dirty)
        mid_layout.addWidget(self.editor, 1)
        self.save_button = QPushButton("保存人工修订")
        self.save_button.setEnabled(False)
        self.save_button.clicked.connect(self.save_current_edit)
        edit_buttons = QHBoxLayout()
        edit_buttons.addWidget(self.save_button)
        self.restore_edit_button = QPushButton("载入本块保留的修订")
        self.restore_edit_button.setEnabled(False)
        self.restore_edit_button.clicked.connect(self.restore_conflicting_edit)
        edit_buttons.addWidget(self.restore_edit_button)
        mid_layout.addLayout(edit_buttons)
        self.merge_button = QPushButton("与紧接的下一正文段落合并")
        self.merge_button.setToolTip("保留两段文字及全部来源页；可跨过已标记的页边内容但不会删除它们。遇到章节、图片或脚注则停止。")
        self.merge_button.setEnabled(False)
        self.merge_button.clicked.connect(self.merge_current)
        mid_layout.addWidget(self.merge_button)
        self.restore_body_button = QPushButton("恢复此块正文")
        self.restore_body_button.setToolTip("仅撤销旧任务对这个普通正文块的人工排除；文字修订不变。")
        self.restore_body_button.setVisible(False)
        self.restore_body_button.clicked.connect(self.restore_legacy_body)
        mid_layout.addWidget(self.restore_body_button)
        split.addWidget(mid)
        right_tabs = QTabWidget()
        self.review_detail_tabs = right_tabs
        self.original_scroll = QScrollArea()
        self.original_scroll.setWidgetResizable(True)
        self.original_label = QLabel("选择内容块后显示原页")
        self.original_label.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        self.original_label.setMinimumWidth(240)
        self.original_scroll.setWidget(self.original_label)
        right_tabs.addTab(self.original_scroll, "原页回查")
        self.preview = LocalBrowser()
        preview_panel = QWidget()
        preview_layout = QVBoxLayout(preview_panel)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        preview_notice = QLabel("这里只预览重排正文，不显示阅读器的真实页边。页眉、页脚和原页码请在「兼容阅读器打开」中查看。")
        preview_notice.setWordWrap(True)
        preview_layout.addWidget(preview_notice)
        preview_layout.addWidget(self.preview, 1)
        right_tabs.addTab(preview_panel, "重排预览")
        self.issues_view = QPlainTextEdit()
        self.issues_view.setReadOnly(True)
        right_tabs.addTab(self.issues_view, "疑点与缺页")
        number_panel = QWidget()
        number_layout = QVBoxLayout(number_panel)
        self.page_number_choice_combo = QComboBox()
        self.page_number_choice_combo.currentIndexChanged.connect(self._page_number_chosen)
        number_layout.addWidget(self.page_number_choice_combo)
        number_notice = QLabel("原书可能故意无页码；只有主动确认才保存为人工推算（非 OCR）。勾选保留页眉、页脚和原页码后，确认值可在兼容阅读器的页脚显示；不插入正文。")
        number_notice.setWordWrap(True)
        number_layout.addWidget(number_notice)
        self.page_number_detail = QPlainTextEdit()
        self.page_number_detail.setReadOnly(True)
        number_layout.addWidget(self.page_number_detail, 1)
        self.inspect_page_number_button = QPushButton("查看候选页原图")
        self.inspect_page_number_button.clicked.connect(self.inspect_page_number)
        number_layout.addWidget(self.inspect_page_number_button)
        self.confirm_page_number_button = QPushButton("确认人工补写")
        self.confirm_page_number_button.clicked.connect(self.confirm_page_number)
        number_layout.addWidget(self.confirm_page_number_button)
        self.clear_page_number_button = QPushButton("撤销人工补写")
        self.clear_page_number_button.clicked.connect(self.clear_page_number)
        number_layout.addWidget(self.clear_page_number_button)
        self.page_number_tab_index = right_tabs.addTab(number_panel, "页码补写")
        right_tabs.setTabVisible(self.page_number_tab_index, False)
        split.addWidget(right_tabs)
        split.setSizes([220, 380, 430])
        layout.addWidget(split, 1)
        footer = QHBoxLayout()
        self.partial_check = QCheckBox("明确允许导出部分结果（附缺页说明）")
        footer.addWidget(self.partial_check)
        footer.addStretch()
        self.report_button = QPushButton("打开质量报告")
        self.report_button.clicked.connect(self.open_report)
        self.report_button.setEnabled(False)
        footer.addWidget(self.report_button)
        self.export_button = QPushButton("保存并重新导出")
        self.export_button.setObjectName("primary")
        self.export_button.clicked.connect(self.export_current)
        self.export_button.setEnabled(False)
        footer.addWidget(self.export_button)
        layout.addLayout(footer)
        self.tabs.addTab(page, "②  校对与导出")

    def _build_settings(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(22, 22, 22, 22)
        box = QGroupBox("离线资源")
        form = QFormLayout(box)
        from .resources import default_resource_dir
        self.resources_edit = QLineEdit(str(default_resource_dir()))
        self.resources_browse = QPushButton("选择目录…")
        self.resources_browse.clicked.connect(self.choose_resources)
        form.addRow("资源目录", self._row(self.resources_edit, self.resources_browse))
        self.resources_detail = QPlainTextEdit()
        self.resources_detail.setReadOnly(True)
        self.resources_detail.setMaximumHeight(160)
        form.addRow("检查结果", self.resources_detail)
        buttons = QHBoxLayout()
        self.check_button = QPushButton("检查资源")
        self.check_button.clicked.connect(self.check_resources)
        self.import_button = QPushButton("导入本地资源包…")
        self.import_button.clicked.connect(self.import_resources)
        self.download_button = QPushButton("主动联网下载资源")
        self.download_button.clicked.connect(self.download_resources)
        buttons.addWidget(self.check_button)
        buttons.addWidget(self.import_button)
        buttons.addWidget(self.download_button)
        form.addRow(buttons)
        info = QLabel("下载仅在点击此按钮后发生，用于准备公开模型；不会发送文档内容。\n导入离线资源包只访问本地文件。转换期间不会联网补齐任何缺项。")
        info.setWordWrap(True)
        form.addRow(info)
        layout.addWidget(box)
        options = QGroupBox("解析设置")
        form = QFormLayout(options)
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("自动判断：可信文字优先，其余使用 OCR", "auto")
        self.mode_combo.addItem("优先文字层（仍检查损坏文字）", "text")
        self.mode_combo.addItem("强制 OCR：每页重新识别", "ocr")
        form.addRow("识别方式", self.mode_combo)
        self.dpi_spin = QSpinBox()
        self.dpi_spin.setRange(120, 400)
        self.dpi_spin.setValue(180)
        self.dpi_spin.setSuffix(" DPI")
        form.addRow("OCR 渲染分辨率", self.dpi_spin)
        self.language_edit = QLineEdit("zh-CN")
        form.addRow("EPUB 语言", self.language_edit)
        self.epubcheck_edit = QLineEdit()
        self.epubcheck_edit.setPlaceholderText("可选：EPUBCheck JAR 文件，须有本地 Java")
        form.addRow("EPUBCheck", self.epubcheck_edit)
        self.ocr_device_combo = QComboBox()
        self.ocr_device_combo.addItem("CPU（Mac 默认；不需要 GPU 运行包）", "cpu")
        self.ocr_device_combo.addItem("DirectML（Windows 默认；需要 DirectML 运行包）", "directml")
        self.ocr_device_combo.addItem("NVIDIA CUDA（Windows 可选高性能后端）", "cuda")
        if sys.platform != "win32":
            self.ocr_device_combo.model().item(1).setEnabled(False)
            self.ocr_device_combo.model().item(2).setEnabled(False)
        else:
            self.ocr_device_combo.setCurrentIndex(1)
        self.ocr_device_combo.currentIndexChanged.connect(self._device_selection_changed)
        form.addRow("OCR 设备", self.ocr_device_combo)
        self.gpu_fallback_check = QCheckBox("GPU 不可用或运行失败时，整页重新使用 CPU")
        self.gpu_fallback_check.setChecked(True)
        self.gpu_fallback_check.setEnabled(sys.platform == "win32")
        self.gpu_fallback_check.setToolTip("取消后为严格 GPU 模式：需 OCR 的页面会明确失败，原生文字页仍可提取。不会把 CPU 结果标为 GPU。")
        form.addRow("故障处理", self.gpu_fallback_check)
        device_note = QLabel("统一使用本地 ONNX Runtime。Windows 新任务默认 DirectML，可选 CPU 或 NVIDIA CUDA；Mac 使用 CPU。GPU 缺少组件时按设置整页回退 CPU，转换不会自动下载。GPU 仍可能包含 CPU 辅助算子；实际执行设备与回退原因显示在任务状态。")
        device_note.setWordWrap(True)
        form.addRow(device_note)
        layout.addWidget(options)
        gpu = QGroupBox("GPU 运行包 · 高级设置")
        form = QFormLayout(gpu)
        from .gpu_resources import default_gpu_runtime_dir
        self.gpu_backend_combo = QComboBox()
        self.gpu_backend_combo.addItem("DirectML（Windows 默认）", "directml")
        self.gpu_backend_combo.addItem("NVIDIA CUDA（可选）", "cuda")
        form.addRow("准备的运行包", self.gpu_backend_combo)
        self._gpu_resource_backend = "directml"
        self._gpu_runtime_paths = {backend: str(default_gpu_runtime_dir(backend=backend)) for backend in ("directml", "cuda")}
        self.gpu_runtime_edit = QLineEdit(self._gpu_runtime_paths["directml"])
        self.gpu_backend_combo.currentIndexChanged.connect(self._gpu_backend_changed)
        self.gpu_browse_button = QPushButton("选择目录…")
        self.gpu_browse_button.clicked.connect(self.choose_gpu_runtime)
        form.addRow("运行包目录", self._row(self.gpu_runtime_edit, self.gpu_browse_button))
        self.gpu_summary = QLabel(self._gpu_runtime_description())
        self.gpu_summary.setWordWrap(True)
        form.addRow(self.gpu_summary)
        self.gpu_detail = QPlainTextEdit()
        self.gpu_detail.setReadOnly(True)
        self.gpu_detail.setMaximumHeight(120)
        self.gpu_detail.setPlainText("未检查 DirectML 运行包；CPU 不需要 GPU 包。检查只验证文件完整性，实际 GPU 能否执行由转换时确认。")
        form.addRow("检查结果", self.gpu_detail)
        self.gpu_check_button = QPushButton("检查运行包")
        self.gpu_check_button.clicked.connect(self.check_gpu_resources)
        self.gpu_import_button = QPushButton("导入本地包…")
        self.gpu_import_button.clicked.connect(self.import_gpu_resources)
        self.gpu_download_button = QPushButton("主动联网下载")
        self.gpu_download_button.clicked.connect(self.download_gpu_resources)
        self.gpu_export_button = QPushButton("导出离线包…")
        self.gpu_export_button.clicked.connect(self.export_gpu_resources)
        form.addRow(self._row(self.gpu_check_button, self.gpu_import_button, self.gpu_download_button, self.gpu_export_button))
        layout.addWidget(gpu)
        limits = QLabel("支持程度：横排文字、中英混排、扫描页、基本目录与插图。\n双栏、脚注、复杂表格和公式请结合原页校对；格式校验通过不代表内容无误。")
        limits.setWordWrap(True)
        layout.addWidget(limits)
        layout.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidget(page)
        self.tabs.addTab(scroll, "③  资源与设置")

    @staticmethod
    def _row(*widgets):
        row = QHBoxLayout()
        for widget in widgets:
            row.addWidget(widget)
        return row

    def _style(self):
        self.setStyleSheet("""
            QMainWindow, QWidget { color: #223532; font-size: 13px; }
            QMainWindow { background: #f3f5f2; }
            QWidget#conversionPage { background: #ffffff; }
            QLabel#brand { font-size: 27px; font-weight: 700; }
            QLabel#subtitle { color: #6b7e75; margin-left: 12px; }
            QLabel#badge { color: #24775e; background: #e4f1e9; padding: 8px 14px; border-radius: 14px; }
            QLabel#intro { font-size: 24px; font-weight: 600; }
            QTabWidget::pane { border: 1px solid #d9e1db; border-radius: 8px; background: #ffffff; }
            QTabBar::tab { padding: 11px 24px; background: #e9eee9; margin-right: 3px; border-top-left-radius: 6px; border-top-right-radius: 6px; }
            QTabBar::tab:selected { background: #ffffff; color: #176549; font-weight: 600; }
            QGroupBox { border: 1px solid #dce4de; border-radius: 7px; margin-top: 13px; padding: 17px 13px 11px; background: #ffffff; }
            QGroupBox::title { subcontrol-origin: margin; left: 13px; padding: 0 5px; color: #345e4d; font-weight: 600; }
            QLineEdit, QPlainTextEdit, QTextBrowser, QComboBox, QSpinBox, QDoubleSpinBox, QListWidget { background: #ffffff; border: 1px solid #cdd9d1; border-radius: 5px; padding: 6px; selection-background-color: #c7e8d7; selection-color: #173c2c; }
            QPushButton { padding: 8px 15px; border: 1px solid #c9d6cd; border-radius: 5px; background: #f7faf7; }
            QPushButton:hover { background: #e9f3ec; }
            QPushButton:pressed { background: #d9eade; }
            QPushButton:disabled { color: #94a099; background: #eff2ef; border-color: #e0e5e1; }
            QPushButton#primary { background: #22694e; color: white; border: 1px solid #22694e; font-weight: 600; }
            QPushButton#primary:hover { background: #18573e; }
            QPushButton#primary:disabled { background: #9db8aa; border-color: #9db8aa; }
            QProgressBar { border: none; background: #e4ebe5; border-radius: 5px; min-height: 10px; max-height: 14px; text-align: center; }
            QProgressBar::chunk { background: #3b9270; border-radius: 5px; }
            QListWidget::item { padding: 6px 2px; border-bottom: 1px solid #eef2ee; }
        """)

    def choose_pdf(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择 PDF", "", "PDF 文件 (*.pdf)")
        if path:
            self.set_source(path)

    def set_source(self, path):
        path = Path(path).expanduser().resolve()
        self.source_edit.setText(str(path))
        self.output_edit.setText(str(path.with_suffix(".epub")))
        self.title_edit.clear()
        self.author_edit.clear()
        self.password_edit.clear()
        self.tabs.setCurrentIndex(0)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() and any(u.isLocalFile() and u.toLocalFile().lower().endswith(".pdf") for u in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event):
        if self._operations_busy():
            self._notice("正在转换或准备资源，请先等待完成。")
            return
        for url in event.mimeData().urls():
            if url.isLocalFile() and url.toLocalFile().lower().endswith(".pdf"):
                self.set_source(url.toLocalFile())
                event.acceptProposedAction()
                break

    def choose_output(self):
        path, _ = QFileDialog.getSaveFileName(self, "选择 EPUB 输出文件", self.output_edit.text(), "EPUB 电子书 (*.epub)")
        if path:
            self.output_edit.setText(path if path.lower().endswith(".epub") else path + ".epub")

    def choose_resources(self):
        if self._operations_busy():
            return self._notice("请等待当前转换或资源操作结束。")
        path = QFileDialog.getExistingDirectory(self, "选择本地资源目录", self.resources_edit.text())
        if path:
            self.resources_edit.setText(path)
            self.check_resources()

    def check_resources(self):
        from .resources import check_resources
        try:
            result = check_resources(Path(self.resources_edit.text()).expanduser())
        except Exception as exc:
            result = {"ok": False, "missing": [], "errors": [str(exc)]}
        if result.get("ok"):
            self.resource_summary.setText("✓ 本地 OCR 资源已就绪 · 可断网转换")
            self.resources_detail.setPlainText("本地模型与清单校验通过。\n" + json.dumps(result.get("manifest") or {}, ensure_ascii=False, indent=2))
        else:
            missing = result.get("missing") or []
            errors = result.get("errors") or []
            self.resource_summary.setText("需要准备 OCR 资源。请到「资源与设置」导入离线包或主动下载。")
            self.resources_detail.setPlainText("缺少：\n" + "\n".join(map(str, missing)) + "\n校验问题：\n" + "\n".join(map(str, errors)) + "\n\n导入包含 manifest.json、det.onnx、rec.onnx、cls.onnx 的资源 ZIP。")
        return bool(result.get("ok"))

    def _operations_busy(self):
        return self._resource_busy or self._worker_busy or self.process.state() != QProcess.NotRunning

    def _gpu_runtime_description(self):
        backend = self.gpu_backend_combo.currentData()
        name = "DirectML" if backend == "directml" else "CUDA"
        try:
            from .gpu_resources import runtime_summary
            summary = runtime_summary(backend=backend)
            download = int(summary.get("download_bytes", 0)) / 1024 ** 3
            installed = int(summary.get("installed_bytes", 0)) / 1024 ** 3
            return (f"独立 Windows {name} OCR 运行包：下载约 {download:.2f} GiB，展开约 {installed:.2f} GiB，准备时还需临时空间。"
                    "仅显式点击下载才联网，不发送 PDF、正文或图片；转换不会自动补齐。可导出 ZIP 复制到离线电脑。")
        except (OSError, ValueError, TypeError, KeyError):
            return f"Windows {name} OCR 运行包大小以本版固定清单为准。当前清单尚不可用，检查会说明缺项；CPU 不受影响。主动下载只准备运行组件，不发送文档，转换不会自动下载。"

    def _gpu_backend_changed(self):
        backend = self.gpu_backend_combo.currentData()
        self._gpu_runtime_paths[self._gpu_resource_backend] = self.gpu_runtime_edit.text()
        self._gpu_resource_backend = backend
        self.gpu_runtime_edit.setText(self._gpu_runtime_paths[backend])
        self._gpu_state = None
        name = "DirectML" if backend == "directml" else "CUDA"
        self.gpu_summary.setText(self._gpu_runtime_description())
        self.gpu_detail.setPlainText(f"未检查 {name} 运行包；两种后端使用各自目录。检查文件完整性不代表 GPU 已执行。")
        # CPU users may prepare either optional package without changing OCR.
        current = self.ocr_device_combo.currentData()
        if current != "cpu" and current != backend:
            self.ocr_device_combo.setCurrentIndex(self.ocr_device_combo.findData(backend))

    def _device_selection_changed(self):
        device = self.ocr_device_combo.currentData()
        gpu = device in ("directml", "cuda")
        if hasattr(self, "gpu_fallback_check"):
            self.gpu_fallback_check.setEnabled(gpu and not self._operations_busy())
        if gpu and hasattr(self, "gpu_backend_combo") and self.gpu_backend_combo.currentData() != device:
            self.gpu_backend_combo.setCurrentIndex(self.gpu_backend_combo.findData(device))

    def _device_args(self):
        args = ["--ocr-device", self.ocr_device_combo.currentData() or "cpu",
                "--gpu-fallback" if self.gpu_fallback_check.isChecked() else "--no-gpu-fallback"]
        if self.ocr_device_combo.currentData() in ("directml", "cuda") and self.gpu_runtime_edit.text().strip():
            args += ["--gpu-runtime", self.gpu_runtime_edit.text().strip()]
        return args

    def _show_gpu_state(self, state):
        self._gpu_state = state
        name = "DirectML" if self.gpu_backend_combo.currentData() == "directml" else "CUDA"
        if "ok" not in state:
            self.gpu_detail.setPlainText("离线运行包已导出：" + str(state.get("path", "")) + "\nSHA-256：" + str(state.get("sha256", "")))
            return
        if state["ok"]:
            manifest = state.get("manifest") or {}
            self.gpu_detail.setPlainText(f"{name} 运行包文件完整；尚未据此确认 GPU 已执行。转换时检查实际设备。\n"
                                         + str(manifest.get("id", "")) + "\n"
                                         + json.dumps(manifest.get("versions", {}), ensure_ascii=False, indent=2))
        else:
            missing, errors = state.get("missing") or [], state.get("errors") or []
            details = list(map(str, errors)) + ["缺少：" + str(name) for name in missing]
            if len(details) > 40:
                details = details[:40] + [f"另有 {len(details) - 40} 项；CLI gpu-resources check 可显示完整清单。"]
            self.gpu_detail.setPlainText(f"{name} 运行包未就绪：\n" + "\n".join(details)
                                         + "\n请导入本地离线包或主动下载。CPU 不需要该包；转换时按回退设置处理，绝不自动下载。")

    def choose_gpu_runtime(self):
        if self._operations_busy():
            return self._notice("请等待当前转换或资源操作结束。")
        path = QFileDialog.getExistingDirectory(self, "选择当前 GPU 后端的运行包目录", self.gpu_runtime_edit.text())
        if path:
            self.gpu_runtime_edit.setText(path)
            self.check_gpu_resources()

    def check_gpu_resources(self):
        from .gpu_resources import check_gpu_runtime
        dest = Path(self.gpu_runtime_edit.text()).expanduser()
        backend = self.gpu_backend_combo.currentData()
        self._resource_work(lambda: check_gpu_runtime(dest, backend=backend), "正在后台逐文件校验 GPU 运行包；窗口可正常响应…", category="gpu")

    def import_gpu_resources(self):
        if self._operations_busy():
            return self._notice("请等待当前转换或资源操作结束。")
        backend = self.gpu_backend_combo.currentData()
        path, _ = QFileDialog.getOpenFileName(self, f"导入离线 {backend} 运行包", "", "运行包 (*.zip)")
        if path:
            from .gpu_resources import import_gpu_pack
            dest = Path(self.gpu_runtime_edit.text()).expanduser()
            self._resource_work(lambda progress: import_gpu_pack(path, dest, progress=progress, backend=backend),
                                "正在后台校验并导入 GPU 运行包…", category="gpu", with_progress=True)

    def download_gpu_resources(self):
        from .gpu_resources import download_gpu_runtime
        dest = Path(self.gpu_runtime_edit.text()).expanduser()
        backend = self.gpu_backend_combo.currentData()
        self._resource_work(lambda progress: download_gpu_runtime(dest, progress=progress, backend=backend),
                            "按你的操作下载固定 GPU 运行组件，不发送文档…", category="gpu", with_progress=True)

    def export_gpu_resources(self):
        if self._operations_busy():
            return self._notice("请等待当前转换或资源操作结束。")
        backend = self.gpu_backend_combo.currentData()
        path, _ = QFileDialog.getSaveFileName(self, "导出当前 GPU 后端的离线运行包", f"LocalPDFtoEPUB-{backend}-runtime.zip", "运行包 (*.zip)")
        if path:
            from .gpu_resources import export_gpu_pack
            dest = path if path.lower().endswith(".zip") else path + ".zip"
            source = Path(self.gpu_runtime_edit.text()).expanduser()
            self._resource_work(lambda: export_gpu_pack(source, dest, backend=backend), "正在后台验证并压缩离线 GPU 运行包…", category="gpu")

    def _resource_work(self, function, message, category="ocr", with_progress=False):
        if self._operations_busy():
            self._notice("转换与资源操作不能同时运行，请等待当前操作结束。")
            return False
        self._resource_busy = True
        self._set_busy(self._worker_busy)
        self.status_label.setText(message)
        self.progress_bar.setRange(0, 0)
        def finish(result):
            self._resource_busy = False
            self._set_busy(self._worker_busy)
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(100 if result.get("ok", True) else 0)
            if category == "gpu":
                self._show_gpu_state(result)
            else:
                self.check_resources()
            self._notice("资源操作完成。" if result.get("ok", True) else "资源检查未通过，请查看缺项并导入本地包。")
        def fail(message):
            self._resource_busy = False
            self._set_busy(self._worker_busy)
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(0)
            if category == "gpu":
                self._show_gpu_state({"ok": False, "missing": [], "errors": [message]})
            else:
                self.check_resources()
            self._error(message)
        self._run_background(function, finish, fail, with_progress=with_progress)
        return True

    def _run_background(self, function, completed, failed=None, with_progress=False):
        thread = CallThread(function, self, with_progress=with_progress)
        thread.ui_completed = completed
        thread.ui_failed = failed or self._error
        self._threads.append(thread)
        thread.completed.connect(self._background_completed, Qt.QueuedConnection)
        thread.failed.connect(self._background_failed, Qt.QueuedConnection)
        thread.progress.connect(self._background_progress, Qt.QueuedConnection)
        thread.finished.connect(self._background_finished, Qt.QueuedConnection)
        thread.start()

    @Slot(str)
    def _background_progress(self, message):
        self._notice(message)

    @Slot(object)
    def _background_completed(self, result):
        thread = self.sender()
        thread.ui_completed(result)

    @Slot(str)
    def _background_failed(self, message):
        thread = self.sender()
        thread.ui_failed(message)

    @Slot()
    def _background_finished(self):
        thread = self.sender()
        if thread in self._threads:
            self._threads.remove(thread)
        thread.deleteLater()

    def import_resources(self):
        if self._operations_busy():
            return self._notice("请等待当前转换或资源操作结束。")
        path, _ = QFileDialog.getOpenFileName(self, "导入离线资源包", "", "资源包 (*.zip)")
        if not path:
            return
        from .resources import import_pack
        dest = Path(self.resources_edit.text()).expanduser()
        self._resource_work(lambda: import_pack(path, dest), "正在验证并导入本地资源包…")

    def download_resources(self):
        from .resources import download_pack
        dest = Path(self.resources_edit.text()).expanduser()
        self._resource_work(lambda: download_pack(dest), "正在按你的操作联网准备资源；不会发送文档…")

    def _output_args(self):
        args = ["--output", self.output_edit.text().strip(), "--font-size", str(self.font_size.value()), "--line-height", str(self.line_height.value()), "--language", self.language_edit.text().strip() or "zh-CN"]
        args.append("--retain-page-margins" if self.retain_page_margins_check.isChecked() else "--no-retain-page-margins")
        if self.title_edit.text().strip():
            args += ["--title", self.title_edit.text().strip()]
        if self.author_edit.text().strip():
            args += ["--author", self.author_edit.text().strip()]
        if self.partial_check.isChecked():
            args += ["--allow-partial"]
        if self.epubcheck_edit.text().strip():
            args += ["--epubcheck", self.epubcheck_edit.text().strip()]
        return args

    def _password_args(self):
        self._cleanup_password()
        password = self.password_edit.text()
        if not password:
            return []
        fd, name = tempfile.mkstemp(prefix="local-epub-password-", suffix=".txt")
        try:
            # mkstemp uses 0600 on POSIX and the user's temporary directory on
            # Windows; no password ever appears in argv or the saved job.
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(password)
        except Exception:
            Path(name).unlink(missing_ok=True)
            raise
        self._password_file = Path(name)
        return ["--password-file", name]

    def _cleanup_password(self):
        if self._password_file:
            self._password_file.unlink(missing_ok=True)
            self._password_file = None

    def start_conversion(self):
        if self._operations_busy():
            return self._notice("请等待当前转换或资源操作结束。")
        if self._dirty:
            self.save_current_edit()
            if self._dirty:
                return
        source = Path(self.source_edit.text().strip()).expanduser()
        if not source.is_file() or source.suffix.lower() != ".pdf":
            return self._error("请选择一个存在且可读的 PDF 文件。")
        if not self.output_edit.text().strip():
            return self._error("请选择 EPUB 输出位置。")
        if not self.check_resources():
            self.tabs.setCurrentIndex(2)
            return self._error("OCR 资源未就绪。请按检查结果导入本地包或主动下载；转换不会自动联网。")
        output = Path(self.output_edit.text()).expanduser().resolve()
        if source.resolve() == output:
            return self._error("输出路径不能与原 PDF 相同。")
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        self.job_dir = output.parent / (output.stem + ".task-" + stamp)
        args = ["convert", str(source.resolve()), "--job", str(self.job_dir), "--resources", self.resources_edit.text(), "--mode", self.mode_combo.currentData(), "--dpi", str(self.dpi_spin.value()), "--cancel-file", str(self._cancel_path())]
        try:
            args += self._password_args() + self._device_args() + self._output_args()
            self._start_worker(args, "convert")
        except Exception as exc:
            self._error(str(exc))

    def _start_worker(self, args, action):
        if self._operations_busy():
            raise RuntimeError("已有转换或资源操作正在运行。")
        if self.job_dir:
            self._cancel_path().unlink(missing_ok=True)
        self._running_action = action
        self._stdout = b""
        self._stderr = ""
        self._last_error = ""
        self._last_status = ""
        self._last_event = {}
        if action in ("convert", "resume"):
            requested = self.ocr_device_combo.currentData() or "cpu"
            self._last_device_event = {}
            self._device_events = []
            self.actual_device_label.setText("实际设备：等待后台执行确认（本次选择 " + requested.upper() + "）。")
        self.log.clear()
        self._set_busy(True)
        self.status_label.setText("正在复用已有识别结果重新整理版面，不重新 OCR…" if action == "rebuild-layout" else
                                  "正在导出已保存的修订，不重复 OCR…" if action == "export" else "正在启动本地转换进程…")
        self.progress_bar.setRange(0, 0)
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONUNBUFFERED", "1")
        if not getattr(sys, "frozen", False):
            src_dir = str(Path(__file__).resolve().parents[1])
            existing = env.value("PYTHONPATH", "")
            env.insert("PYTHONPATH", src_dir + (os.pathsep + existing if existing else ""))
            command_args = ["-m", "local_epub"] + args
        else:
            command_args = ["--worker"] + args
        self.process.setProcessEnvironment(env)
        executable = sys.executable
        if getattr(sys, "frozen", False):
            # A Windows GUI-subsystem executable has no stdout pipe. The bundled
            # console companion shares its runtime and provides reliable JSONL.
            companion = Path(sys.executable).with_name("LocalPDFtoEPUB-CLI.exe" if sys.platform == "win32" else "LocalPDFtoEPUB-CLI")
            if companion.is_file():
                executable = str(companion)
        self.process.start(executable, command_args)

    def _set_busy(self, busy):
        self._worker_busy = bool(busy)
        busy = bool(busy or self._resource_busy)
        for widget in (self.start_button, self.import_button, self.download_button, self.check_button,
                       self.resources_edit, self.resources_browse, self.ocr_device_combo,
                       self.gpu_backend_combo, self.gpu_runtime_edit, self.gpu_browse_button, self.gpu_check_button,
                       self.gpu_import_button, self.gpu_download_button, self.gpu_export_button,
                       self.mode_combo, self.dpi_spin):
            widget.setEnabled(not busy)
        self.gpu_fallback_check.setEnabled(not busy and self.ocr_device_combo.currentData() in ("directml", "cuda"))
        self.export_button.setEnabled(not busy and self.job_dir is not None)
        self.resume_button.setEnabled(not busy and self.job_dir is not None)
        self.retry_button.setEnabled(not busy and self.job_dir is not None)
        self.rebuild_layout_button.setEnabled(not busy and self.job_dir is not None)
        self.exclude_button.setEnabled(not busy and self.job_dir is not None)
        self.retain_page_margins_check.setEnabled(not busy)
        self.compatible_reader_button.setEnabled(not busy and not self._reader_opening and self._last_output is not None and self._last_output.is_file())
        self.cancel_button.setEnabled(self._worker_busy)
        self.cancel_button.setText("取消任务")
        self._sync_editor_controls(busy=busy)
        self.save_button.setEnabled(not busy and self._dirty)

    def _read_stdout(self):
        self._stdout += bytes(self.process.readAllStandardOutput())
        while b"\n" in self._stdout:
            line, self._stdout = self._stdout.split(b"\n", 1)
            self._handle_line(line.decode("utf-8", errors="replace"))

    def _handle_line(self, line):
        if not line.strip():
            return
        try:
            event = json.loads(line)
        except ValueError:
            self.log.appendPlainText(line)
            return
        if not isinstance(event, dict):
            self.log.appendPlainText(line)
            return
        self._last_event = event
        kind = event.get("event", "")
        if kind == "progress":
            page, total = event.get("page", 0), event.get("total", 0)
            stages = {"parse": "解析与识别", "export": "生成 EPUB", "inspect": "检查文件", "validate": "校验格式", "cache": "读取检查点", "complete": "处理完成", "structure": "整理结构", "layout": "重新整理版面", "rebuild_layout": "重新整理版面", "page_error": "页面失败", "device": "执行设备"}
            stage = stages.get(event.get("stage", ""), event.get("stage", "处理"))
            message = str(event.get("message") or stage)
            if total:
                self.progress_bar.setRange(0, int(total))
                self.progress_bar.setValue(int(page))
                message = f"{stage} · {page}/{total} 页 · {message}"
            self._set_status_text(message)
            self.log.appendPlainText(message)
            if event.get("stage") == "device":
                self._last_device_event = event
                self._device_events.append(event)
                actual = event.get("actual_device")
                if actual:
                    device_name = {"native": "CPU（文字层）", "cpu": "CPU OCR", "directml": "DirectML OCR", "cuda": "NVIDIA CUDA OCR"}.get(actual, str(actual))
                    label = ("实际完成设备：" if event.get("page") else "等待页面执行确认，当前计划设备：") + device_name
                    if event.get("fallback_reason"):
                        reason = str(event["fallback_reason"])
                        label += " · CPU 回退原因：" + (reason if len(reason) <= 100 else reason[:100] + "…（完整原因见日志）")
                    self.actual_device_label.setText(label)
        elif kind == "error":
            self._last_error = str(event.get("message", "未知错误"))
            self._error(self._last_error)
        else:
            self._last_status = str(event.get("status", self._last_status))
            if event.get("output"):
                self._last_output = Path(event["output"])
            if event.get("report"):
                self._last_report = Path(event["report"])
            if event.get("format"):
                self._last_format = event["format"]
            self.log.appendPlainText("后台任务结束：" + str(event.get("message") or STATES.get(event.get("status"), event.get("status", "完成"))))
            if event.get("output"):
                self.log.appendPlainText("EPUB：" + str(event["output"]))
            if event.get("report"):
                self.log.appendPlainText("质量报告：" + str(event["report"]))

    def _read_stderr(self):
        data = bytes(self.process.readAllStandardError()).decode("utf-8", errors="replace")
        self._stderr += data
        if data.strip():
            self.log.appendPlainText(data.strip())

    def _process_error(self, error):
        if error == QProcess.FailedToStart:
            self._cleanup_password()
            self._set_busy(False)
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(0)
            self._error("后台进程无法启动：" + self.process.errorString())

    def _finished(self, exit_code, exit_status):
        self._read_stdout()
        self._read_stderr()
        if self._stdout.strip():
            self._handle_line(self._stdout.decode("utf-8", errors="replace"))
        self._stdout = b""
        self._cleanup_password()
        self._set_busy(False)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(100 if exit_code == 0 else 0)
        if self.job_dir and (self.job_dir / "job.json").exists():
            self.load_task(self.job_dir, preserve_form=True)
        cancelled = self.job_dir and self._cancel_path().exists()
        if cancelled:
            self._notice("任务已取消。已完成页面保留为检查点；可继续任务。")
        elif exit_code == 2 and self._last_status == "incomplete" and self._last_output and self._last_output.is_file():
            self._notice("已按你的选择导出部分结果。缺失页面已写入 EPUB 与质量报告，可重试后重新导出。")
            self.open_result_button.setEnabled(True)
            self.compatible_reader_button.setEnabled(not self._reader_opening)
            self.tabs.setCurrentIndex(1)
        elif exit_code != 0:
            self._error(self._last_error or self._stderr.strip() or f"后台进程异常退出（{exit_code}）；已完成页仍保留。")
        else:
            missing = self.document.get("missing_pages", [])
            if missing:
                self._notice(f"存在未完成页面 {missing}。只有明确允许部分结果时才会导出；请查看疑点与缺页。")
            else:
                epubcheck_state = self._last_format.get("epubcheck", {}).get("status", "not_run")
                label = {"passed": "通过", "valid": "通过", "failed": "未通过", "not_run": "未运行"}.get(epubcheck_state, epubcheck_state)
                completed = "版面已重新整理并导出" if self._running_action == "rebuild-layout" else "转换与导出已完成"
                self._notice(f"{completed} · EPUBCheck：{label}。请校对内容疑点；格式合法不代表文字准确。")
            output = Path(self.output_edit.text()).expanduser()
            if output.is_file():
                self._last_output = output
                self.open_result_button.setEnabled(True)
                self.compatible_reader_button.setEnabled(not self._reader_opening)
            self.tabs.setCurrentIndex(1)
        self.report_button.setEnabled(self.job_dir is not None)

    def _cancel_path(self):
        # Place the signal beside the task: early cancellation must not make a
        # not-yet-created task directory appear nonempty to create_job().
        return self.job_dir.with_name(self.job_dir.name + ".cancel")

    def cancel_task(self):
        if self.process.state() == QProcess.NotRunning:
            return
        if self.job_dir:
            self.job_dir.parent.mkdir(parents=True, exist_ok=True)
            cancel = self._cancel_path()
            if not cancel.exists():
                cancel.write_text("cancel", encoding="utf-8")
                self.cancel_button.setText("立即终止")
                self._notice("正在取消：等待当前页落盘。再次点击可立即终止，当前页将在下次重试。")
                return
        self.process.kill()
        self._notice("已请求立即终止；重新打开任务可继续。")

    def choose_job(self):
        if self._operations_busy():
            return self._error("请先完成当前转换或资源操作。")
        path = QFileDialog.getExistingDirectory(self, "选择包含 job.json 的任务目录")
        if path:
            self.load_task(Path(path))

    def load_task(self, path, preserve_form=False):
        from .pipeline import load_job, build_document
        try:
            if self._dirty and self.selected_block:
                self.save_current_edit()
                if self._dirty:
                    return
            path = Path(path)
            same_job = self._loaded_job_root == path.resolve()
            previous_page = self.page_combo.currentData() if same_job else None
            if not same_job:
                self.selected_block = None
            job = load_job(path)
            document = build_document(path)
            self.job_dir = path
            self.document = document
            completed = any(page.get("status") == "success" for page in job.get("pages", []))
            actual = (job.get("device") or document.get("device")) if completed else None
            actual = actual or "尚无已完成识别页；未确认执行设备"
            execution = job.get("execution_summary") or {}
            label = "实际设备：" + str(actual)
            fallback_pages = execution.get("fallback_pages") or []
            if fallback_pages:
                label += f" · {len(fallback_pages)} 页已回退 CPU，原因见处理记录与质量报告"
            self.actual_device_label.setText(label)
            self._last_output = None
            self._last_report = None
            self._last_format = {}
            self.open_result_button.setEnabled(False)
            self.compatible_reader_button.setEnabled(False)
            last_export = path / "last-export.json"
            if last_export.is_file():
                export_info = json.loads(last_export.read_text(encoding="utf-8"))
                if export_info.get("report"):
                    self._last_report = Path(export_info["report"])
                if export_info.get("output") and Path(export_info["output"]).is_file():
                    self._last_output = Path(export_info["output"])
                    self.open_result_button.setEnabled(True)
                    self.compatible_reader_button.setEnabled(not self._reader_opening)
                self._last_format = export_info.get("format", {})
            self.job_label.setText(f"任务：{path.name} · {len(job.get('pages', []))} 页 · 原稿、自动稿和人工修订分别保存")
            if preserve_form:
                meta = document.get("metadata", job.get("metadata", {}))
                if not self.title_edit.text().strip():
                    self.title_edit.setText(meta.get("title") or "")
                if not self.author_edit.text().strip():
                    self.author_edit.setText(meta.get("author") or "")
            if not preserve_form:
                self.source_edit.setText(str(job.get("source", {}).get("path", path / "source.pdf")))
                meta = document.get("metadata", job.get("metadata", {}))
                self.title_edit.setText(meta.get("title") or path.name)
                self.author_edit.setText(meta.get("author") or "")
                self.output_edit.setText(str(self._last_output) if self._last_output else str(path.parent / ((meta.get("title") or "电子书") + ".epub")))
                export_options = job.get("export_options", {})
                self.font_size.setValue(float(export_options.get("font_size", 1.0)))
                self.line_height.setValue(float(export_options.get("line_height", 1.8)))
                self.language_edit.setText(export_options.get("language", "zh-CN"))
                # Legacy appendix requests do not opt in to running margins.
                self.retain_page_margins_check.setChecked(bool(export_options.get("retain_page_margins", False)))
                options = job.get("options", {})
                self.mode_combo.setCurrentIndex(max(0, self.mode_combo.findData(options.get("mode", "auto"))))
                self.dpi_spin.setValue(int(options.get("dpi", 180)))
                self.ocr_device_combo.setCurrentIndex(max(0, self.ocr_device_combo.findData(options.get("ocr_device", "cpu"))))
                self.gpu_fallback_check.setChecked(bool(options.get("gpu_fallback", True)))
                saved_runtime = job.get("gpu_runtime", {})
                previous_gpu = saved_runtime.get("path")
                requested_device = options.get("ocr_device", "cpu")
                # An interrupted device switch may retain the previous backend's
                # runtime. Do not override the worker's backend isolation with it.
                if previous_gpu and requested_device in ("directml", "cuda") and saved_runtime.get("backend", "cuda") == requested_device:
                    self.gpu_runtime_edit.setText(str(previous_gpu))
                previous_resources = job.get("resources", {}).get("path")
                if previous_resources and Path(previous_resources).is_dir():
                    self.resources_edit.setText(previous_resources)
                    self.check_resources()
            # Cocoa accessibility clients retain item interfaces. A model-wide
            # clear/reset on every export invalidates that hierarchy needlessly.
            # Keep choices and content items alive, updating only changed data.
            self.page_combo.blockSignals(True)
            choices = [("全部页面", None)] + [
                (f"第 {page.get('number')} 页 · {STATES.get(page.get('status'), page.get('status', '未知'))}", page.get("number"))
                for page in job.get("pages", [])
            ]
            for index, (label, value) in enumerate(choices):
                if index == self.page_combo.count():
                    self.page_combo.addItem(label, value)
                else:
                    if self.page_combo.itemText(index) != label:
                        self.page_combo.setItemText(index, label)
                    if self.page_combo.itemData(index) != value:
                        self.page_combo.setItemData(index, value)
            while self.page_combo.count() > len(choices):
                self.page_combo.removeItem(self.page_combo.count() - 1)
            self.page_combo.setCurrentIndex(max(0, self.page_combo.findData(previous_page)))
            self.page_combo.blockSignals(False)
            self._loaded_job_root = path.resolve()
            self._dirty = False
            self._filter_blocks()
            self._update_preview()
            self._set_busy(False)
            self.report_button.setEnabled(True)
            self.tabs.setCurrentIndex(1)
            return True
        except Exception as exc:
            self._error("无法打开任务：" + str(exc))
            return False

    @staticmethod
    def _block_list_label(block):
        pages = [source.get("page") for source in block.get("sources", [])]
        kind = KINDS.get(block.get("kind"), block.get("kind", "内容"))
        text = block.get("text", "").replace("\n", " ")[:52] or block.get("asset", "") or "无文字"
        role = block.get("page_margin_role")
        state = " · " + DECORATIONS.get(role, role) if role else (" · 历史未导出" if block.get("export_override") == "exclude" else "")
        return f"{kind} · 页 {','.join(map(str, pages)) or '?'}{state}\n{text}"

    def _filter_blocks(self):
        if self._dirty:
            self.save_current_edit()
            if self._dirty:
                return
        page = self.page_combo.currentData()
        selected_id = self.selected_block.get("id") if self.selected_block else None
        desired = [block for block in self.document.get("blocks", [])
                   if page is None or page in [source.get("page") for source in block.get("sources", [])]]
        self.block_list.blockSignals(True)
        selected_row = 0
        # Reuse a row pool rather than deleting QListWidgetItems. Filtering,
        # reopening, and exporting retain the same native accessible objects;
        # surplus rows are hidden and released of old content, not destroyed.
        for index, block in enumerate(desired):
            label = self._block_list_label(block)
            item = self.block_list.item(index)
            if item is None:
                item = QListWidgetItem()
                self.block_list.addItem(item)
            if item.text() != label:
                item.setText(label)
            if item.data(Qt.UserRole) != block:
                item.setData(Qt.UserRole, block)
            if item.toolTip() != block.get("text", ""):
                item.setToolTip(block.get("text", ""))
            if item.isHidden():
                item.setHidden(False)
            if block.get("id") == selected_id:
                selected_row = index
        for index in range(len(desired), self.block_list.count()):
            item = self.block_list.item(index)
            if not item.isHidden():
                item.setHidden(True)
            if item.data(Qt.UserRole):
                item.setData(Qt.UserRole, {})
                item.setText("")
                item.setToolTip("")
        if desired:
            self.block_list.setCurrentRow(selected_row)
        else:
            self.block_list.setCurrentRow(-1)
        self.block_list.blockSignals(False)
        if desired:
            self._select_block(self.block_list.item(selected_row))
        else:
            self.selected_block = None
            self._loading_editor = True
            if self.editor.toPlainText():
                self.editor.clear()
            self._loading_editor = False
            self.source_label.setText("此页暂无可编辑内容；失败或未完成页可单独重试。")
            self.save_button.setEnabled(False)
            self._sync_editor_controls()
            if page:
                self._show_original(page)

    def _select_block(self, item, previous=None):
        if self._dirty and self.selected_block:
            self.save_current_edit()
            if self._dirty:
                return
        if not item:
            return
        block = item.data(Qt.UserRole)
        if not block or item.isHidden():
            return
        self.selected_block = block
        self._loading_editor = True
        if self.editor.toPlainText() != block.get("text", ""):
            self.editor.setPlainText(block.get("text", ""))
        self.kind_combo.setCurrentIndex(max(0, self.kind_combo.findData(block.get("kind", "paragraph"))))
        self.heading_level.setValue(int(block.get("level") or 1))
        self._loading_editor = False
        self._dirty = False
        self.save_button.setEnabled(False)
        self._update_source_label()
        self._sync_editor_controls()
        sources = block.get("sources", [])
        if sources and sources[0].get("page"):
            self._show_original(sources[0]["page"])
        self._align_page_number_choice()

    def _update_source_label(self):
        block = self.selected_block
        if not block:
            return
        sources = block.get("sources", [])
        parts = []
        for source in sources:
            bbox = source.get("bbox")
            region = "[" + ", ".join(f"{float(v):.1f}" for v in bbox) + "]" if bbox is not None else "未提供"
            parts.append(f"第 {source.get('page', '?')} 页 · 区域 {region}")
        confidence = block.get("confidence")
        if confidence:
            parts.append(f"引擎分数：{confidence.get('value')}（{confidence.get('source')}；不是文字准确率）")
        else:
            parts.append("引擎未提供置信度")
        if block.get("kind") in ("image", "table"):
            parts.append("图片和表格暂为只读；文字段落、脚注和标题可以校对。")
        decoration = block.get("decoration")
        if decoration:
            evidence = decoration.get("evidence_pages") or []
            parts.append("自动识别为" + DECORATIONS.get(decoration.get("kind"), "页边内容") + "：" + str(decoration.get("reason") or "请对照原页核查。"))
            if evidence:
                parts.append("依据页面：" + "、".join(map(str, evidence)))
        if block.get("page_margin_role"):
            parts.append("当前输出不保留页边内容；这里的原文、修订和原页仍可查看。")
        elif block.get("export_override") == "exclude":
            parts.append("旧任务曾人工排除此块正文，可点击「恢复此块正文」撤销该历史选择。")
        else:
            parts.append("原文、来源与人工修订均保留。")
        self.source_label.setText("\n".join(parts))

    def _sync_editor_controls(self, busy=None):
        if busy is None:
            busy = self.process.state() != QProcess.NotRunning
        editable = self.selected_block is not None and self.selected_block.get("kind") not in ("image", "table")
        self.editor.setReadOnly(busy or not editable)
        self.kind_combo.setEnabled(not busy and editable)
        self.heading_level.setEnabled(not busy and editable)
        conflicts = self.document.get("edit_conflicts", [])
        conflict = editable and any(c.get("block_id") == self.selected_block["id"] for c in conflicts)
        self.restore_edit_button.setEnabled(not busy and bool(conflict))
        pair = self._merge_pair()
        self.merge_button.setEnabled(not busy and pair is not None)
        historical = bool(self.selected_block and not self.selected_block.get("page_margin_role")
                          and self.selected_block.get("export_override") == "exclude")
        self.restore_body_button.setVisible(historical)
        self.restore_body_button.setEnabled(not busy and historical)
        self._sync_page_number_buttons(busy)

    def restore_legacy_body(self):
        if not self.job_dir or not self.selected_block or self.process.state() != QProcess.NotRunning:
            return
        if self.selected_block.get("page_margin_role") or self.selected_block.get("export_override") != "exclude":
            return
        block_id = self.selected_block["id"]
        self.save_current_edit()
        if self._dirty:
            return
        try:
            from .pipeline import set_export_override
            set_export_override(self.job_dir, block_id, "auto")
            self.load_task(self.job_dir, preserve_form=True)
            self._notice("已撤销此块的历史人工排除，文字修订保留。重新导出后生效。")
        except Exception as exc:
            self._error("恢复正文未完成：" + str(exc))

    def _page_number_state(self):
        page = self.page_number_choice_combo.currentData()
        suggestion = next((item for item in self.document.get("page_number_suggestions", []) if item.get("page") == page), None)
        repair = next((item for item in self.document.get("page_number_repairs", []) if item.get("page") == page), None)
        conflict = next((item for item in self.document.get("page_number_repair_conflicts", []) if item.get("page") == page), None)
        return page, suggestion, repair, conflict

    def _refresh_page_number_controls(self):
        previous = self.page_number_choice_combo.currentData()
        entries = {}
        for item in self.document.get("page_number_repair_conflicts", []):
            if isinstance(item.get("page"), int):
                entries[item["page"]] = f"第 {item['page']} 页 · 补写记录需复核"
        for item in self.document.get("page_number_repairs", []):
            entries[item["page"]] = f"第 {item['page']} 页 · 人工推算 {item['label']}"
        for item in self.document.get("page_number_suggestions", []):
            entries[item["page"]] = f"第 {item['page']} 页 · 建议 {item['label']}"
        choices = sorted(entries.items())
        self.page_number_choice_combo.blockSignals(True)
        for index, (number, label) in enumerate(choices):
            if index == self.page_number_choice_combo.count():
                self.page_number_choice_combo.addItem(label, number)
            else:
                self.page_number_choice_combo.setItemText(index, label)
                self.page_number_choice_combo.setItemData(index, number)
        while self.page_number_choice_combo.count() > len(choices):
            self.page_number_choice_combo.removeItem(self.page_number_choice_combo.count() - 1)
        self.page_number_choice_combo.setCurrentIndex(max(0, self.page_number_choice_combo.findData(previous)) if choices else -1)
        self.page_number_choice_combo.blockSignals(False)
        self.review_detail_tabs.setTabVisible(self.page_number_tab_index, bool(choices))
        self._align_page_number_choice()
        self._describe_page_number()

    def _align_page_number_choice(self):
        page = self.page_combo.currentData()
        if page is None and self.selected_block:
            sources = {source.get("page") for source in self.selected_block.get("sources", [])}
            page = next(iter(sources)) if len(sources) == 1 else None
        index = self.page_number_choice_combo.findData(page)
        if page is not None and index >= 0:
            self.page_number_choice_combo.blockSignals(True)
            self.page_number_choice_combo.setCurrentIndex(index)
            self.page_number_choice_combo.blockSignals(False)
            self._describe_page_number()

    def _describe_page_number(self):
        page, suggestion, repair, conflict = self._page_number_state()
        lines = []
        if suggestion:
            evidence = suggestion.get("evidence", {})
            for name in ("left", "right"):
                item = evidence.get(name, {})
                lines.append(f"原 PDF 第 {item.get('page', '?')} 页实际识别：{item.get('text', '未提供')}")
            lines.extend(["", f"原 PDF 第 {page} 页建议补写：{suggestion['label']}（待主动确认）。"])
        if repair:
            lines.append(f"原 PDF 第 {page} 页已保存人工推算页码「{repair['label']}」，不是 OCR 识别结果。")
        if conflict:
            lines.append("原补写记录未应用：" + conflict.get("reason", "来源已变化，请重新核对。"))
            old_label = conflict.get("record", {}).get("label")
            if old_label is not None:
                lines.append(f"保留的旧人工推算值：{old_label}")
        text = "\n".join(lines)
        if self.page_number_detail.toPlainText() != text:
            self.page_number_detail.setPlainText(text)
        self._sync_page_number_buttons()

    def _sync_page_number_buttons(self, busy=None):
        if busy is None:
            busy = self.process.state() != QProcess.NotRunning
        page, suggestion, repair, conflict = self._page_number_state()
        self.confirm_page_number_button.setVisible(suggestion is not None)
        self.confirm_page_number_button.setEnabled(not busy and suggestion is not None and self.job_dir is not None)
        self.clear_page_number_button.setVisible(repair is not None or conflict is not None)
        self.clear_page_number_button.setEnabled(not busy and (repair is not None or conflict is not None) and self.job_dir is not None)
        self.page_number_choice_combo.setEnabled(not busy)
        self.inspect_page_number_button.setEnabled(not busy and page is not None and self.job_dir is not None)

    def _page_number_chosen(self, *_):
        page = self.page_number_choice_combo.currentData()
        index = self.page_combo.findData(page)
        if page is not None and index >= 0:
            self.page_combo.setCurrentIndex(index)
        self._describe_page_number()

    def inspect_page_number(self):
        page = self.page_number_choice_combo.currentData()
        if page is not None:
            index = self.page_combo.findData(page)
            if index >= 0:
                self.page_combo.setCurrentIndex(index)
            self._show_original(page)
            self.review_detail_tabs.setCurrentIndex(0)

    def confirm_page_number(self):
        if not self.job_dir or self.process.state() != QProcess.NotRunning:
            return
        page, suggestion, _, _ = self._page_number_state()
        if suggestion is None:
            return
        self.save_current_edit()
        if self._dirty:
            return
        try:
            from .page_number_repairs import save_page_number_repair
            save_page_number_repair(self.job_dir, page, suggestion["label"])
            self.load_task(self.job_dir, preserve_form=True)
            self._notice("人工补写记录已保存，身份为人工推算、非 OCR。勾选保留页眉、页脚和原页码并重新导出后，兼容阅读器可在页脚显示确认值；正文不插入页码。")
        except Exception as exc:
            self._error("未保存人工补写：" + str(exc))

    def clear_page_number(self):
        if not self.job_dir or self.process.state() != QProcess.NotRunning:
            return
        page, _, repair, conflict = self._page_number_state()
        if page is None or (repair is None and conflict is None):
            return
        self.save_current_edit()
        if self._dirty:
            return
        try:
            from .page_number_repairs import clear_page_number_repair
            clear_page_number_repair(self.job_dir, page)
            self.load_task(self.job_dir, preserve_form=True)
            self._notice("已撤销人工补写；原文与补写历史保留，未自动填入其他页码。")
        except Exception as exc:
            self._error("未撤销人工补写：" + str(exc))

    def _mark_dirty(self, *_):
        if not self._loading_editor and self.selected_block:
            self._dirty = True
            self.save_button.setEnabled(self.process.state() == QProcess.NotRunning)

    def save_current_edit(self):
        if not self.job_dir or not self.selected_block or not self._dirty:
            return
        from .pipeline import save_edit, build_document
        try:
            text = self.editor.toPlainText()
            kind = self.kind_combo.currentData()
            level = self.heading_level.value()
            save_edit(self.job_dir, self.selected_block["id"], text, kind=kind, level=level)
            block_id = self.selected_block["id"]
            self.document = build_document(self.job_dir)
            self.selected_block = next(block for block in self.document.get("blocks", []) if block.get("id") == block_id)
            for item_index in range(self.block_list.count()):
                current_item = self.block_list.item(item_index)
                if (current_item.data(Qt.UserRole) or {}).get("id") == block_id:
                    current_item.setData(Qt.UserRole, self.selected_block)
                    current_item.setText(self._block_list_label(self.selected_block))
                    current_item.setToolTip(self.selected_block.get("text", ""))
                    break
            self._dirty = False
            self.save_button.setEnabled(False)
            self._update_source_label()
            self._sync_editor_controls()
            self._update_preview()
            self._notice("人工修订已保存。重新导出会应用修改，重新识别时保留修订并报告冲突。")
        except Exception as exc:
            self._error("保存修订失败：" + str(exc))

    def _merge_pair(self):
        if not self.selected_block or self.selected_block.get("kind") != "paragraph":
            return None
        def margin(block):
            return bool(block.get("page_margin_role")) or any(isinstance(issue, dict) and issue.get("code") == "margin_content" for issue in block.get("issues", []))
        if margin(self.selected_block):
            return None
        blocks = self.document.get("blocks", [])
        index = next((i for i, b in enumerate(blocks) if b.get("id") == self.selected_block["id"]), None)
        if index is None:
            return None
        for following in blocks[index + 1:]:
            if margin(following):
                continue
            if following.get("kind") == "paragraph":
                return blocks[index]["id"], following["id"]
            return None
        return None

    def merge_current(self):
        if not self.job_dir:
            return
        self.save_current_edit()
        if self._dirty:
            return
        pair = self._merge_pair()
        if not pair:
            return self._error("只能合并两个紧接的正文段落；请先选择合适段落。")
        try:
            from .pipeline import merge_blocks
            merge_blocks(self.job_dir, pair[0], pair[1])
            self.load_task(self.job_dir, preserve_form=True)
            for index in range(self.block_list.count()):
                if self.block_list.item(index).data(Qt.UserRole).get("id") == pair[0]:
                    self.block_list.setCurrentRow(index)
                    break
            self._notice("已保存人工段落合并，全部来源页保留。请核对连接处，再重新导出 EPUB。")
        except Exception as exc:
            self._error("合并未完成：" + str(exc))

    def restore_conflicting_edit(self):
        if not self.selected_block:
            return
        conflict = next((c for c in self.document.get("edit_conflicts", []) if c.get("block_id") == self.selected_block["id"]), None)
        if not conflict:
            return
        edit = conflict.get("edit", {})
        self.editor.setPlainText(edit.get("text", ""))
        if edit.get("kind"):
            self.kind_combo.setCurrentIndex(max(0, self.kind_combo.findData(edit["kind"])))
        if edit.get("level"):
            self.heading_level.setValue(int(edit["level"]))
        self._notice("已载入保留的旧修订。请对照新识别结果与原页，点击「保存人工修订」确认应用。")

    def exclude_page(self):
        if not self.job_dir:
            return
        page = self.page_combo.currentData()
        if page is None:
            return self._error("请先在页面下拉框中明确选择要排除的一页。")
        self.save_current_edit()
        if self._dirty:
            return
        try:
            from .pipeline import exclude_pages
            exclude_pages(self.job_dir, [page])
            self.load_task(self.job_dir, preserve_form=True)
            self._notice(f"第 {page} 页已主动排除。导出须明确允许部分结果，EPUB 会附缺页说明；重新识别该页可恢复。")
        except Exception as exc:
            self._error(str(exc))

    def _show_original(self, page_number):
        if not self.job_dir:
            return
        self._render_generation += 1
        generation = self._render_generation
        source = self.job_dir / "source.pdf"
        dest = self.job_dir / "preview" / f"{page_number:04d}.png"
        if dest.exists() and self._set_original(dest):
            return
        self._original_display_path = None
        self.original_label.setText("正在生成原页预览…")
        password = self.password_edit.text() or None
        def render():
            from .backend import render_page
            dest.parent.mkdir(parents=True, exist_ok=True)
            # Repeated selections must not treat a half-written PNG as a
            # completed cache entry and invalidate the first render callback.
            fd, temporary = tempfile.mkstemp(prefix=f".{page_number:04d}-", suffix=".png", dir=dest.parent)
            os.close(fd)
            try:
                with _RENDER_LOCK:
                    render_page(source, page_number, temporary, password=password, dpi=100)
                os.replace(temporary, dest)
            finally:
                Path(temporary).unlink(missing_ok=True)
            return dest
        self._run_background(
            render,
            lambda result: self._set_original(result) if generation == self._render_generation else None,
            lambda error: self.original_label.setText("原页预览失败：" + error) if generation == self._render_generation else None,
        )

    def _set_original(self, path):
        display_key = (str(path), self.original_scroll.viewport().width())
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            self.original_label.setText("无法加载原页图片。")
            return False
        if display_key == self._original_display_path:
            return True
        self.original_label.setPixmap(pixmap.scaledToWidth(max(260, self.original_scroll.viewport().width() - 20), Qt.SmoothTransformation))
        self._original_display_path = display_key
        self.original_label.setToolTip("原始 PDF 页面。调整窗口后重新选择内容块可改变预览大小。")
        return True

    def _update_preview(self):
        from .page_margins import export_document
        effective = export_document(self.document, False)
        body = ["<html><head><style>body{font-family:serif;font-size:18px;line-height:1.7;color:#22352d}p{margin:0.7em 0}img{max-width:100%}table{border-collapse:collapse}td{border:1px solid #ccc;padding:5px}small{color:#738175}</style></head><body>"]
        visible_blocks = [block for block in effective.get("blocks", []) if not block.get("exclude_from_export")]
        if not visible_blocks:
            body.append("<p>暂无可导出的正文。请回查输入页面，或恢复旧任务人工排除的普通正文。</p>")
        for block in visible_blocks:
            text = html.escape(block.get("text", "")).replace("\n", "<br/>")
            kind = block.get("kind", "paragraph")
            anchor = html.escape(block.get("id", ""), quote=True)
            if kind == "heading":
                level = max(1, min(6, int(block.get("level") or 1)))
                body.append(f'<h{level} id="{anchor}">{text}</h{level}>')
            elif kind == "table" and block.get("rows"):
                body.append("<table>" + "".join("<tr>" + "".join("<td>" + html.escape(str(cell or "")) + "</td>" for cell in row) + "</tr>" for row in block["rows"]) + "</table>")
            elif kind == "image" and block.get("asset") and self.job_dir:
                asset = (self.job_dir / block["asset"]).resolve()
                if asset.is_relative_to(self.job_dir.resolve()):
                    body.append(f'<p><img width="320" src="{html.escape(QUrl.fromLocalFile(str(asset)).toString(), quote=True)}" alt="原文图片"/></p>')
                if text:
                    body.append("<p>" + text + "</p>")
            else:
                body.append(f'<p id="{anchor}">{text}</p>')
        body.append("</body></html>")
        markup = "".join(body)
        if markup != self._preview_html:
            self.preview.setHtml(markup)
            self._preview_html = markup
        from .reporting import summarize_issues, format_issue
        summary = summarize_issues(export_document(self.document, self.retain_page_margins_check.isChecked()))
        issues = {
            "缺失页面": self.document.get("missing_pages", []),
            "人工修订冲突": self.document.get("edit_conflicts", []),
        }
        lines = ["请逐项对照原页。以下是异常提示，不是识别准确率。", "", "缺失页面"]
        for missing in issues["缺失页面"]:
            if isinstance(missing, dict):
                lines.append(f"• 第 {missing.get('number', '?')} 页：{STATES.get(missing.get('status'), missing.get('status', '未知'))}；{missing.get('error') or '尚无内容'}")
            else:
                lines.append("• " + str(missing))
        if not issues["缺失页面"]:
            lines.append("无；所有输入页均有处理结果。")
        lines.extend(["", "人工修订冲突"])
        for conflict in issues["人工修订冲突"]:
            lines.append(f"• {conflict.get('block_id', '?')}：{conflict.get('reason', '')}")
            if conflict.get("edit", {}).get("text"):
                lines.append("保留的修订：" + conflict["edit"]["text"])
        if not issues["人工修订冲突"]:
            lines.append("无。")
        lines.extend(["", "页边输出"])
        if self.retain_page_margins_check.isChecked():
            lines.append("当前设置保留已识别页边内容及人工确认的页码，需在 Vivliostyle 兼容阅读器查看。每个阅读页采用起始来源页的页眉、页脚，页脚显示该起始来源的原页码；此处只预览正文。")
        else:
            lines.append(f"当前设置不保留 {summary.get('margin_omitted_count', 0)} 处已识别页眉、页脚或页码；原文与原页保留在任务中。")
        if summary.get("excluded"):
            lines.append(f"另有 {len(summary['excluded'])} 个普通正文块沿用旧任务的人工排除决定，可选中该块后点击「恢复此块正文」。")
        if self.document.get("page_number_suggestions") or self.document.get("page_number_repairs") or self.document.get("page_number_repair_conflicts"):
            lines.extend(["", "页码补写"])
            lines.append("存在页码建议、人工补写记录或来源冲突，请查看「页码补写」标签；未经主动确认不会补写，人工推算不会标为 OCR。")
        lines.extend(["", "待核对内容"])
        for issue in summary.get("review", []):
            lines.append("• " + format_issue(issue))
        if not summary.get("review"):
            lines.append("未发现启发式异常；这不等同内容准确性保证。")
        lines.extend(["", "处理记录"])
        for issue in summary.get("processing", []):
            lines.append("• " + format_issue(issue))
        if not summary.get("processing"):
            lines.append("无额外处理记录。")
        issue_text = "\n".join(lines)
        if self.issues_view.toPlainText() != issue_text:
            self.issues_view.setPlainText(issue_text)
        self._refresh_page_number_controls()

    def resume_job(self):
        self._resume(None)

    def retry_page(self):
        page = self.page_combo.currentData()
        if page is None and self.selected_block:
            sources = self.selected_block.get("sources", [])
            page = sources[0].get("page") if sources else None
        if page is None:
            return self._error("请先在页面列表中选择要重试的一页。")
        self._resume([page])

    def _resume(self, pages):
        if self._operations_busy():
            return self._notice("请等待当前转换或资源操作结束。")
        if not self.job_dir:
            return
        self.save_current_edit()
        if self._dirty:
            return
        if not self.check_resources():
            self.tabs.setCurrentIndex(2)
            return self._error("请先准备缺少的本地资源。")
        args = ["resume", str(self.job_dir), "--resources", self.resources_edit.text(), "--cancel-file", str(self._cancel_path())]
        if pages:
            args += ["--retry-pages", ",".join(map(str, pages))]
        try:
            args += self._password_args() + self._device_args() + self._output_args()
            self._start_worker(args, "resume")
        except Exception as exc:
            self._error(str(exc))

    def export_current(self):
        if self._operations_busy():
            return self._notice("请等待当前转换或资源操作结束。")
        if not self.job_dir:
            return
        self.save_current_edit()
        if self._dirty:
            return
        if not self.output_edit.text().strip():
            self.choose_output()
        if not self.output_edit.text().strip():
            return
        if self.document.get("missing_pages") and not self.partial_check.isChecked():
            return self._error("存在缺页。请先重试，或明确勾选「允许导出部分结果」。")
        from .page_margins import export_document
        if not any(not block.get("exclude_from_export") for block in export_document(self.document, False).get("blocks", [])):
            return self._error("没有可导出的正文。请回查输入页面，或恢复旧任务人工排除的普通正文。")
        try:
            self._start_worker(["export", str(self.job_dir)] + self._output_args(), "export")
        except Exception as exc:
            self._error(str(exc))

    def rebuild_layout_current(self):
        if self._operations_busy():
            return self._notice("请等待当前转换或资源操作结束。")
        if not self.job_dir:
            return
        self.save_current_edit()
        if self._dirty:
            return
        if not self.output_edit.text().strip():
            self.choose_output()
        if not self.output_edit.text().strip():
            return
        page = self.page_combo.currentData()
        if self.document.get("missing_pages") and not self.partial_check.isChecked():
            from .pipeline import load_job
            try:
                recoverable = {state["number"] for state in load_job(self.job_dir)["pages"]
                               if state.get("layout_rebuild_failed") and (page is None or state["number"] == page)}
            except Exception as exc:
                return self._error(str(exc))
            if any(item["number"] not in recoverable for item in self.document["missing_pages"]):
                return self._error("存在缺页。若需整理并导出当前已有内容，请明确勾选「允许导出部分结果」；缺失的识别行不能由版面整理补造。")
        args = ["rebuild-layout", str(self.job_dir), "--cancel-file", str(self._cancel_path())]
        if page is not None:
            args += ["--pages", str(page)]
        try:
            self._start_worker(args + self._password_args() + self._output_args(), "rebuild-layout")
        except Exception as exc:
            self._error(str(exc))

    def open_result(self):
        if self._last_output and self._last_output.is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._last_output.resolve())))

    def open_compatible_reader(self, expected_sha256=None):
        if self._reader_opening or self._operations_busy():
            return
        if not self._last_output or not self._last_output.is_file():
            return self._error("请先导出一个 EPUB 文件。")
        output = self._last_output.resolve()
        self._reader_opening = True
        self._reader_result = None
        self.compatible_reader_button.setEnabled(False)
        self._notice("正在本机启动兼容阅读器；阅读时请保持本软件运行…")

        def open_reader():
            if expected_sha256:
                from .storage import digest
                if digest(output) != expected_sha256:
                    raise ValueError("EPUB 内容与指定任务的最近导出记录不一致；请重新导出或单独打开该 EPUB。")
            from .reader import open_epub
            return open_epub(output)

        def finished(result):
            self._reader_opening = False
            self._reader_result = result
            self.compatible_reader_button.setEnabled(self.process.state() == QProcess.NotRunning)
            self._notice("兼容阅读器已在本机浏览器打开，无需联网。阅读时请保持本软件运行；页边是否保留取决于导出时的勾选设置。")

        def failed(message):
            self._reader_opening = False
            self.compatible_reader_button.setEnabled(self.process.state() == QProcess.NotRunning)
            if message == "File is not a zip file":
                message = "所选文件不是可读取的 EPUB 压缩包（File is not a zip file）。"
            elif "No module named" in message:
                message = "阅读器组件不完整，请重新解压完整应用包。" + message
            self._error("兼容阅读器无法打开：" + message)

        self._run_background(open_reader, finished, failed)

    def open_startup_paths(self, job_path=None, reader_path=None):
        """Open explicitly supplied local paths; never start a conversion."""
        try:
            expected_sha256 = None
            if job_path is not None:
                job_path = Path(job_path).expanduser().resolve()
                if not job_path.is_dir() or not (job_path / "job.json").is_file():
                    raise ValueError("指定任务目录不存在或缺少 job.json。")
            if reader_path is not None:
                reader_path = Path(reader_path).expanduser().resolve()
                if not reader_path.is_file() or reader_path.suffix.lower() != ".epub":
                    raise ValueError("指定 EPUB 文件不存在或扩展名不是 .epub。")
                if job_path is not None:
                    last_export = job_path / "last-export.json"
                    if not last_export.is_file():
                        raise ValueError("指定任务没有导出记录；未打开阅读器。")
                    record = json.loads(last_export.read_text(encoding="utf-8"))
                    if (not record.get("output") or Path(record["output"]).resolve() != reader_path
                            or not isinstance(record.get("sha256"), str) or len(record["sha256"]) != 64):
                        raise ValueError("指定 EPUB 与任务最近的导出记录不匹配；可不带 --open-job 单独打开 EPUB。")
                    expected_sha256 = record["sha256"]
            if job_path is not None:
                if not self.load_task(job_path):
                    return
                if reader_path is not None and record.get("source_sha256") != self.document.get("source_sha256"):
                    raise ValueError("指定 EPUB 的源文档摘要与任务不一致；未打开阅读器。")
            if reader_path is not None:
                self._last_output = reader_path
                self.open_result_button.setEnabled(True)
                self.compatible_reader_button.setEnabled(True)
                self.open_compatible_reader(expected_sha256)
        except Exception as exc:
            self._error("启动时未打开指定内容：" + str(exc))

    def open_report(self):
        if self._last_report and self._last_report.is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._last_report.resolve())))
            return
        if not self.job_dir:
            return
        for name in ("report.html", "quality-report.html", "report.json", "quality-report.json"):
            path = self.job_dir / name
            if path.is_file():
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve())))
                return
        self._notice("尚无独立报告文件，请查看「疑点与缺页」标签，或完成一次导出。")

    def _set_status_text(self, message):
        # Missing-runtime and native DLL errors can contain hundreds of paths.
        # Keep the task controls visible; the full message remains in the log.
        text = str(message)
        self.status_label.setText(text if len(text) <= 100 else text[:100] + "…（完整信息见日志）")
        self.status_label.setToolTip(text)

    def _notice(self, message):
        self._set_status_text(message)
        self.log.appendPlainText(message)

    def _error(self, message):
        self._set_status_text("操作未完成：" + message)
        self.log.appendPlainText("错误：" + message)

    def closeEvent(self, event):
        if self._resource_busy:
            self._notice("资源校验、导入或下载仍在后台进行，请完成后再关闭；不会中断正在写入的运行包。")
            event.ignore()
            return
        if self.process.state() != QProcess.NotRunning:
            answer = QMessageBox.question(self, "转换仍在进行", "关闭窗口将立即终止转换。已完成页面会保留，当前页可在重新打开任务后重试。现在关闭？", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer != QMessageBox.Yes:
                event.ignore()
                return
            self.process.kill()
            self.process.waitForFinished(3000)
        if self._threads:
            self._notice("资源准备或原页渲染仍在进行，请稍后关闭窗口。")
            event.ignore()
            return
        self.save_current_edit()
        if self._dirty:
            event.ignore()
            return
        self._cleanup_password()
        event.accept()


def main(argv=None):
    import argparse
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] == "gui":
        arguments = arguments[1:]
    parser = argparse.ArgumentParser(description="本地 PDF 转 EPUB 桌面界面")
    parser.add_argument("--open-job", type=Path, help="重新打开指定任务，不自动转换或导出")
    parser.add_argument("--open-reader", type=Path, help="在本机兼容阅读器打开指定 EPUB；阅读时保持应用运行")
    args = parser.parse_args(arguments)
    app = QApplication.instance() or QApplication([sys.argv[0]])
    app.setApplicationName("本地书坊")
    app.setOrganizationName("Local EPUB")
    window = Window()
    window.show()
    if args.open_job is not None or args.open_reader is not None:
        QTimer.singleShot(0, lambda: window.open_startup_paths(args.open_job, args.open_reader))
    return app.exec()
