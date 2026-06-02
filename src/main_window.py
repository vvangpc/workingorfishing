"""主窗口：仅 概览 / 设置 两 Tab；规则、AI 判断、统计 都是弹出卡片，几何持久化。"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCloseEvent, QHideEvent, QIcon
from PySide6.QtWidgets import (
    QDialog,
    QMainWindow,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .ai_classifier import AIClassifier
from .classifier import Classifier
from .paths import APP_DISPLAY_NAME, icon_file
from .settings import Settings, restore_geometry, save_geometry
from .stats_window import StatsTab
from .storage import Storage
from .widgets.ai_tab import AITab
from .widgets.overview_tab import OverviewTab
from .widgets.rules_tab import RulesTab
from .widgets.settings_tab import SettingsTab


class _CardDialog(QDialog):
    """弹出卡片：隐藏 / 关闭时把几何保存到 settings.window_geometry[name]。"""

    def __init__(self, name: str, settings: Settings, title: str, parent=None):
        super().__init__(parent)
        self._geom_name = name
        self._settings = settings
        self.setWindowTitle(title)
        self.setWindowFlags(
            Qt.Window
            | Qt.WindowMinMaxButtonsHint
            | Qt.WindowCloseButtonHint
        )

    def restore_or_default(self, default_w: int, default_h: int) -> None:
        if not restore_geometry(self._settings, self._geom_name, self):
            self.resize(default_w, default_h)

    def hideEvent(self, e: QHideEvent) -> None:
        save_geometry(self._settings, self._geom_name, self)
        super().hideEvent(e)


class MainWindow(QMainWindow):
    settings_changed = Signal(Settings)
    rules_changed = Signal()
    pause_requested = Signal()

    def __init__(
        self,
        storage: Storage,
        classifier: Classifier,
        ai_classifier: AIClassifier,
        settings: Settings,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.setWindowTitle(APP_DISPLAY_NAME)
        _ico = QIcon(str(icon_file()))
        if not _ico.isNull():
            self.setWindowIcon(_ico)

        self._storage = storage
        self._classifier = classifier
        self._ai = ai_classifier
        self._settings = settings

        self.overview = OverviewTab(
            storage, classifier, ai_classifier,
            settings.sample_interval_seconds,
            settings,
        )
        self.settings_tab = SettingsTab(settings)

        self.rules_widget = RulesTab(classifier, ai_classifier=ai_classifier)
        self.ai_widget = AITab(settings, ai_classifier)
        self.stats_widget = StatsTab(
            storage, classifier, settings.sample_interval_seconds,
            ai_classifier=ai_classifier,
        )

        self._rules_dialog: Optional[_CardDialog] = None
        self._ai_dialog: Optional[_CardDialog] = None
        self._stats_dialog: Optional[_CardDialog] = None

        tabs = QTabWidget()
        tabs.addTab(self.overview, "概览")
        tabs.addTab(self.settings_tab, "设置")
        self.setCentralWidget(tabs)
        self._tabs = tabs

        # 恢复主窗口几何
        if not restore_geometry(settings, "main", self):
            self.resize(500, 420)

        # --- 信号转发 ---
        self.overview.pause_requested.connect(self.pause_requested)
        self.overview.rules_changed.connect(self._on_rules_changed_internal)
        self.overview.open_ai_settings_requested.connect(self.show_ai_dialog)

        self.rules_widget.rules_changed.connect(self._on_rules_changed_internal)
        self.ai_widget.settings_changed.connect(self.settings_changed)
        self.settings_tab.settings_changed.connect(self.settings_changed)

        self.settings_tab.open_rules_requested.connect(self.show_rules_dialog)
        self.settings_tab.open_ai_requested.connect(self.show_ai_dialog)
        self.settings_tab.open_stats_requested.connect(self.show_stats_dialog)
        self.settings_tab.open_commentary_requested.connect(self.show_commentary_dialog)

    # --- 弹出卡片（懒创建 + 几何持久化） ---

    def _build_dialog(
        self, name: str, title: str, widget: QWidget, default: tuple[int, int]
    ) -> _CardDialog:
        d = _CardDialog(name, self._settings, title, self)
        layout = QVBoxLayout(d)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addWidget(widget)
        d.restore_or_default(*default)
        return d

    def show_rules_dialog(self) -> None:
        if self._rules_dialog is None:
            self._rules_dialog = self._build_dialog(
                "rules", "规则管理", self.rules_widget, (980, 620)
            )
        self._rules_dialog.show()
        self._rules_dialog.raise_()
        self._rules_dialog.activateWindow()

    def show_ai_dialog(self) -> None:
        if self._ai_dialog is None:
            self._ai_dialog = self._build_dialog(
                "ai", "AI 判断", self.ai_widget, (820, 720)
            )
        self._ai_dialog.show()
        self._ai_dialog.raise_()
        self._ai_dialog.activateWindow()

    def show_stats_dialog(self) -> None:
        if self._stats_dialog is None:
            self._stats_dialog = self._build_dialog(
                "stats", "活动统计", self.stats_widget, (1100, 720)
            )
        self.stats_widget.refresh_all()
        self._stats_dialog.show()
        self._stats_dialog.raise_()
        self._stats_dialog.activateWindow()

    def show_commentary_dialog(self) -> None:
        from .widgets.commentary_dialog import CommentaryDialog
        dlg = CommentaryDialog(self._settings, self._ai, self._storage, self)
        dlg.settings_changed.connect(self.settings_changed)
        dlg.exec()

    # --- 主窗口控制 ---

    def _on_rules_changed_internal(self) -> None:
        self.rules_widget.refresh()
        self.overview.refresh_pending_count()
        self.overview.refresh_bars()
        self.stats_widget.refresh_all()
        self.rules_changed.emit()

    def closeEvent(self, e: QCloseEvent) -> None:
        save_geometry(self._settings, "main", self)
        e.ignore()
        self.hide()

    def show_and_raise(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()
        # 主窗口隐藏时概览不再刷新（性能优化），show 时补齐一次
        self.overview.refresh_bars()
        self.overview.refresh_pending_count()
        # 每次打开窗口刷新今日点评（后台隐藏时不触发）
        self.overview.refresh_commentary()

    def jump_to_overview(self) -> None:
        self._tabs.setCurrentIndex(0)

    def jump_to_settings(self) -> None:
        self._tabs.setCurrentIndex(1)

    def jump_to_rules(self) -> None:
        self.show_rules_dialog()

    def jump_to_ai(self) -> None:
        self.show_ai_dialog()

    def jump_to_stats(self) -> None:
        self.show_stats_dialog()
