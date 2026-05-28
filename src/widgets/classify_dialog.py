"""共享的"归类为…"对话框：新建规则 / 编辑规则 / 归类样本 / 重新归类 都复用。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..classifier import SOURCE_AI, SOURCE_USER, VALID_CATEGORIES, Rule

CATEGORY_LABELS = {
    "work": "工作",
    "fishing": "摸鱼",
    "neutral": "中立",
}
CATEGORY_COLORS = {
    "work": "#2ecc71",
    "fishing": "#e74c3c",
    "neutral": "#3498db",
}


@dataclass
class ClassifyResult:
    rule: Rule
    backfill: bool
    create_rule: bool


_DIALOG_QSS = """
QDialog { background-color: #f4f6f7; }
QGroupBox {
    font-weight: bold;
    border: 1px solid #d0d6d9;
    border-radius: 6px;
    margin-top: 14px;
    padding-top: 10px;
    background-color: white;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
    color: #2c3e50;
    background-color: #f4f6f7;
}
QLineEdit, QSpinBox, QComboBox, QPlainTextEdit {
    padding: 5px 8px;
    border: 1px solid #d0d6d9;
    border-radius: 4px;
    background: white;
}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus {
    border: 1px solid #3498db;
}
QLabel { color: #34495e; }
"""

_PRIMARY_BTN = (
    "QPushButton { padding: 7px 22px; border-radius: 4px;"
    " background-color: #3498db; color: white; border: none; font-weight: bold; }"
    "QPushButton:hover { background-color: #2980b9; }"
    "QPushButton:disabled { background-color: #bdc3c7; color: #ecf0f1; }"
)
_DEFAULT_BTN = (
    "QPushButton { padding: 7px 18px; border-radius: 4px;"
    " background-color: #ecf0f1; color: #2c3e50; border: 1px solid #d0d6d9; }"
    "QPushButton:hover { background-color: #d6dbdf; }"
)
_AI_BTN = (
    "QPushButton { padding: 6px 16px; border-radius: 4px;"
    " background-color: #8e44ad; color: white; border: none; font-weight: bold; }"
    "QPushButton:hover { background-color: #7d3c98; }"
)


class ClassifyDialog(QDialog):
    def __init__(
        self,
        *,
        title: str = "归类",
        sample_process: Optional[str] = None,
        sample_title: Optional[str] = None,
        sample_url: Optional[str] = None,
        pre_url_regex: Optional[str] = None,
        rule: Optional[Rule] = None,
        allow_skip_rule: bool = True,
        ai_classifier=None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(640, 640)
        self.setStyleSheet(_DIALOG_QSS)
        self._rule = rule
        self._editing = rule is not None
        self._allow_skip_rule = allow_skip_rule
        self._ai = ai_classifier

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)

        # --- 顶部标题 ---
        header_lbl = QLabel(title)
        header_lbl.setStyleSheet("font: bold 17px '微软雅黑'; color: #2c3e50;")
        root.addWidget(header_lbl)

        # --- 样本预览（仅新建归类时显示） ---
        if not self._editing and any((sample_process, sample_title, sample_url)):
            sample_card = QGroupBox("当前样本")
            sl = QVBoxLayout(sample_card)
            sl.setContentsMargins(12, 10, 12, 10)
            sl.setSpacing(4)
            for label, value in (
                ("进程", sample_process or "—"),
                ("标题", sample_title or "—"),
                ("URL", sample_url or "—"),
            ):
                row = QHBoxLayout()
                tag = QLabel(label)
                tag.setFixedWidth(40)
                tag.setStyleSheet("color: #7f8c8d; font-weight: bold; font-size: 12px;")
                val = QLabel(value)
                val.setWordWrap(True)
                val.setTextInteractionFlags(Qt.TextSelectableByMouse)
                val.setStyleSheet("color: #2c3e50;")
                row.addWidget(tag)
                row.addWidget(val, 1)
                sl.addLayout(row)
            root.addWidget(sample_card)

        # --- AI 辅助生成（仅新建 + 有 AI 时） ---
        if self._ai is not None and not self._editing:
            ai_card = QGroupBox("AI 自然语言生成")
            al = QVBoxLayout(ai_card)
            al.setContentsMargins(12, 10, 12, 10)
            al.setSpacing(6)
            top_row = QHBoxLayout()
            self._ai_desc = QLineEdit()
            self._ai_desc.setPlaceholderText("例：把 LinkedIn 都归为工作 / 钉钉相关都归为工作")
            self._ai_desc.returnPressed.connect(self._on_ai_generate)
            btn_gen = QPushButton("✨ AI 生成")
            btn_gen.setStyleSheet(_AI_BTN)
            btn_gen.setMinimumHeight(30)
            btn_gen.clicked.connect(self._on_ai_generate)
            top_row.addWidget(self._ai_desc, 1)
            top_row.addWidget(btn_gen)
            al.addLayout(top_row)
            self._ai_status = QLabel(
                "<span style='color:#7f8c8d; font-size:11px;'>"
                "需先在 设置 → AI 判断 配好 API Key。回车 / 点按钮触发；结果会预填到下方表单。"
                "</span>"
            )
            al.addWidget(self._ai_status)
            root.addWidget(ai_card)

        # --- 规则字段 ---
        form_card = QGroupBox("规则字段")
        form = QFormLayout(form_card)
        form.setContentsMargins(12, 12, 12, 10)
        form.setSpacing(8)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self._cat = QComboBox()
        for c in VALID_CATEGORIES:
            self._cat.addItem(f"● {CATEGORY_LABELS[c]}", c)
            self._cat.setItemData(
                self._cat.count() - 1,
                QColor(CATEGORY_COLORS[c]),
                Qt.ForegroundRole,
            )
        if rule:
            idx = self._cat.findData(rule.category)
            self._cat.setCurrentIndex(max(0, idx))
        form.addRow("类别", self._cat)

        self._create_rule = QCheckBox("把这次决定保存为一条规则（推荐）")
        self._create_rule.setChecked(True)
        if not allow_skip_rule:
            self._create_rule.setChecked(True)
            self._create_rule.setEnabled(False)
        form.addRow("", self._create_rule)

        self._process = QLineEdit(rule.process if rule else (sample_process or ""))
        self._process.setPlaceholderText("如 Code.exe；留空则不按进程匹配")
        form.addRow("进程名", self._process)

        self._title_re = QLineEdit(rule.title_regex if rule else "")
        self._title_re.setPlaceholderText("Python re，如 (?i).*bilibili.*")
        form.addRow("标题正则", self._title_re)

        self._url_re = QLineEdit(rule.url_regex if rule else "")
        self._url_re.setPlaceholderText("Python re，浏览器 URL 用")
        form.addRow("URL 正则", self._url_re)

        prio_row = QHBoxLayout()
        self._priority = QSpinBox()
        self._priority.setRange(1, 9999)
        self._priority.setValue(rule.priority if rule else 100)
        self._priority.setFixedWidth(110)
        prio_row.addWidget(self._priority)
        prio_hint = QLabel("<span style='color:#888; font-size:11px;'>越小越优先</span>")
        prio_row.addWidget(prio_hint)
        prio_row.addStretch(1)
        form.addRow("优先级", prio_row)

        self._note = QLineEdit(rule.note if rule else "")
        self._note.setPlaceholderText("可选，方便日后维护")
        form.addRow("备注", self._note)

        self._backfill = QCheckBox("同时把历史『未知』记录回填为此类别")
        self._backfill.setChecked(True)
        form.addRow("", self._backfill)

        root.addWidget(form_card, 1)

        # --- 底部按钮 ---
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        btn_cancel = QPushButton("取消")
        btn_cancel.setStyleSheet(_DEFAULT_BTN)
        btn_cancel.clicked.connect(self.reject)
        btn_ok = QPushButton("保存" if not self._editing else "更新")
        btn_ok.setStyleSheet(_PRIMARY_BTN)
        btn_ok.setDefault(True)
        btn_ok.clicked.connect(self._on_ok)
        btn_row.addWidget(btn_cancel)
        btn_row.addWidget(btn_ok)
        root.addLayout(btn_row)

        # 联动
        self._create_rule.toggled.connect(self._on_create_toggled)

        # 预填规则字段
        if pre_url_regex and not self._editing:
            self._url_re.setText(pre_url_regex)
            self._process.setText("")
        elif not self._editing:
            self._populate_suggestions(sample_process, sample_title, sample_url)

        self._on_create_toggled(self._create_rule.isChecked())

    # --- 建议预填 ---

    @staticmethod
    def _suggest_url_regex(url: str) -> Optional[str]:
        import re as _re
        m = _re.match(r"https?://([^/]+)", url)
        if not m:
            return None
        host = m.group(1)
        host = _re.sub(r"^www\.", "", host)
        return f"(?i).*{_re.escape(host)}.*"

    def _populate_suggestions(
        self,
        proc: Optional[str],
        title: Optional[str],
        url: Optional[str],
    ) -> None:
        if url:
            sug = self._suggest_url_regex(url)
            if sug:
                self._url_re.setText(sug)
                self._process.setText("")

    def _on_create_toggled(self, checked: bool) -> None:
        for w in (self._process, self._title_re, self._url_re, self._priority, self._note):
            w.setEnabled(checked)
        self._backfill.setEnabled(checked)

    # --- AI 生成 ---

    def _on_ai_generate(self) -> None:
        if self._ai is None:
            return
        desc = self._ai_desc.text().strip()
        if not desc:
            QMessageBox.warning(self, "需要描述", "请先输入自然语言描述。")
            return
        self._ai_status.setText(
            "<span style='color:#7f8c8d; font-size:11px;'>调用中…</span>"
        )
        QApplication.processEvents()
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            rule_dict, raw = self._ai.generate_rule(desc)
        finally:
            QApplication.restoreOverrideCursor()
        if rule_dict is None:
            self._ai_status.setText(
                f"<span style='color:#c0392b; font-size:11px;'>失败：{raw[:200]}</span>"
            )
            return
        idx = self._cat.findData(rule_dict["category"])
        if idx >= 0:
            self._cat.setCurrentIndex(idx)
        self._process.setText(rule_dict.get("process") or "")
        self._title_re.setText(rule_dict.get("title_regex") or "")
        self._url_re.setText(rule_dict.get("url_regex") or "")
        self._priority.setValue(int(rule_dict.get("priority") or 100))
        self._note.setText(rule_dict.get("note") or "AI 生成")
        if not self._create_rule.isChecked():
            self._create_rule.setChecked(True)
        self._source_hint = SOURCE_AI
        self._ai_status.setText(
            f"<span style='color:#27ae60; font-size:11px;'>"
            f"✓ 已填入：{CATEGORY_LABELS.get(rule_dict['category'])}  ·  "
            f"优先级 {rule_dict.get('priority', 100)}</span>"
        )

    # --- 提交 ---

    def _on_ok(self) -> None:
        process = self._process.text().strip() or None
        title_re = self._title_re.text().strip() or None
        url_re = self._url_re.text().strip() or None
        create_rule = self._create_rule.isChecked()

        if create_rule and not any((process, title_re, url_re)):
            QMessageBox.warning(
                self, "信息不全",
                "进程名、标题正则、URL 正则至少要填一项。",
            )
            return

        if create_rule:
            import re as _re
            for label, expr in (("标题正则", title_re), ("URL 正则", url_re)):
                if expr:
                    try:
                        _re.compile(expr)
                    except _re.error as e:
                        QMessageBox.warning(self, f"{label}不合法", str(e))
                        return

        category = self._cat.currentData()
        source = getattr(self, "_source_hint", SOURCE_USER)
        if self._editing:
            r = self._rule
            r.category = category
            r.process = process
            r.title_regex = title_re
            r.url_regex = url_re
            r.priority = self._priority.value()
            r.note = self._note.text()
            self._result_rule = r
        else:
            self._result_rule = Rule.new(
                category=category,
                process=process,
                title_regex=title_re,
                url_regex=url_re,
                priority=self._priority.value(),
                source=source,
                note=self._note.text(),
            )
        self._result_backfill = self._backfill.isChecked() and create_rule
        self._result_create = create_rule
        self.accept()

    def result_data(self) -> ClassifyResult:
        return ClassifyResult(
            rule=self._result_rule,
            backfill=self._result_backfill,
            create_rule=self._result_create,
        )
