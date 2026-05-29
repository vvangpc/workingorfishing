"""规则管理 Tab：GUI 增删改查规则（美化版）。"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..classifier import SOURCE_AI, SOURCE_BUILTIN, Classifier
from .classify_dialog import CATEGORY_LABELS, ClassifyDialog


CATEGORY_COLORS = {
    "work": QColor("#2ecc71"),
    "fishing": QColor("#e74c3c"),
    "neutral": QColor("#3498db"),
}
CATEGORY_BG = {
    "work": QColor("#eafaf1"),
    "fishing": QColor("#fdedec"),
    "neutral": QColor("#ebf5fb"),
}

SOURCE_LABELS = {
    SOURCE_BUILTIN: "内置",
    SOURCE_AI: "AI",
    "user": "我",
}

_TAB_QSS = """
QWidget#RulesTabRoot { background-color: #f4f6f7; }
QGroupBox {
    font-weight: bold;
    border: 1px solid #d0d6d9;
    border-radius: 6px;
    margin-top: 12px;
    padding-top: 8px;
    background-color: white;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
    color: #2c3e50;
    background-color: #f4f6f7;
}
QLineEdit {
    padding: 5px 8px;
    border: 1px solid #d0d6d9;
    border-radius: 4px;
    background: white;
}
QLineEdit:focus { border: 1px solid #3498db; }
"""

_PRIMARY_BTN_QSS = (
    "QPushButton {"
    " padding: 7px 16px;"
    " border-radius: 4px;"
    " background-color: #3498db;"
    " color: white;"
    " border: none;"
    " font-weight: bold;"
    "}"
    "QPushButton:hover { background-color: #2980b9; }"
)
_DEFAULT_BTN_QSS = (
    "QPushButton {"
    " padding: 7px 14px;"
    " border-radius: 4px;"
    " background-color: #ecf0f1;"
    " color: #2c3e50;"
    " border: 1px solid #d0d6d9;"
    "}"
    "QPushButton:hover { background-color: #d6dbdf; }"
)


class RulesTab(QWidget):
    rules_changed = Signal()

    def __init__(
        self,
        classifier: Classifier,
        ai_classifier=None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._classifier = classifier
        self._ai = ai_classifier
        self.setObjectName("RulesTabRoot")
        self.setStyleSheet(_TAB_QSS)

        # --- 顶部头条：标题 + 数量徽章 + 搜索 + 主操作按钮 ---
        title = QLabel("规则管理")
        title.setStyleSheet("font: bold 16px '微软雅黑'; color: #2c3e50;")
        self._count_label = QLabel("0 条")
        self._count_label.setStyleSheet(
            "background-color: #ecf0f1; color: #2c3e50;"
            "border-radius: 8px; padding: 2px 10px; font-size: 11px;"
        )

        self._search = QLineEdit()
        self._search.setPlaceholderText("按进程 / 标题 / URL / 备注过滤…")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(lambda _t: self.refresh())
        self._search.setMaximumWidth(280)

        btn_add = QPushButton("+ 新建规则")
        btn_add.setStyleSheet(_PRIMARY_BTN_QSS)
        btn_add.clicked.connect(self._on_add)
        btn_del = QPushButton("删除选中")
        btn_del.setStyleSheet(_DEFAULT_BTN_QSS)
        btn_del.clicked.connect(self._on_delete)
        btn_reload = QPushButton("重新加载")
        btn_reload.setStyleSheet(_DEFAULT_BTN_QSS)
        btn_reload.clicked.connect(self._reload_classifier_and_view)

        header = QHBoxLayout()
        header.setSpacing(8)
        header.addWidget(title)
        header.addWidget(self._count_label)
        header.addSpacing(12)
        header.addWidget(self._search)
        header.addStretch(1)
        header.addWidget(btn_reload)
        header.addWidget(btn_del)
        header.addWidget(btn_add)

        # --- 表格 ---
        self._table = QTableWidget(0, 9)
        self._table.setHorizontalHeaderLabels(
            ["启用", "优先级", "类别", "进程", "标题正则", "URL 正则", "来源", "备注", "操作"]
        )
        # 所有列均可拖拽调整宽度（含「标题正则」列，原先 Stretch 无法手动调整）
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self._table.horizontalHeader().setStretchLastSection(False)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.setShowGrid(False)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(28)
        self._table.setStyleSheet(
            "QTableWidget { background-color: white; gridline-color: transparent; }"
            "QTableWidget::item:selected { background-color: #d4e6f1; color: #000; }"
            "QHeaderView::section {"
            " background-color: #f4f6f7; color: #333; padding: 6px;"
            " border: none; border-bottom: 1px solid #d0d6d9;"
            " font-weight: bold;"
            "}"
        )

        for col, w in {0: 48, 1: 60, 2: 70, 3: 130, 4: 220, 5: 200, 6: 50, 7: 130, 8: 110}.items():
            self._table.setColumnWidth(col, w)

        # --- 测试匹配区 ---
        test_box = QGroupBox("测试匹配（输入活动，看哪条规则会命中）")
        test_layout = QHBoxLayout(test_box)
        self._test_process = QLineEdit()
        self._test_process.setPlaceholderText("进程，如 chrome.exe")
        self._test_title = QLineEdit()
        self._test_title.setPlaceholderText("窗口标题")
        self._test_url = QLineEdit()
        self._test_url.setPlaceholderText("URL")
        btn_test = QPushButton("测试")
        btn_test.setStyleSheet(_DEFAULT_BTN_QSS)
        btn_test.clicked.connect(self._on_test)
        self._test_result = QLabel("—")
        test_layout.addWidget(QLabel("进程:"))
        test_layout.addWidget(self._test_process, 1)
        test_layout.addWidget(QLabel("标题:"))
        test_layout.addWidget(self._test_title, 2)
        test_layout.addWidget(QLabel("URL:"))
        test_layout.addWidget(self._test_url, 2)
        test_layout.addWidget(btn_test)
        test_layout.addWidget(self._test_result, 1)

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(6)
        root.addLayout(header)
        root.addWidget(self._table, 1)
        root.addWidget(test_box)

        self.refresh()

    # --- 渲染 ---

    def refresh(self) -> None:
        rules = self._classifier.get_rules()
        # 搜索过滤
        q = self._search.text().strip().lower()
        if q:
            def hit(r):
                return any(
                    q in (getattr(r, f) or "").lower()
                    for f in ("process", "title_regex", "url_regex", "note")
                )
            rules = [r for r in rules if hit(r)]

        self._table.setRowCount(0)
        for r in rules:
            row = self._table.rowCount()
            self._table.insertRow(row)

            cb = QCheckBox()
            cb.setChecked(r.enabled)
            cb.stateChanged.connect(lambda state, rid=r.id: self._on_toggle(rid, state == Qt.Checked))
            wrap = QWidget()
            l = QHBoxLayout(wrap)
            l.setContentsMargins(0, 0, 0, 0)
            l.addWidget(cb, 0, Qt.AlignCenter)
            self._table.setCellWidget(row, 0, wrap)

            self._table.setItem(row, 1, QTableWidgetItem(str(r.priority)))

            cat_color = CATEGORY_COLORS.get(r.category, QColor("black"))
            cat_item = QTableWidgetItem(f"● {CATEGORY_LABELS.get(r.category, r.category)}")
            cat_item.setForeground(cat_color)
            f = cat_item.font(); f.setBold(True); cat_item.setFont(f)
            self._table.setItem(row, 2, cat_item)

            self._table.setItem(row, 3, QTableWidgetItem(r.process or ""))
            self._table.setItem(row, 4, QTableWidgetItem(r.title_regex or ""))
            self._table.setItem(row, 5, QTableWidgetItem(r.url_regex or ""))
            src_item = QTableWidgetItem(SOURCE_LABELS.get(r.source, r.source))
            if r.source == SOURCE_AI:
                src_item.setForeground(QColor("#8e44ad"))
            elif r.source == SOURCE_BUILTIN:
                src_item.setForeground(QColor("#7f8c8d"))
            self._table.setItem(row, 6, src_item)
            self._table.setItem(row, 7, QTableWidgetItem(r.note or ""))

            op_w = QWidget()
            op_l = QHBoxLayout(op_w)
            op_l.setContentsMargins(2, 0, 2, 0)
            btn_edit = QPushButton("编辑")
            btn_edit.setMaximumHeight(22)
            btn_edit.clicked.connect(lambda _=False, rid=r.id: self._on_edit(rid))
            btn_del = QPushButton("删")
            btn_del.setFixedWidth(28)
            btn_del.setMaximumHeight(22)
            btn_del.clicked.connect(lambda _=False, rid=r.id: self._on_delete_one(rid))
            op_l.addWidget(btn_edit)
            op_l.addWidget(btn_del)
            self._table.setCellWidget(row, 8, op_w)

            self._table.item(row, 1).setData(Qt.UserRole, r.id)

        # 数量徽章：显示当前可见 / 总数
        total = len(self._classifier.get_rules())
        if q:
            self._count_label.setText(f"{len(rules)} / {total}")
        else:
            self._count_label.setText(f"{total} 条")

    def _id_at(self, row: int) -> Optional[str]:
        item = self._table.item(row, 1)
        return item.data(Qt.UserRole) if item else None

    # --- 动作 ---

    def _on_toggle(self, rule_id: str, enabled: bool) -> None:
        self._classifier.set_enabled(rule_id, enabled)
        self.rules_changed.emit()

    def _on_add(self) -> None:
        dlg = ClassifyDialog(
            title="新建规则",
            allow_skip_rule=False,
            ai_classifier=self._ai,
            parent=self,
        )
        if dlg.exec() == ClassifyDialog.Accepted:
            self._classifier.add_rule(dlg.result_data().rule)
            self.refresh()
            self.rules_changed.emit()

    def _on_edit(self, rule_id: str) -> None:
        rule = self._classifier.get_rule(rule_id)
        if not rule:
            return
        dlg = ClassifyDialog(
            title="编辑规则",
            rule=rule,
            allow_skip_rule=False,
            parent=self,
        )
        if dlg.exec() == ClassifyDialog.Accepted:
            self._classifier.update_rule(dlg.result_data().rule)
            self.refresh()
            self.rules_changed.emit()

    def _on_delete_one(self, rule_id: str) -> None:
        if QMessageBox.question(self, "删除规则", "确认删除这条规则？") != QMessageBox.Yes:
            return
        self._classifier.delete_rule(rule_id)
        self.refresh()
        self.rules_changed.emit()

    def _on_delete(self) -> None:
        rows = sorted({i.row() for i in self._table.selectedIndexes()}, reverse=True)
        if not rows:
            return
        if QMessageBox.question(self, "删除规则", f"确认删除选中的 {len(rows)} 条规则？") != QMessageBox.Yes:
            return
        for row in rows:
            rid = self._id_at(row)
            if rid:
                self._classifier.delete_rule(rid, save=False)
        self._classifier.save()
        self.refresh()
        self.rules_changed.emit()

    def _on_test(self) -> None:
        p = self._test_process.text().strip() or None
        t = self._test_title.text().strip() or None
        u = self._test_url.text().strip() or None
        rule = self._classifier.find_matching_rule(p, t, u)
        if rule is None:
            self._test_result.setText("<i style='color:#888;'>无匹配，归 unknown</i>")
            return
        label = CATEGORY_LABELS.get(rule.category, rule.category)
        color = CATEGORY_COLORS.get(rule.category, QColor("black")).name()
        status = "" if rule.enabled else "（已禁用）"
        self._test_result.setText(
            f"<b style='color:{color}'>● {label}</b>{status}  ·  优先级 {rule.priority}  ·  {rule.note or rule.id[:8]}"
        )

    def _reload_classifier_and_view(self) -> None:
        self._classifier.reload(force=True)
        self.refresh()
        self.rules_changed.emit()
