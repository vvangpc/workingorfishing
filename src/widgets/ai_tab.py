"""AI 判断 Tab：OpenAI 兼容 LLM 配置 + 测试（美化版）。"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..ai_classifier import AIClassifier
from ..settings import DEFAULT_AI_PROMPT, AISettings, Settings


_TAB_QSS = """
QWidget#AITabRoot { background-color: #f4f6f7; }
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
QLineEdit, QSpinBox, QDoubleSpinBox, QPlainTextEdit {
    padding: 5px 8px;
    border: 1px solid #d0d6d9;
    border-radius: 4px;
    background: white;
}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QPlainTextEdit:focus {
    border: 1px solid #3498db;
}
"""

_PRIMARY_BTN_QSS = (
    "QPushButton {"
    " padding: 7px 20px;"
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


class AITab(QWidget):
    settings_changed = Signal(Settings)

    def __init__(
        self,
        settings: Settings,
        ai_classifier: AIClassifier,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._settings = settings
        self._ai = ai_classifier
        self.setObjectName("AITabRoot")
        self.setStyleSheet(_TAB_QSS)
        ai = settings.ai

        # --- 顶部：标题 + 状态徽章 ---
        title = QLabel("AI 判断")
        title.setStyleSheet("font: bold 16px '微软雅黑'; color: #2c3e50;")
        self._status_badge = QLabel("")
        self._status_badge.setStyleSheet(
            "padding: 2px 10px; border-radius: 8px; font-size: 11px; font-weight: bold;"
        )
        header = QHBoxLayout()
        header.setSpacing(8)
        header.addWidget(title)
        header.addWidget(self._status_badge)
        header.addStretch(1)

        # --- 启用 ---
        self._enabled = QCheckBox("启用 AI 自动判断（后台对未匹配规则的活动调 LLM 给建议）")
        self._enabled.setChecked(ai.enabled)
        self._enabled.stateChanged.connect(lambda _s: self._update_status())

        # --- 连接配置 ---
        conn_box = QGroupBox("连接")
        conn_form = QFormLayout(conn_box)
        conn_form.setContentsMargins(10, 8, 10, 8)
        self._base_url = QLineEdit(ai.base_url)
        self._base_url.setPlaceholderText("https://api.openai.com/v1（或兼容端点）")
        self._api_key = QLineEdit(ai.api_key)
        self._api_key.setEchoMode(QLineEdit.Password)
        self._api_key.textChanged.connect(lambda _t: self._update_status())
        self._model = QLineEdit(ai.model)
        self._model.setPlaceholderText("gpt-4o-mini / deepseek-chat / qwen-plus / 本地 ollama 模型名")
        conn_form.addRow("Base URL", self._base_url)
        conn_form.addRow("API Key", self._api_key)
        conn_form.addRow("模型", self._model)

        # --- 调度参数 ---
        sched_box = QGroupBox("调度与生成参数")
        sched_form = QFormLayout(sched_box)
        sched_form.setContentsMargins(10, 8, 10, 8)
        self._temp = QDoubleSpinBox()
        self._temp.setRange(0.0, 2.0)
        self._temp.setSingleStep(0.1)
        self._temp.setValue(ai.temperature)
        self._batch = QSpinBox()
        self._batch.setRange(1, 50)
        self._batch.setValue(ai.batch_size)
        self._interval = QSpinBox()
        self._interval.setRange(5, 3600)
        self._interval.setSuffix(" 秒")
        self._interval.setValue(ai.interval_seconds)
        sched_form.addRow("Temperature", self._temp)
        sched_form.addRow("批量大小", self._batch)
        sched_form.addRow("调用间隔", self._interval)

        # --- Prompt ---
        prompt_box = QGroupBox("Prompt 模板")
        prompt_layout = QVBoxLayout(prompt_box)
        prompt_layout.setContentsMargins(10, 8, 10, 8)
        self._prompt = QPlainTextEdit(ai.prompt_template or DEFAULT_AI_PROMPT)
        self._prompt.setMinimumHeight(140)
        prompt_layout.addWidget(self._prompt)

        # --- 运行统计 ---
        stats_row = QHBoxLayout()
        self._stats_label = QLabel("调用 0 次  ·  错误 0 次")
        self._stats_label.setStyleSheet("color: #555; font-size: 11px;")
        self._ai.stats_changed.connect(self._on_stats)
        stats_row.addWidget(self._stats_label)
        stats_row.addStretch(1)

        # --- 测试区 ---
        test_box = QGroupBox("测试调用（不会写入数据库 / 规则）")
        test_layout = QVBoxLayout(test_box)
        test_layout.setContentsMargins(10, 8, 10, 8)
        sample_row = QHBoxLayout()
        self._t_proc = QLineEdit("chrome.exe")
        self._t_proc.setPlaceholderText("进程名")
        self._t_title = QLineEdit("Bilibili - 哔哩哔哩")
        self._t_title.setPlaceholderText("窗口标题")
        self._t_url = QLineEdit("https://www.bilibili.com/")
        self._t_url.setPlaceholderText("URL")
        sample_row.addWidget(self._t_proc)
        sample_row.addWidget(self._t_title)
        sample_row.addWidget(self._t_url)
        btn_test = QPushButton("发送测试请求")
        btn_test.setStyleSheet(_DEFAULT_BTN_QSS)
        btn_test.clicked.connect(self._on_test)
        self._test_output = QPlainTextEdit()
        self._test_output.setReadOnly(True)
        self._test_output.setMinimumHeight(120)
        self._test_output.setStyleSheet(
            "QPlainTextEdit { background-color: #f8f9fa; border: 1px solid #d0d6d9;"
            " border-radius: 3px; font-family: Consolas, monospace; font-size: 11px; }"
        )
        test_layout.addLayout(sample_row)
        test_layout.addWidget(btn_test)
        test_layout.addWidget(self._test_output)

        # --- 保存 ---
        btn_save = QPushButton("保存配置")
        btn_save.setStyleSheet(_PRIMARY_BTN_QSS)
        btn_save.clicked.connect(self._on_save)
        save_row = QHBoxLayout()
        save_row.addStretch(1)
        save_row.addWidget(btn_save)

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(8)
        root.addLayout(header)
        root.addWidget(self._enabled)
        root.addWidget(conn_box)
        root.addWidget(sched_box)
        root.addWidget(prompt_box)
        root.addLayout(stats_row)
        root.addWidget(test_box)
        root.addLayout(save_row)

        self._update_status()

    # --- 状态徽章 ---

    def _update_status(self) -> None:
        has_key = bool(self._api_key.text().strip())
        enabled = self._enabled.isChecked()
        if enabled and has_key:
            self._status_badge.setText("● 已启用")
            self._status_badge.setStyleSheet(
                self._status_badge.styleSheet().rstrip(";") + ";"
                "background-color: #d4efdf; color: #1e8449;"
            )
        elif has_key:
            self._status_badge.setText("● 已配置 / 未启用")
            self._status_badge.setStyleSheet(
                self._status_badge.styleSheet().rstrip(";") + ";"
                "background-color: #fcf3cf; color: #b7950b;"
            )
        else:
            self._status_badge.setText("● 未配置")
            self._status_badge.setStyleSheet(
                self._status_badge.styleSheet().rstrip(";") + ";"
                "background-color: #ecf0f1; color: #7f8c8d;"
            )

    def _on_stats(self, stats: dict) -> None:
        last = stats.get("last_error", "")
        text = f"调用 {stats.get('calls', 0)} 次  ·  错误 {stats.get('errors', 0)} 次"
        if last:
            text += f"  ·  最后错误: {last[:60]}"
        self._stats_label.setText(text)

    # --- 提交 ---

    def _gather(self) -> AISettings:
        return AISettings(
            enabled=self._enabled.isChecked(),
            base_url=self._base_url.text().strip(),
            api_key=self._api_key.text(),
            model=self._model.text().strip(),
            temperature=self._temp.value(),
            batch_size=self._batch.value(),
            interval_seconds=self._interval.value(),
            prompt_template=self._prompt.toPlainText() or DEFAULT_AI_PROMPT,
        )

    def _on_save(self) -> None:
        new_ai = self._gather()
        self._settings.ai = new_ai
        self._settings.save()
        self._ai.configure(new_ai)
        if new_ai.enabled:
            self._ai.start()
        else:
            self._ai.stop()
        self.settings_changed.emit(self._settings)
        self._update_status()
        QMessageBox.information(self, "已保存", "AI 配置已应用。")

    def _on_test(self) -> None:
        new_ai = self._gather()
        self._ai.configure(new_ai)
        sample = {
            "process": self._t_proc.text().strip() or None,
            "title": self._t_title.text().strip() or None,
            "url": self._t_url.text().strip() or None,
        }
        self._test_output.setPlainText("调用中…")
        sug, raw = self._ai.test(sample)
        if sug is None:
            self._test_output.setPlainText(f"--- 失败 ---\n{raw}")
        else:
            from ..widgets.classify_dialog import CATEGORY_LABELS
            self._test_output.setPlainText(
                "--- 解析结果 ---\n"
                f"类别: {CATEGORY_LABELS.get(sug.category, sug.category)}\n"
                f"理由: {sug.reason}\n"
                f"建议进程: {sug.suggested_process or '(无)'}\n"
                f"建议标题正则: {sug.suggested_title_regex or '(无)'}\n"
                f"建议 URL 正则: {sug.suggested_url_regex or '(无)'}\n\n"
                "--- 原始响应 ---\n"
                f"{raw}"
            )
