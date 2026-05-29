"""设置 Tab：工具卡片入口 / 采样 / 启动与界面 / 数据管理 / WebDAV。"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..settings import Settings, get_autostart, set_autostart
from .floating_settings_dialog import FloatingSettingsDialog


_TOOL_BTN_QSS = (
    "QPushButton {"
    " padding: 6px 14px;"
    " border-radius: 4px;"
    " background-color: #ecf0f1;"
    " border: 1px solid #d0d6d9;"
    " font-weight: bold;"
    "}"
    "QPushButton:hover { background-color: #d6dbdf; }"
)
_PRIMARY_BTN_QSS = (
    "QPushButton {"
    " padding: 6px 18px;"
    " border-radius: 4px;"
    " background-color: #3498db;"
    " color: white;"
    " border: none;"
    " font-weight: bold;"
    "}"
    "QPushButton:hover { background-color: #2980b9; }"
)
_DANGER_BTN_QSS = (
    "QPushButton {"
    " padding: 6px 14px;"
    " border-radius: 4px;"
    " background-color: #fce4e4;"
    " color: #c0392b;"
    " border: 1px solid #f5b7b1;"
    " font-weight: bold;"
    "}"
    "QPushButton:hover { background-color: #f5b7b1; color: white; }"
)


class SettingsTab(QWidget):
    settings_changed = Signal(Settings)
    open_rules_requested = Signal()
    open_ai_requested = Signal()
    open_stats_requested = Signal()
    request_export = Signal(str)        # 目标 zip 路径
    request_import = Signal(str)
    request_clear = Signal()
    request_webdav_push = Signal(str)   # "overwrite" / "merge"
    request_webdav_pull = Signal(str)   # "overwrite" / "merge"
    request_webdav_test = Signal()

    def __init__(self, settings: Settings, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._settings = settings

        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        # --- 工具卡片 ---
        tools_box = QGroupBox("工具")
        tools_layout = QHBoxLayout(tools_box)
        tools_layout.setContentsMargins(10, 8, 10, 8)
        for label, signal in (
            ("规则管理", self.open_rules_requested),
            ("AI 判断", self.open_ai_requested),
            ("统计", self.open_stats_requested),
        ):
            btn = QPushButton(label)
            btn.setMinimumHeight(34)
            btn.setStyleSheet(_TOOL_BTN_QSS)
            btn.clicked.connect(signal)
            tools_layout.addWidget(btn)
        layout.addWidget(tools_box)

        # --- 采样 ---
        sampling_box = QGroupBox("采样")
        sampling_layout = QHBoxLayout(sampling_box)
        sampling_layout.setContentsMargins(10, 8, 10, 8)
        self._interval = QSpinBox()
        self._interval.setRange(1, 600)
        self._interval.setSuffix(" 秒")
        self._interval.setValue(settings.sample_interval_seconds)
        self._interval.setFixedWidth(100)
        self._idle = QSpinBox()
        self._idle.setRange(30, 3600)
        self._idle.setSuffix(" 秒")
        self._idle.setValue(settings.idle_threshold_seconds)
        self._idle.setFixedWidth(100)
        sampling_layout.addWidget(QLabel("采样间隔"))
        sampling_layout.addWidget(self._interval)
        sampling_layout.addSpacing(16)
        sampling_layout.addWidget(QLabel("空闲阈值"))
        sampling_layout.addWidget(self._idle)
        sampling_layout.addStretch(1)
        layout.addWidget(sampling_box)

        # --- 启动与界面 ---
        ui_box = QGroupBox("启动与界面")
        ui_layout = QHBoxLayout(ui_box)
        ui_layout.setContentsMargins(10, 8, 10, 8)
        self._autostart = QCheckBox("开机自动启动")
        self._autostart.setChecked(get_autostart())
        btn_auto_pause = QPushButton("自动暂停设置…")
        btn_auto_pause.setToolTip("到点自动暂停、过点自动恢复（如午餐时段），避免误记录")
        btn_auto_pause.clicked.connect(self._open_auto_pause_dialog)
        btn_floating = QPushButton("悬浮窗设置…")
        btn_floating.setToolTip("启用 / 大小 / 透明度 / 字体颜色 / 鼠标穿透")
        btn_floating.clicked.connect(self._open_floating_dialog)
        ui_layout.addWidget(self._autostart)
        ui_layout.addStretch(1)
        ui_layout.addWidget(btn_auto_pause)
        ui_layout.addWidget(btn_floating)
        layout.addWidget(ui_box)

        # --- 数据管理 ---
        data_box = QGroupBox("数据管理")
        data_layout = QVBoxLayout(data_box)
        data_layout.setContentsMargins(10, 8, 10, 8)
        data_layout.setSpacing(6)

        data_btn_row = QHBoxLayout()
        btn_export = QPushButton("导出数据…")
        btn_export.setToolTip("将活动数据库 + 规则 + 设置 一并打包为 zip")
        btn_export.clicked.connect(self._on_export)
        btn_import = QPushButton("导入数据…")
        btn_import.setToolTip("从之前导出的 zip 还原")
        btn_import.clicked.connect(self._on_import)
        btn_clear = QPushButton("清除所有统计数据…")
        btn_clear.setStyleSheet(_DANGER_BTN_QSS)
        btn_clear.clicked.connect(self._on_clear)
        data_btn_row.addWidget(btn_export)
        data_btn_row.addWidget(btn_import)
        data_btn_row.addStretch(1)
        data_btn_row.addWidget(btn_clear)
        data_layout.addLayout(data_btn_row)
        layout.addWidget(data_box)

        # --- WebDAV ---
        webdav_box = QGroupBox("WebDAV 同步（所有数据）")
        webdav_layout = QVBoxLayout(webdav_box)
        webdav_layout.setContentsMargins(10, 8, 10, 8)
        webdav_layout.setSpacing(6)

        webdav_form = QFormLayout()
        self._wd_url = QLineEdit(settings.webdav.url)
        self._wd_url.setPlaceholderText("https://dav.example.com/path/")
        self._wd_user = QLineEdit(settings.webdav.username)
        self._wd_pass = QLineEdit(settings.webdav.password)
        self._wd_pass.setEchoMode(QLineEdit.Password)
        webdav_form.addRow("地址", self._wd_url)
        webdav_form.addRow("用户名", self._wd_user)
        webdav_form.addRow("密码", self._wd_pass)
        webdav_layout.addLayout(webdav_form)

        wd_btn_row = QHBoxLayout()
        btn_test = QPushButton("测试连接")
        btn_test.clicked.connect(self._on_webdav_test)
        btn_push = QPushButton("推送到云端")
        btn_push.clicked.connect(self._on_webdav_push)
        btn_pull = QPushButton("从云端拉取")
        btn_pull.clicked.connect(self._on_webdav_pull)
        wd_btn_row.addWidget(btn_test)
        wd_btn_row.addStretch(1)
        wd_btn_row.addWidget(btn_pull)
        wd_btn_row.addWidget(btn_push)
        webdav_layout.addLayout(wd_btn_row)

        self._wd_status = QLabel(self._format_sync_status())
        self._wd_status.setStyleSheet("color: #888; font-size: 11px;")
        webdav_layout.addWidget(self._wd_status)

        layout.addWidget(webdav_box)

        layout.addStretch(1)

        # --- 保存 ---
        btn_save = QPushButton("保存设置")
        btn_save.setMinimumWidth(120)
        btn_save.setStyleSheet(_PRIMARY_BTN_QSS)
        btn_save.clicked.connect(self._on_save)
        save_row = QHBoxLayout()
        save_row.addStretch(1)
        save_row.addWidget(btn_save)
        layout.addLayout(save_row)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(inner)
        scroll.setFrameShape(QScrollArea.NoFrame)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(scroll)

    # --- 同步状态显示 ---

    def _format_sync_status(self) -> str:
        wd = self._settings.webdav
        from datetime import datetime as _dt
        parts = []
        if wd.last_push:
            parts.append(f"上次推送 {_dt.fromtimestamp(wd.last_push).strftime('%Y-%m-%d %H:%M')}")
        if wd.last_pull:
            parts.append(f"上次拉取 {_dt.fromtimestamp(wd.last_pull).strftime('%Y-%m-%d %H:%M')}")
        if not parts:
            return "尚未同步"
        return "  ·  ".join(parts)

    def refresh_sync_status(self) -> None:
        self._wd_status.setText(self._format_sync_status())

    # --- 重新加载 ---

    def reload_from_settings(self) -> None:
        s = self._settings
        self._interval.setValue(s.sample_interval_seconds)
        self._idle.setValue(s.idle_threshold_seconds)
        self._autostart.setChecked(get_autostart())
        self._wd_url.setText(s.webdav.url)
        self._wd_user.setText(s.webdav.username)
        self._wd_pass.setText(s.webdav.password)
        self.refresh_sync_status()

    def _open_floating_dialog(self) -> None:
        dlg = FloatingSettingsDialog(self._settings, self)
        dlg.settings_changed.connect(self.settings_changed)
        dlg.exec()

    def _open_auto_pause_dialog(self) -> None:
        from .auto_pause_dialog import AutoPauseDialog
        dlg = AutoPauseDialog(self._settings, self)
        dlg.settings_changed.connect(self.settings_changed)
        dlg.exec()

    # --- 数据按钮 ---

    def _on_export(self) -> None:
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getSaveFileName(
            self, "导出数据到 zip", "WorkingorFishing-backup.zip", "ZIP (*.zip)"
        )
        if path:
            self.request_export.emit(path)

    def _on_import(self) -> None:
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(
            self, "从 zip 导入数据", "", "ZIP (*.zip)"
        )
        if not path:
            return
        if QMessageBox.question(
            self, "确认导入",
            "导入会覆盖当前的活动数据 / 规则 / 设置。继续？",
            QMessageBox.Yes | QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        self.request_import.emit(path)

    def _on_clear(self) -> None:
        if QMessageBox.question(
            self, "清除统计数据",
            "确定要清除所有活动统计数据吗？\n规则和设置不会受影响，但所有历史记录将被永久删除。",
            QMessageBox.Yes | QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        self.request_clear.emit()

    # --- WebDAV ---

    def _flush_webdav_to_settings(self) -> None:
        """把当前输入框的值同步到 settings.webdav 但不落盘，由调用方触发保存。"""
        wd = self._settings.webdav
        wd.url = self._wd_url.text().strip()
        wd.username = self._wd_user.text().strip()
        wd.password = self._wd_pass.text()

    def _on_webdav_test(self) -> None:
        self._flush_webdav_to_settings()
        self._settings.save()
        self.request_webdav_test.emit()

    def _ask_sync_mode(
        self, title: str, overwrite_desc: str, merge_desc: str
    ) -> Optional[str]:
        """弹窗让用户选择 覆盖 / 合并 / 取消。返回 'overwrite' / 'merge' / None。"""
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setIcon(QMessageBox.Question)
        box.setText(f"选择{title}方式：")
        box.setInformativeText(f"覆盖：{overwrite_desc}\n\n合并：{merge_desc}")
        btn_ow = box.addButton("覆盖", QMessageBox.AcceptRole)
        btn_mg = box.addButton("合并", QMessageBox.AcceptRole)
        box.addButton("取消", QMessageBox.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked is btn_ow:
            return "overwrite"
        if clicked is btn_mg:
            return "merge"
        return None

    def _on_webdav_push(self) -> None:
        self._flush_webdav_to_settings()
        self._settings.save()
        mode = self._ask_sync_mode(
            "推送到云端",
            "用本地数据覆盖云端（云端原有未同步数据会被替换）。",
            "先把云端数据合并进本地，再上传汇总结果（双向不丢数据）。",
        )
        if mode:
            self.request_webdav_push.emit(mode)

    def _on_webdav_pull(self) -> None:
        self._flush_webdav_to_settings()
        self._settings.save()
        mode = self._ask_sync_mode(
            "从云端拉取",
            "用云端数据覆盖本地（本地未同步数据会丢失）。",
            "把云端数据合并到本地，保留本地已有记录和规则，不删除任何数据。",
        )
        if mode:
            self.request_webdav_pull.emit(mode)

    # --- 保存 ---

    def _on_save(self) -> None:
        s = self._settings
        s.sample_interval_seconds = self._interval.value()
        s.idle_threshold_seconds = self._idle.value()
        s.autostart = self._autostart.isChecked()
        self._flush_webdav_to_settings()
        s.save()
        try:
            set_autostart(s.autostart)
        except OSError as e:
            QMessageBox.warning(self, "自启设置失败", str(e))
        self.settings_changed.emit(s)
        QMessageBox.information(self, "已保存", "设置已应用。")
