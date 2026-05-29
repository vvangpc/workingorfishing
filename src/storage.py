"""SQLite 持久层、聚合查询、批量回填。

Schema：activity_log 单表
  - category: work / fishing / neutral / unknown / idle
  - rule_id : 命中的规则 id（unknown / idle 为 NULL）
"""
from __future__ import annotations

import logging
import re
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from .paths import db_file

logger = logging.getLogger(__name__)


_SCHEMA_BASE = """
CREATE TABLE IF NOT EXISTS activity_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    process_name TEXT,
    window_title TEXT,
    url TEXT,
    category TEXT NOT NULL,
    is_idle INTEGER NOT NULL DEFAULT 0,
    rule_id TEXT,
    interval INTEGER
);
"""

_SCHEMA_INDICES = """
CREATE INDEX IF NOT EXISTS idx_ts ON activity_log(ts);
CREATE INDEX IF NOT EXISTS idx_category_ts ON activity_log(category, ts);
CREATE INDEX IF NOT EXISTS idx_process ON activity_log(process_name);
CREATE INDEX IF NOT EXISTS idx_rule_id ON activity_log(rule_id);
"""


def _existing_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    cur = conn.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cur.fetchall()}


class Storage:
    def __init__(self, path: Optional[Path] = None, default_interval: int = 10) -> None:
        self._path = Path(path) if path else db_file()
        # 用于历史行（迁移前未记录 interval）的回填值，也是 SUM 时的兜底
        self._default_interval = max(1, int(default_interval))
        self._lock = threading.Lock()
        self._connect()

    def _connect(self) -> None:
        self._conn = sqlite3.connect(
            str(self._path), check_same_thread=False, isolation_level=None
        )
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(_SCHEMA_BASE)
        self._migrate()
        self._conn.executescript(_SCHEMA_INDICES)

    def _migrate(self) -> None:
        cols = _existing_columns(self._conn, "activity_log")
        if "rule_id" not in cols:
            self._conn.execute("ALTER TABLE activity_log ADD COLUMN rule_id TEXT")
            logger.info("storage: added rule_id column")
        if "interval" not in cols:
            # 旧库每行没有采样间隔。回填当前配置的间隔——等于升级时刻旧逻辑
            # (COUNT * 当前间隔) 的取值，使既有统计在升级后保持不变；此后新行记录真实间隔。
            self._conn.execute("ALTER TABLE activity_log ADD COLUMN interval INTEGER")
            self._conn.execute(
                "UPDATE activity_log SET interval = ? WHERE interval IS NULL",
                (self._default_interval,),
            )
            logger.info(
                "storage: added interval column, backfilled with %d", self._default_interval
            )

    def set_default_interval(self, seconds: int) -> None:
        self._default_interval = max(1, int(seconds))

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass

    def reopen(self) -> None:
        """关闭当前连接并按现在的 self._path 重新连。导入数据 / WebDAV 拉取后用。"""
        self.close()
        with self._lock:
            self._connect()

    def clear_all_activity(self) -> int:
        """删除全部活动记录，返回删除条数。"""
        with self._lock:
            cur = self._conn.execute("DELETE FROM activity_log")
            n = cur.rowcount or 0
        return n

    def merge_db(self, other_path: Path) -> int:
        """把另一个 activity.db 里本地没有的活动行并入本库。返回新增行数。

        去重以自然键 (ts, 进程, 标题, url, category, is_idle) 判断，避免重复导入。
        兼容旧 schema（缺 interval / rule_id 列）的来源库。WebDAV 合并同步用。
        """
        other_path = Path(other_path)
        if not other_path.exists():
            return 0
        with self._lock:
            self._conn.execute("ATTACH DATABASE ? AS merge_src", (str(other_path),))
            try:
                cols = {
                    row[1]
                    for row in self._conn.execute(
                        "PRAGMA merge_src.table_info(activity_log)"
                    )
                }
                if not cols:
                    return 0
                interval_sel = "o.interval" if "interval" in cols else str(self._default_interval)
                rule_sel = "o.rule_id" if "rule_id" in cols else "NULL"
                cur = self._conn.execute(
                    f"""
                    INSERT INTO activity_log
                        (ts, process_name, window_title, url, category, is_idle, rule_id, interval)
                    SELECT o.ts, o.process_name, o.window_title, o.url, o.category,
                           o.is_idle, {rule_sel}, {interval_sel}
                    FROM merge_src.activity_log o
                    WHERE NOT EXISTS (
                        SELECT 1 FROM activity_log a
                        WHERE a.ts = o.ts
                          AND IFNULL(a.process_name, '') = IFNULL(o.process_name, '')
                          AND IFNULL(a.window_title, '') = IFNULL(o.window_title, '')
                          AND IFNULL(a.url, '') = IFNULL(o.url, '')
                          AND a.category = o.category
                          AND a.is_idle = o.is_idle
                    )
                    """
                )
                n = cur.rowcount or 0
            finally:
                self._conn.execute("DETACH DATABASE merge_src")
        return n

    # --- 写入 ---

    def insert(
        self,
        *,
        process_name: Optional[str],
        window_title: Optional[str],
        url: Optional[str],
        category: str,
        is_idle: bool,
        rule_id: Optional[str] = None,
        ts: Optional[int] = None,
        interval: Optional[int] = None,
    ) -> None:
        ts = ts if ts is not None else int(time.time())
        # 记录这条样本代表的时长（= 采样间隔）。聚合时按行求和，改间隔不影响历史。
        iv = self._default_interval if interval is None else max(1, int(interval))
        with self._lock:
            self._conn.execute(
                "INSERT INTO activity_log"
                " (ts, process_name, window_title, url, category, is_idle, rule_id, interval)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    ts,
                    process_name,
                    window_title,
                    url,
                    category,
                    1 if is_idle else 0,
                    rule_id,
                    iv,
                ),
            )

    # --- 聚合 ---

    def aggregate_range(
        self, start_ts: int, end_ts: int, sample_interval: int
    ) -> dict[str, int]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT category, SUM(COALESCE(interval, ?)) FROM activity_log"
                " WHERE ts >= ? AND ts < ? GROUP BY category",
                (sample_interval, start_ts, end_ts),
            )
            rows = cur.fetchall()
        return {cat: int(total) for cat, total in rows}

    def buckets(
        self,
        start_ts: int,
        end_ts: int,
        bucket_seconds: int,
        sample_interval: int,
    ) -> list[tuple[int, dict[str, int]]]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT ((ts - ?) / ?) AS bucket, category, SUM(COALESCE(interval, ?))"
                " FROM activity_log"
                " WHERE ts >= ? AND ts < ?"
                " GROUP BY bucket, category"
                " ORDER BY bucket",
                (start_ts, bucket_seconds, sample_interval, start_ts, end_ts),
            )
            rows = cur.fetchall()
        out: dict[int, dict[str, int]] = {}
        for bucket, cat, total in rows:
            out.setdefault(bucket, {})[cat] = int(total)
        return [
            (start_ts + b * bucket_seconds, cats) for b, cats in sorted(out.items())
        ]

    def top_processes(
        self,
        start_ts: int,
        end_ts: int,
        category: Optional[str],
        sample_interval: int,
        limit: int = 10,
    ) -> list[tuple[str, int]]:
        sql = (
            "SELECT COALESCE(process_name, '(unknown)'), SUM(COALESCE(interval, ?)) FROM activity_log"
            " WHERE ts >= ? AND ts < ?"
        )
        params: list = [sample_interval, start_ts, end_ts]
        if category:
            sql += " AND category = ?"
            params.append(category)
        sql += " GROUP BY process_name ORDER BY 2 DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            cur = self._conn.execute(sql, params)
            rows = cur.fetchall()
        return [(name, int(total)) for name, total in rows]

    def top_urls(
        self,
        start_ts: int,
        end_ts: int,
        category: Optional[str],
        sample_interval: int,
        limit: int = 10,
    ) -> list[tuple[str, int]]:
        sql = (
            "SELECT url, SUM(COALESCE(interval, ?)) FROM activity_log"
            " WHERE ts >= ? AND ts < ? AND url IS NOT NULL AND url != ''"
        )
        params: list = [sample_interval, start_ts, end_ts]
        if category:
            sql += " AND category = ?"
            params.append(category)
        sql += " GROUP BY url ORDER BY 2 DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            cur = self._conn.execute(sql, params)
            rows = cur.fetchall()
        return [(url, int(total)) for url, total in rows]

    def breakdown_by_process(
        self,
        start_ts: int,
        end_ts: int,
        category: str,
        sample_interval: int,
        limit: int = 50,
    ) -> list[tuple[Optional[str], int]]:
        """按 (category, process_name) 分组，返回 [(process, seconds), ...] 降序。"""
        with self._lock:
            cur = self._conn.execute(
                "SELECT process_name, SUM(COALESCE(interval, ?)) FROM activity_log"
                " WHERE ts >= ? AND ts < ? AND category = ? AND is_idle = 0"
                " GROUP BY process_name"
                " ORDER BY 2 DESC"
                " LIMIT ?",
                (sample_interval, start_ts, end_ts, category, limit),
            )
            rows = cur.fetchall()
        return [(name, int(total)) for name, total in rows]

    def breakdown_by_window(
        self,
        start_ts: int,
        end_ts: int,
        category: str,
        process_name: Optional[str],
        sample_interval: int,
        use_url: bool = False,
        limit: int = 100,
    ) -> list[tuple[str, int]]:
        """对单个 (category, process) 再按窗口标题（或浏览器 URL）分组。

        - use_url=True：优先按 url 分组，url 为空时退回 title
        - use_url=False：按 window_title 分组
        返回 [(bucket_label, seconds), ...] 降序。
        """
        if use_url:
            bucket_expr = "COALESCE(NULLIF(url, ''), window_title, '(空)')"
        else:
            bucket_expr = "COALESCE(window_title, '(空)')"

        sql = (
            f"SELECT {bucket_expr} AS bucket, SUM(COALESCE(interval, ?)) FROM activity_log"
            " WHERE ts >= ? AND ts < ? AND category = ? AND is_idle = 0"
        )
        params: list = [sample_interval, start_ts, end_ts, category]
        if process_name is None:
            sql += " AND process_name IS NULL"
        else:
            sql += " AND LOWER(process_name) = ?"
            params.append(process_name.lower())
        sql += " GROUP BY bucket ORDER BY 2 DESC LIMIT ?"
        params.append(limit)

        with self._lock:
            cur = self._conn.execute(sql, params)
            rows = cur.fetchall()
        return [(str(name) if name is not None else "(空)", int(total)) for name, total in rows]

    def recent(self, limit: int = 20) -> list[sqlite3.Row]:
        with self._lock:
            self._conn.row_factory = sqlite3.Row
            cur = self._conn.execute(
                "SELECT * FROM activity_log ORDER BY id DESC LIMIT ?", (limit,)
            )
            rows = cur.fetchall()
            self._conn.row_factory = None
        return rows

    # --- 未确认（unknown）管理 ---

    def pending_unknown(self, limit: int = 50) -> list[dict]:
        """聚合所有 category='unknown' 记录，按 (process, url 主机, title 主词) 分组。
        返回 [{process, sample_title, sample_url, count, last_ts}, ...]，按 count 降序。
        """
        with self._lock:
            cur = self._conn.execute(
                "SELECT process_name, window_title, url, COUNT(*) as n, MAX(ts) as last_ts"
                " FROM activity_log"
                " WHERE category = 'unknown'"
                " GROUP BY process_name, COALESCE(url, ''), COALESCE(window_title, '')"
                " ORDER BY n DESC, last_ts DESC"
                " LIMIT ?",
                (limit,),
            )
            rows = cur.fetchall()
        return [
            {
                "process": r[0],
                "sample_title": r[1],
                "sample_url": r[2],
                "count": r[3],
                "last_ts": r[4],
            }
            for r in rows
        ]

    def unknown_count(self) -> int:
        with self._lock:
            cur = self._conn.execute(
                "SELECT COUNT(*) FROM activity_log WHERE category = 'unknown'"
            )
            return int(cur.fetchone()[0])

    # --- 批量回填 ---

    def reclassify_by_rule(self, rule, only_unknown: bool = True) -> int:
        """用一条规则扫历史记录，命中的更新 category + rule_id。返回更新的行数。

        规则可能含 process / title_regex / url_regex 三种条件（AND）。为减小扫描面，
        若有 process 字段，先用 SQL WHERE 过滤 process。剩下的 regex 在 Python 里跑。
        """
        conditions = ["category = 'unknown'" if only_unknown else "1=1"]
        params: list = []
        if rule.process:
            conditions.append("LOWER(process_name) = ?")
            params.append(rule.process.lower())
        if rule.title_regex:
            conditions.append("window_title IS NOT NULL")
        if rule.url_regex:
            conditions.append("url IS NOT NULL")
        # 排除 idle 占位行
        conditions.append("is_idle = 0")

        sql = (
            "SELECT id, process_name, window_title, url FROM activity_log"
            " WHERE " + " AND ".join(conditions)
        )

        title_re = re.compile(rule.title_regex) if rule.title_regex else None
        url_re = re.compile(rule.url_regex) if rule.url_regex else None

        with self._lock:
            cur = self._conn.execute(sql, params)
            rows = cur.fetchall()

            ids_to_update: list[int] = []
            for row_id, proc, title, url in rows:
                if title_re and (not title or not title_re.search(title)):
                    continue
                if url_re and (not url or not url_re.search(url)):
                    continue
                ids_to_update.append(row_id)

            if not ids_to_update:
                return 0

            placeholders = ",".join("?" * len(ids_to_update))
            self._conn.execute(
                f"UPDATE activity_log SET category = ?, rule_id = ?"
                f" WHERE id IN ({placeholders})",
                [rule.category, rule.id] + ids_to_update,
            )
        return len(ids_to_update)

    def reclassify_by_process(
        self,
        process_name: str,
        new_category: str,
        only_unknown: bool = False,
        rule_id: Optional[str] = None,
    ) -> int:
        """把所有 process_name 匹配的非 idle 记录改成 new_category。返回更新的行数。"""
        conditions = ["LOWER(process_name) = ?", "is_idle = 0"]
        params: list = [process_name.lower()]
        if only_unknown:
            conditions.append("category = 'unknown'")
        with self._lock:
            cur = self._conn.execute(
                "UPDATE activity_log SET category = ?, rule_id = ?"
                " WHERE " + " AND ".join(conditions),
                [new_category, rule_id] + params,
            )
            return cur.rowcount or 0


# --- 时间范围辅助 ---

def day_range(d: datetime) -> tuple[int, int]:
    start = datetime(d.year, d.month, d.day)
    end = start + timedelta(days=1)
    return int(start.timestamp()), int(end.timestamp())


def week_range(d: datetime) -> tuple[int, int]:
    monday = datetime(d.year, d.month, d.day) - timedelta(days=d.weekday())
    return int(monday.timestamp()), int((monday + timedelta(days=7)).timestamp())


def month_range(d: datetime) -> tuple[int, int]:
    start = datetime(d.year, d.month, 1)
    if d.month == 12:
        end = datetime(d.year + 1, 1, 1)
    else:
        end = datetime(d.year, d.month + 1, 1)
    return int(start.timestamp()), int(end.timestamp())
