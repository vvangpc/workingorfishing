"""待确定活动弹窗：列出未匹配规则的活动 + 自动规则 + 刷新（美化版）。"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..ai_classifier import AIClassifier, AISuggestion
from ..classifier import SOURCE_AI, Classifier, Rule
from ..storage import Storage
from .classify_dialog import CATEGORY_LABELS, ClassifyDialog


_DIALOG_QSS = """
QDialog { background-color: #f4f6f7; }
QLabel { color: #34495e; }
QTableWidget {
    background-color: white;
    border: 1px solid #d0d6d9;
    border-radius: 4px;
    gridline-color: transparent;
    alternate-background-color: #fafbfc;
}
QTableWidget::item { padding: 4px 6px; }
QTableWidget::item:selected { background-color: #d4e6f1; color: #000; }
QHeaderView::section {
    background-color: #f4f6f7;
    color: #2c3e50;
    padding: 8px 6px;
    border: none;
    border-bottom: 1px solid #d0d6d9;
    font-weight: bold;
}
"""

_PRIMARY_BTN = (
    "QPushButton { padding: 7px 18px; border-radius: 4px;"
    " background-color: #3498db; color: white; border: none; font-weight: bold; }"
    "QPushButton:hover { background-color: #2980b9; }"
)
_DEFAULT_BTN = (
    "QPushButton { padding: 7px 16px; border-radius: 4px;"
    " background-color: #ecf0f1; color: #2c3e50; border: 1px solid #d0d6d9; }"
    "QPushButton:hover { background-color: #d6dbdf; }"
)
_AI_BTN = (
    "QPushButton { padding: 7px 18px; border-radius: 4px;"
    " background-color: #8e44ad; color: white; border: none; font-weight: bold; }"
    "QPushButton:hover { background-color: #7d3c98; }"
)


def _category_pill(cat: str, text: str) -> str:
    colors = {
        "work": ("#2ecc71", "#eafaf1"),
        "fishing": ("#e74c3c", "#fdedec"),
        "neutral": ("#3498db", "#ebf5fb"),
    }
    fg, bg = colors.get(cat, ("#2c3e50", "#ecf0f1"))
    return (
        "QPushButton {"
        " padding: 3px 10px;"
        f" background-color: {bg};"
        f" color: {fg};"
        " border: 1px solid " + fg + ";"
        " border-radius: 10px;"
        " font-weight: bold;"
        " font-size: 11px;"
        " min-width: 36px;"
        "}"
        "QPushButton:hover {"
        f" background-color: {fg};"
        " color: white;"
        "}"
    )


class PendingDialog(QDialog):
    rules_changed = Signal()
    open_ai_settings_requested = Signal()

    def __init__(
        self,
        storage: Storage,
        classifier: Classifier,
        ai_classifier: AIClassifier,
        suggestions: dict,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("待确定的活动")
        self.resize(1000, 580)
        self.setStyleSheet(_DIALOG_QSS)
        self._storage = storage
        self._classifier = classifier
        self._ai = ai_classifier
        self._suggestions = suggestions

        # --- 顶部 header ---
        title = QLabel("待确定")
        title.setStyleSheet("font: bold 17px '微软雅黑'; color: #2c3e50;")
        self._count_badge = QLabel("0 项")
        self._count_badge.setStyleSheet(
            "background-color: #fff4e0; color: #c0392b; font-weight: bold;"
            " border-radius: 9px; padding: 2px 12px; font-size: 11px;"
        )
        hint = QLabel(
            "<span style='color:#7f8c8d; font-size:11px;'>"
            "每行右侧的 工作 / 摸鱼 / 中立 按钮可直接归类（并生成规则 + 回填历史）"
            "</span>"
        )
        header = QHBoxLayout()
        header.setSpacing(8)
        header.addWidget(title)
        header.addWidget(self._count_badge)
        header.addStretch(1)
        header.addWidget(hint)

        # --- 表格 ---
        self._table = QTableWidget(0, 6)
        self._table.setHorizontalHeaderLabels(
            ["进程", "标题", "URL", "次数", "AI 建议", "操作"]
        )
        # 所有列均可拖拽调整宽度（原先「标题」列 Stretch 无法手动调整）
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self._table.horizontalHeader().setStretchLastSection(False)
        for col, w in {0: 130, 1: 240, 2: 240, 3: 56, 4: 130, 5: 200}.items():
            self._table.setColumnWidth(col, w)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.setShowGrid(False)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(30)

        # 空状态
        self._empty_label = QLabel(
            "<div style='text-align:center; padding:30px;'>"
            "<span style='font-size:28px;'>✓</span><br>"
            "<span style='color:#7f8c8d;'>暂无待确定的活动</span><br>"
            "<span style='color:#bdc3c7; font-size:11px;'>"
            "等下一次采样时未匹配规则的活动会出现在这里</span>"
            "</div>"
        )
        self._empty_label.setAlignment(Qt.AlignCenter)
        self._empty_label.setStyleSheet("background-color: white; border: 1px solid #d0d6d9; border-radius: 4px;")
        self._empty_label.hide()

        # --- 底部按钮 ---
        btn_row = QHBoxLayout()
        btn_close = QPushButton("关闭")
        btn_close.setStyleSheet(_DEFAULT_BTN)
        btn_close.clicked.connect(self.accept)
        btn_row.addWidget(btn_close)
        btn_row.addStretch(1)
        btn_refresh = QPushButton("⟳ 刷新列表")
        btn_refresh.setStyleSheet(_DEFAULT_BTN)
        btn_refresh.clicked.connect(self.refresh_pending)
        self._btn_auto = QPushButton("✨ 自动规则（AI）")
        self._btn_auto.setStyleSheet(_AI_BTN)
        self._btn_auto.setToolTip("调用 AI 对所有待确定活动逐条自动生成规则并回填历史")
        self._btn_auto.clicked.connect(self._on_auto_rule)
        btn_row.addWidget(btn_refresh)
        btn_row.addWidget(self._btn_auto)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(8)
        root.addLayout(header)
        root.addWidget(self._table, 1)
        root.addWidget(self._empty_label, 1)
        root.addLayout(btn_row)

        self._ai.suggestion_ready.connect(self._on_suggestion)
        self.refresh_pending()

    # --- 表格 ---

    def refresh_pending(self) -> None:
        rows = self._storage.pending_unknown(limit=200)
        self._count_badge.setText(f"{len(rows)} 项")
        if not rows:
            self._table.setRowCount(0)
            self._table.hide()
            self._empty_label.show()
            return
        self._empty_label.hide()
        self._table.show()

        self._table.setRowCount(0)
        for r in rows:
            row = self._table.rowCount()
            self._table.insertRow(row)

            proc_item = QTableWidgetItem(r["process"] or "—")
            self._table.setItem(row, 0, proc_item)
            self._table.setItem(row, 1, QTableWidgetItem((r["sample_title"] or "")[:120]))
            url_item = QTableWidgetItem((r["sample_url"] or "")[:120])
            if r["sample_url"]:
                url_item.setForeground(QColor("#3498db"))
            self._table.setItem(row, 2, url_item)

            count_item = QTableWidgetItem(str(r["count"]))
            count_item.setTextAlignment(Qt.AlignCenter)
            count_item.setForeground(QColor("#7f8c8d"))
            self._table.setItem(row, 3, count_item)

            key = self._sug_key(r["process"], r["sample_url"], r["sample_title"])
            sug = self._suggestions.get(key)
            if sug:
                sug_label = f"{CATEGORY_LABELS.get(sug.category, sug.category)} · {sug.reason[:24]}"
                sug_item = QTableWidgetItem(sug_label)
                sug_item.setForeground(QColor("#8e44ad"))
                sug_item.setToolTip(sug.reason)
                self._table.setItem(row, 4, sug_item)
            else:
                self._table.setItem(row, 4, QTableWidgetItem(""))

            op_w = QWidget()
            op_l = QHBoxLayout(op_w)
            op_l.setContentsMargins(2, 1, 2, 1)
            op_l.setSpacing(4)
            for cat, label in (("work", "工作"), ("fishing", "摸鱼"), ("neutral", "中立")):
                btn = QPushButton(label)
                btn.setStyleSheet(_category_pill(cat, label))
                btn.setMaximumHeight(22)
                btn.clicked.connect(
                    lambda _=False, c=cat, rec=r: self._classify_record(c, rec)
                )
                op_l.addWidget(btn)
            op_l.addStretch(1)
            self._table.setCellWidget(row, 5, op_w)

    @staticmethod
    def _sug_key(process: Optional[str], url: Optional[str], title: Optional[str]) -> tuple:
        return ((process or "").lower(), (url or "")[:80] or (title or "")[:80])

    def _on_suggestion(self, sug: AISuggestion) -> None:
        key = self._sug_key(sug.process, sug.url, sug.title)
        self._suggestions[key] = sug
        self.refresh_pending()

    # --- 单条归类 ---

    def _classify_record(self, category: str, rec: dict) -> None:
        sug = self._suggestions.get(
            self._sug_key(rec["process"], rec["sample_url"], rec["sample_title"])
        )
        sample_process = rec["process"]
        sample_title = rec["sample_title"]
        sample_url = rec["sample_url"]

        dlg = ClassifyDialog(
            title="归类未知活动",
            sample_process=sample_process,
            sample_title=sample_title,
            sample_url=sample_url,
            ai_classifier=self._ai,
            parent=self,
        )
        idx = dlg._cat.findData(category)
        if idx >= 0:
            dlg._cat.setCurrentIndex(idx)
        if sug and sug.category == category:
            if sug.suggested_process:
                dlg._process.setText(sug.suggested_process)
            if sug.suggested_title_regex:
                dlg._title_re.setText(sug.suggested_title_regex)
            if sug.suggested_url_regex:
                dlg._url_re.setText(sug.suggested_url_regex)

        if dlg.exec() != ClassifyDialog.Accepted:
            return
        result = dlg.result_data()
        msgs = []
        if result.create_rule:
            self._classifier.add_rule(result.rule)
            msgs.append(f"已添加规则（{CATEGORY_LABELS.get(result.rule.category)}）")
            if result.backfill:
                n = self._storage.reclassify_by_rule(result.rule, only_unknown=True)
                msgs.append(f"回填 {n} 条历史 unknown 记录")
        else:
            if sample_process:
                n = self._storage.reclassify_by_process(
                    sample_process, category, only_unknown=True
                )
                msgs.append(f"回填 {n} 条 {sample_process} 的 unknown 记录")
        QMessageBox.information(self, "已应用", "\n".join(msgs) or "已应用。")
        self.refresh_pending()
        self.rules_changed.emit()

    # --- 自动规则 ---

    def _on_auto_rule(self) -> None:
        ai_settings = self._ai.settings
        if not ai_settings.api_key or not ai_settings.base_url:
            r = QMessageBox.question(
                self, "AI 尚未配置",
                "AI 判断需要先配置 Base URL 和 API Key。是否打开设置 → AI 判断？",
                QMessageBox.Yes | QMessageBox.No,
            )
            if r == QMessageBox.Yes:
                self.open_ai_settings_requested.emit()
                self.accept()
            return

        rows = self._storage.pending_unknown(limit=100)
        if not rows:
            QMessageBox.information(self, "无可处理", "当前没有待确定的未知活动。")
            return

        if QMessageBox.question(
            self, "自动规则",
            f"将对 {len(rows)} 条未确定活动逐条调用 AI 判断并生成规则。\n"
            "过程中可点击「取消」中断。继续？",
            QMessageBox.Yes | QMessageBox.No,
        ) != QMessageBox.Yes:
            return

        progress = QProgressDialog("准备…", "取消", 0, len(rows), self)
        progress.setWindowTitle("AI 自动规则")
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setValue(0)
        QApplication.processEvents()

        created = backfilled = skipped = errors = 0
        first_error_msg = ""

        for i, rec in enumerate(rows):
            if progress.wasCanceled():
                break
            tag = (rec["process"] or rec["sample_url"] or rec["sample_title"] or "?")[:40]
            progress.setLabelText(f"AI 判断 {i + 1}/{len(rows)}: {tag}")
            progress.setValue(i)
            QApplication.processEvents()

            sample = {
                "process": rec["process"],
                "title": rec["sample_title"],
                "url": rec["sample_url"],
            }
            try:
                sug, raw = self._ai.test(sample)
            except Exception as e:
                errors += 1
                if not first_error_msg:
                    first_error_msg = f"调用异常: {e}"
                continue
            if sug is None:
                errors += 1
                if not first_error_msg:
                    first_error_msg = (raw or "返回为空")[:200]
                continue

            self._suggestions[self._sug_key(rec["process"], rec["sample_url"], rec["sample_title"])] = sug

            proc_field = sug.suggested_process
            title_field = sug.suggested_title_regex
            url_field = sug.suggested_url_regex
            if not any((proc_field, title_field, url_field)):
                if rec["process"]:
                    proc_field = rec["process"]
                else:
                    skipped += 1
                    continue
            if url_field and proc_field and proc_field.lower() in {
                "chrome.exe", "msedge.exe", "firefox.exe", "brave.exe",
                "opera.exe", "vivaldi.exe",
            }:
                proc_field = None

            rule = Rule.new(
                category=sug.category,
                process=proc_field,
                title_regex=title_field,
                url_regex=url_field,
                priority=200,
                source=SOURCE_AI,
                note=(sug.reason or "AI auto")[:80],
            )
            self._classifier.add_rule(rule)
            n = self._storage.reclassify_by_rule(rule, only_unknown=True)
            created += 1
            backfilled += n

        progress.setValue(len(rows))
        summary = (
            f"创建规则 {created} 条\n"
            f"回填历史 {backfilled} 条\n"
            f"跳过 {skipped} 条（AI 未给出可用字段）\n"
            f"失败 {errors} 条"
        )
        if first_error_msg:
            summary += f"\n\n首条错误：{first_error_msg}"
        QMessageBox.information(self, "自动规则完成", summary)

        self.refresh_pending()
        self.rules_changed.emit()
