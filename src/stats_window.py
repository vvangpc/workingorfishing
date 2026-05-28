"""统计窗口：日/周/月，饼图 + 柱状图 + 三级层级树（类别 → 进程 → 标题/URL）。"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from PySide6.QtCharts import (
    QBarCategoryAxis,
    QBarSeries,
    QBarSet,
    QChart,
    QChartView,
    QPieSeries,
    QValueAxis,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QColor, QFont, QPainter
from PySide6.QtWidgets import (
    QDateEdit,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .classifier import Classifier
from .storage import Storage, day_range, month_range, week_range
from .tray import STATE_COLORS, STATE_LABELS
from .widgets.classify_dialog import CATEGORY_LABELS, ClassifyDialog

_CATEGORIES = ("work", "fishing", "neutral", "idle")
_TREE_CATEGORIES = ("work", "fishing", "neutral")

# 浏览器进程：第三级按 URL 分组
_BROWSER_PROCS = {
    "chrome.exe",
    "msedge.exe",
    "firefox.exe",
    "brave.exe",
    "opera.exe",
    "vivaldi.exe",
}

# 常见站点：把 URL 折叠成一个域名桶。值 = (显示名, 一并匹配的 url_regex)
_URL_FRIENDLY = {
    "youtube.com": ("YouTube", r"(?i).*(youtube\.com|youtu\.be).*"),
    "youtu.be": ("YouTube", r"(?i).*(youtube\.com|youtu\.be).*"),
    "bilibili.com": ("哔哩哔哩", r"(?i).*(bilibili\.com|b23\.tv).*"),
    "b23.tv": ("哔哩哔哩", r"(?i).*(bilibili\.com|b23\.tv).*"),
    "twitter.com": ("X / Twitter", r"(?i).*(twitter\.com|x\.com).*"),
    "x.com": ("X / Twitter", r"(?i).*(twitter\.com|x\.com).*"),
    "zhihu.com": ("知乎", r"(?i).*zhihu\.com.*"),
    "github.com": ("GitHub", r"(?i).*github\.com.*"),
    "gitlab.com": ("GitLab", r"(?i).*gitlab\.com.*"),
    "gitee.com": ("Gitee", r"(?i).*gitee\.com.*"),
    "stackoverflow.com": ("Stack Overflow", r"(?i).*stackoverflow\.com.*"),
    "stackexchange.com": ("Stack Exchange", r"(?i).*stackexchange\.com.*"),
    "deepseek.com": ("DeepSeek", r"(?i).*deepseek\.com.*"),
    "chatgpt.com": ("ChatGPT", r"(?i).*(chatgpt\.com|openai\.com).*"),
    "openai.com": ("ChatGPT", r"(?i).*(chatgpt\.com|openai\.com).*"),
    "claude.ai": ("Claude", r"(?i).*claude\.ai.*"),
    "gemini.google.com": ("Gemini", r"(?i).*gemini\.google\.com.*"),
    "kimi.moonshot.cn": ("Kimi", r"(?i).*kimi\.moonshot\.cn.*"),
    "weibo.com": ("微博", r"(?i).*weibo\.com.*"),
    "xiaohongshu.com": ("小红书", r"(?i).*xiaohongshu\.com.*"),
    "douyin.com": ("抖音", r"(?i).*douyin\.com.*"),
    "reddit.com": ("Reddit", r"(?i).*reddit\.com.*"),
    "taobao.com": ("淘宝", r"(?i).*taobao\.com.*"),
    "tmall.com": ("天猫", r"(?i).*tmall\.com.*"),
    "jd.com": ("京东", r"(?i).*jd\.com.*"),
    "linkedin.com": ("LinkedIn", r"(?i).*linkedin\.com.*"),
    "notion.so": ("Notion", r"(?i).*notion\.so.*"),
    "slack.com": ("Slack", r"(?i).*slack\.com.*"),
    "teams.microsoft.com": ("Microsoft Teams", r"(?i).*teams\.microsoft\.com.*"),
    "discord.com": ("Discord", r"(?i).*discord\.com.*"),
    "twitch.tv": ("Twitch", r"(?i).*twitch\.tv.*"),
    "google.com": ("Google", r"(?i).*google\.com.*"),
    "baidu.com": ("百度", r"(?i).*baidu\.com.*"),
    "arxiv.org": ("arXiv", r"(?i).*arxiv\.org.*"),
}


def _extract_host(url: str) -> str:
    import re as _re
    m = _re.match(r"https?://([^/?#]+)", url)
    if not m:
        return url
    host = m.group(1).lower()
    return _re.sub(r"^(www|m|mobile)\.", "", host)


def _url_to_bucket(url: str) -> tuple[str, str]:
    """返回 (显示名, 该桶对应的 url_regex)。"""
    if not url:
        return ("(空)", "")
    if not url.lower().startswith(("http://", "https://")):
        # 不是合法 URL，保持原样
        return (url, "")
    host = _extract_host(url)
    if host in _URL_FRIENDLY:
        return _URL_FRIENDLY[host]
    parts = host.split(".")
    if len(parts) > 2:
        parent = ".".join(parts[-2:])
        if parent in _URL_FRIENDLY:
            return _URL_FRIENDLY[parent]
    # 默认：用裸主机名
    import re as _re
    return (host, f"(?i).*{_re.escape(host)}.*")


def _fmt_duration(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    m, s = divmod(seconds, 60)
    if m < 60:
        return f"{m}m {s}s"
    h, m = divmod(m, 60)
    return f"{h}h {m}m"


def _trunc(text: str, n: int = 100) -> str:
    if text is None:
        return ""
    text = str(text)
    return text if len(text) <= n else text[: n - 1] + "…"


class _StatsView(QWidget):
    def __init__(
        self,
        storage: Storage,
        classifier: Classifier,
        sample_interval: int,
        mode: str,
        ai_classifier=None,
        parent=None,
    ):
        super().__init__(parent)
        self._storage = storage
        self._classifier = classifier
        self._ai = ai_classifier
        self._sample_interval = sample_interval
        self._mode = mode
        self._anchor = datetime.now()
        self._current_range: tuple[int, int] = (0, 0)

        # --- 顶部控件 ---
        self._date_edit = QDateEdit()
        self._date_edit.setCalendarPopup(True)
        self._date_edit.setDate(self._anchor.date())
        self._date_edit.dateChanged.connect(self._on_date_changed)

        btn_prev = QPushButton("◀ 上一" + self._unit_label())
        btn_prev.clicked.connect(lambda: self._shift(-1))
        btn_next = QPushButton("下一" + self._unit_label() + " ▶")
        btn_next.clicked.connect(lambda: self._shift(+1))
        btn_today = QPushButton("今天")
        btn_today.clicked.connect(self._jump_today)

        for b in (btn_prev, btn_next, btn_today):
            b.setStyleSheet(
                "QPushButton { padding: 4px 12px; border-radius: 4px;"
                " background-color: #ecf0f1; border: 1px solid #d0d6d9; }"
                "QPushButton:hover { background-color: #d6dbdf; }"
            )

        top = QHBoxLayout()
        top.addWidget(btn_prev)
        top.addWidget(self._date_edit)
        top.addWidget(btn_next)
        top.addWidget(btn_today)
        top.addStretch(1)
        self._summary_label = QLabel("")
        self._summary_label.setStyleSheet("font: bold 13px '微软雅黑'; color: #2c3e50;")
        self._summary_label.setTextFormat(Qt.RichText)
        top.addWidget(self._summary_label)

        # --- 图表 ---
        self._pie_chart = QChart()
        self._pie_chart.legend().setAlignment(Qt.AlignRight)
        self._pie_view = QChartView(self._pie_chart)
        self._pie_view.setRenderHint(QPainter.Antialiasing)
        self._pie_view.setMinimumHeight(220)

        self._bar_chart = QChart()
        self._bar_chart.legend().setAlignment(Qt.AlignBottom)
        self._bar_view = QChartView(self._bar_chart)
        self._bar_view.setRenderHint(QPainter.Antialiasing)
        self._bar_view.setMinimumHeight(220)

        chart_row = QHBoxLayout()
        chart_row.addWidget(self._pie_view, 1)
        chart_row.addWidget(self._bar_view, 2)

        # --- 层级树 ---
        self._tree = QTreeWidget()
        self._tree.setColumnCount(3)
        self._tree.setHeaderLabels(["分类 / 进程 / 标题（或 URL）", "时长", "占比"])
        self._tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self._tree.setColumnWidth(1, 110)
        self._tree.setColumnWidth(2, 70)
        self._tree.setAlternatingRowColors(True)
        self._tree.setUniformRowHeights(True)
        self._tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._on_context)
        self._tree.itemExpanded.connect(self._on_item_expanded)
        self._tree.itemDoubleClicked.connect(self._on_double_click)
        self._tree.setStyleSheet(
            "QTreeWidget { background-color: white; border: 1px solid #d0d6d9;"
            " border-radius: 4px; alternate-background-color: #fafbfc; }"
            "QTreeWidget::item { padding: 4px 0; }"
            "QTreeWidget::item:selected { background-color: #d4e6f1; color: #000; }"
            "QHeaderView::section { background-color: #f4f6f7; color: #333;"
            " padding: 6px; border: none; border-bottom: 1px solid #d0d6d9;"
            " font-weight: bold; }"
        )

        info = QLabel(
            "<i style='color:#888; font-size:11px;'>"
            "点击 ▶ 展开下一层；右键 / 双击行可对该进程或站点重新归类。"
            "</i>"
        )

        root = QVBoxLayout(self)
        root.addLayout(top)
        root.addLayout(chart_row)
        root.addWidget(info)
        root.addWidget(self._tree, 1)

        self.refresh()

    def _unit_label(self) -> str:
        return {"day": "天", "week": "周", "month": "月"}[self._mode]

    def _shift(self, n: int) -> None:
        if self._mode == "day":
            self._anchor += timedelta(days=n)
        elif self._mode == "week":
            self._anchor += timedelta(weeks=n)
        else:
            y, m = self._anchor.year, self._anchor.month + n
            while m < 1:
                m += 12
                y -= 1
            while m > 12:
                m -= 12
                y += 1
            day = min(self._anchor.day, 28)
            self._anchor = self._anchor.replace(year=y, month=m, day=day)
        self._date_edit.blockSignals(True)
        self._date_edit.setDate(self._anchor.date())
        self._date_edit.blockSignals(False)
        self.refresh()

    def _jump_today(self) -> None:
        self._anchor = datetime.now()
        self._date_edit.blockSignals(True)
        self._date_edit.setDate(self._anchor.date())
        self._date_edit.blockSignals(False)
        self.refresh()

    def _on_date_changed(self, qd) -> None:
        self._anchor = datetime(qd.year(), qd.month(), qd.day())
        self.refresh()

    def set_sample_interval(self, seconds: int) -> None:
        self._sample_interval = seconds
        self.refresh()

    # --- 渲染 ---

    def _resolve_range(self) -> tuple[int, int]:
        if self._mode == "day":
            return day_range(self._anchor)
        if self._mode == "week":
            return week_range(self._anchor)
        return month_range(self._anchor)

    def refresh(self) -> None:
        start, end = self._resolve_range()
        self._current_range = (start, end)
        totals = self._storage.aggregate_range(start, end, self._sample_interval)
        self._render_pie(totals)
        self._render_bar(start, end)
        self._render_tree(start, end, totals)
        self._render_summary(totals)

    # --- 饼图 ---

    def _render_pie(self, totals: dict[str, int]) -> None:
        series = QPieSeries()
        any_data = False
        for cat in _CATEGORIES:
            v = totals.get(cat, 0)
            if v <= 0:
                continue
            any_data = True
            slc = series.append(f"{STATE_LABELS.get(cat, cat)} ({_fmt_duration(v)})", v)
            slc.setBrush(STATE_COLORS.get(cat, QColor("gray")))
            slc.setLabelVisible(True)
        self._pie_chart.removeAllSeries()
        if any_data:
            self._pie_chart.addSeries(series)
        self._pie_chart.setTitle("分类占比")

    # --- 柱状图 ---

    def _render_bar(self, start: int, end: int) -> None:
        if self._mode == "day":
            bucket = 3600
            n_buckets = 24
            labels = [f"{i:02d}" for i in range(24)]
        elif self._mode == "week":
            bucket = 86400
            n_buckets = 7
            monday = datetime.fromtimestamp(start)
            labels = [(monday + timedelta(days=i)).strftime("%m/%d") for i in range(7)]
        else:
            bucket = 86400
            first = datetime.fromtimestamp(start)
            nxt = datetime.fromtimestamp(end)
            n_buckets = (nxt - first).days
            labels = [(first + timedelta(days=i)).strftime("%d") for i in range(n_buckets)]

        rows = self._storage.buckets(start, end, bucket, self._sample_interval)
        per_bucket: dict[int, dict[str, int]] = {}
        for bucket_start, cats in rows:
            idx = (bucket_start - start) // bucket
            per_bucket[idx] = cats

        series = QBarSeries()
        max_val = 0
        for cat in _CATEGORIES:
            bs = QBarSet(STATE_LABELS.get(cat, cat))
            bs.setColor(STATE_COLORS.get(cat, QColor("gray")))
            for i in range(n_buckets):
                v = per_bucket.get(i, {}).get(cat, 0)
                bs.append(v / 60.0)
            series.append(bs)
            for i in range(n_buckets):
                v = per_bucket.get(i, {}).get(cat, 0)
                max_val = max(max_val, v)

        self._bar_chart.removeAllSeries()
        for ax in list(self._bar_chart.axes()):
            self._bar_chart.removeAxis(ax)
        self._bar_chart.addSeries(series)
        ax_x = QBarCategoryAxis()
        ax_x.append(labels)
        self._bar_chart.addAxis(ax_x, Qt.AlignBottom)
        series.attachAxis(ax_x)
        ax_y = QValueAxis()
        ax_y.setTitleText("分钟")
        ax_y.setRange(0, max(5, max_val / 60.0 * 1.1))
        self._bar_chart.addAxis(ax_y, Qt.AlignLeft)
        series.attachAxis(ax_y)
        self._bar_chart.setTitle("分时段分布")

    # --- 三级树 ---

    def _render_tree(self, start: int, end: int, totals: dict[str, int]) -> None:
        self._tree.clear()
        # 分母用 work + fishing + neutral（不含 idle / unknown），方便看占比
        grand = sum(totals.get(c, 0) for c in _TREE_CATEGORIES) or 1
        for cat in _TREE_CATEGORIES:
            cat_secs = totals.get(cat, 0)
            if cat_secs <= 0:
                continue
            root = QTreeWidgetItem()
            root.setText(0, STATE_LABELS.get(cat, cat))
            root.setText(1, _fmt_duration(cat_secs))
            root.setText(2, f"{cat_secs / grand * 100:.0f}%")
            color = STATE_COLORS.get(cat, QColor("black"))
            for c in range(3):
                root.setForeground(c, color)
            f = root.font(0)
            f.setBold(True)
            root.setFont(0, f)
            root.setData(0, Qt.UserRole, {"level": "category", "category": cat, "loaded": False, "total": cat_secs})
            placeholder = QTreeWidgetItem()
            placeholder.setText(0, "加载中…")
            root.addChild(placeholder)
            self._tree.addTopLevelItem(root)
        # 默认展开类别级别 → 触发 itemExpanded 懒加载进程
        for i in range(self._tree.topLevelItemCount()):
            self._tree.expandItem(self._tree.topLevelItem(i))

    def _on_item_expanded(self, item: QTreeWidgetItem) -> None:
        data = item.data(0, Qt.UserRole) or {}
        if data.get("loaded"):
            return
        start, end = self._current_range
        # 清掉 placeholder
        while item.childCount():
            item.removeChild(item.child(0))

        level = data.get("level")
        if level == "category":
            cat = data["category"]
            cat_total = data["total"] or 1
            procs = self._storage.breakdown_by_process(start, end, cat, self._sample_interval, limit=80)
            color = STATE_COLORS.get(cat, QColor("black"))
            for proc_name, secs in procs:
                node = QTreeWidgetItem()
                node.setText(0, proc_name or "(未知进程)")
                node.setText(1, _fmt_duration(secs))
                node.setText(2, f"{secs / cat_total * 100:.0f}%")
                node.setForeground(0, color)
                f = node.font(0)
                f.setBold(True)
                node.setFont(0, f)
                node.setData(0, Qt.UserRole, {
                    "level": "process",
                    "category": cat,
                    "process": proc_name,
                    "loaded": False,
                    "total": secs,
                })
                placeholder = QTreeWidgetItem()
                placeholder.setText(0, "加载中…")
                node.addChild(placeholder)
                item.addChild(node)
        elif level == "process":
            cat = data["category"]
            proc = data["process"]
            proc_total = data["total"] or 1
            use_url = (proc or "").lower() in _BROWSER_PROCS
            raw_items = self._storage.breakdown_by_window(
                start, end, cat, proc, self._sample_interval, use_url=use_url, limit=400
            )

            # 浏览器：按域名/友好名聚合，把同站点的所有 URL 合并成一行
            if use_url:
                grouped: dict[tuple[str, str], int] = {}
                for value, secs in raw_items:
                    label, regex = _url_to_bucket(value)
                    key = (label, regex)
                    grouped[key] = grouped.get(key, 0) + secs
                items = sorted(
                    ((label, regex, secs) for (label, regex), secs in grouped.items()),
                    key=lambda x: -x[2],
                )
            else:
                items = [(name, "", secs) for name, secs in raw_items]

            for label, url_regex, secs in items:
                node = QTreeWidgetItem()
                node.setText(0, _trunc(label, 120))
                node.setText(1, _fmt_duration(secs))
                node.setText(2, f"{secs / proc_total * 100:.0f}%")
                node.setForeground(0, QColor("#555"))
                node.setData(0, Qt.UserRole, {
                    "level": "leaf",
                    "category": cat,
                    "process": proc,
                    "value": label,
                    "url_regex": url_regex,
                    "is_url": use_url,
                })
                item.addChild(node)

        data["loaded"] = True
        item.setData(0, Qt.UserRole, data)

    # --- 摘要 ---

    def _render_summary(self, totals: dict[str, int]) -> None:
        work = totals.get("work", 0)
        fishing = totals.get("fishing", 0)
        active = work + fishing + totals.get("neutral", 0)
        if active <= 0:
            self._summary_label.setText(
                "<span style='color:#999;'>该区间暂无数据</span>"
            )
            return
        ratio = work / active * 100 if active else 0
        self._summary_label.setText(
            f"<span style='color:#2ecc71;'>工作 {_fmt_duration(work)}</span>"
            "  <span style='color:#bbb;'>·</span>  "
            f"<span style='color:#e74c3c;'>摸鱼 {_fmt_duration(fishing)}</span>"
            "  <span style='color:#bbb;'>·</span>  "
            f"<span style='color:#34495e;'>工作占比 <b>{ratio:.1f}%</b></span>"
        )

    # --- 右键 / 双击改分类 ---

    def _on_context(self, pos) -> None:
        item = self._tree.itemAt(pos)
        if item is None:
            return
        data = item.data(0, Qt.UserRole) or {}
        level = data.get("level")
        if level not in ("process", "leaf"):
            return
        current_cat = data.get("category")
        menu = QMenu(self)
        for target_cat in ("work", "fishing", "neutral"):
            if target_cat == current_cat:
                continue
            label = CATEGORY_LABELS.get(target_cat, target_cat)
            act = QAction(f"归类为 {label}…", menu)
            act.triggered.connect(
                lambda _=False, c=target_cat, d=data: self._reclassify(c, d)
            )
            menu.addAction(act)
        menu.exec(self._tree.viewport().mapToGlobal(pos))

    def _on_double_click(self, item: QTreeWidgetItem, _col: int) -> None:
        data = item.data(0, Qt.UserRole) or {}
        level = data.get("level")
        if level not in ("process", "leaf"):
            return
        current = data.get("category")
        target = "work" if current == "fishing" else "fishing"
        self._reclassify(target, data)

    def _reclassify(self, target_cat: str, data: dict) -> None:
        level = data.get("level")
        pre_url_regex = None
        if level == "process":
            sample_process = data.get("process")
            sample_url = None
            sample_title = None
            display = sample_process or "(未知)"
        else:  # leaf
            sample_process = data.get("process")
            if data.get("is_url"):
                sample_url = data.get("value")  # 已经是友好域名标签
                sample_title = None
                pre_url_regex = data.get("url_regex") or None
            else:
                sample_url = None
                sample_title = data.get("value")
            display = data.get("value") or "(空)"

        dlg = ClassifyDialog(
            title=f"重新归类 {_trunc(display, 60)}",
            sample_process=sample_process,
            sample_title=sample_title,
            sample_url=sample_url,
            pre_url_regex=pre_url_regex,
            ai_classifier=self._ai,
            parent=self,
        )
        idx = dlg._cat.findData(target_cat)
        if idx >= 0:
            dlg._cat.setCurrentIndex(idx)
        if dlg.exec() != ClassifyDialog.Accepted:
            return
        result = dlg.result_data()
        msgs = []
        if result.create_rule:
            self._classifier.add_rule(result.rule)
            msgs.append("规则已添加")
            if result.backfill:
                n = self._storage.reclassify_by_rule(result.rule, only_unknown=False)
                msgs.append(f"回填 {n} 条历史记录")
        else:
            if level == "process" and sample_process:
                n = self._storage.reclassify_by_process(
                    sample_process, target_cat, only_unknown=False
                )
                msgs.append(f"回填 {n} 条 {sample_process} 的历史记录")
        QMessageBox.information(self, "已应用", "\n".join(msgs) or "已应用")
        self.refresh()


class StatsTab(QWidget):
    """统计 Tab：嵌入主窗口『设置』下的子 Tab。日 / 周 / 月。"""

    def __init__(
        self,
        storage: Storage,
        classifier: Classifier,
        sample_interval: int,
        ai_classifier=None,
        parent=None,
    ):
        super().__init__(parent)
        self._views = {
            "day": _StatsView(storage, classifier, sample_interval, "day", ai_classifier=ai_classifier),
            "week": _StatsView(storage, classifier, sample_interval, "week", ai_classifier=ai_classifier),
            "month": _StatsView(storage, classifier, sample_interval, "month", ai_classifier=ai_classifier),
        }
        tabs = QTabWidget()
        tabs.addTab(self._views["day"], "日")
        tabs.addTab(self._views["week"], "周")
        tabs.addTab(self._views["month"], "月")
        tabs.currentChanged.connect(lambda _: self._refresh_current(tabs))
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(tabs)

    def _refresh_current(self, tabs: QTabWidget) -> None:
        w = tabs.currentWidget()
        if isinstance(w, _StatsView):
            w.refresh()

    def set_sample_interval(self, seconds: int) -> None:
        for v in self._views.values():
            v.set_sample_interval(seconds)

    def refresh_all(self) -> None:
        for v in self._views.values():
            v.refresh()
