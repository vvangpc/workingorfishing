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
    rule_id TEXT
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
    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = Path(path) if path else db_file()
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
    ) -> None:
        ts = ts if ts is not None else int(time.time())
        with self._lock:
            self._conn.execute(
                "INSERT INTO activity_log"
                " (ts, process_name, window_title, url, category, is_idle, rule_id)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    ts,
                    process_name,
                    window_title,
                    url,
                    category,
                    1 if is_idle else 0,
                    rule_id,
                ),
            )

    # --- 聚合 ---

    def aggregate_range(
        self, start_ts: int, end_ts: int, sample_interval: int
    ) -> dict[str, int]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT category, COUNT(*) FROM activity_log"
                " WHERE ts >= ? AND ts < ? GROUP BY category",
                (start_ts, end_ts),
            )
            rows = cur.fetchall()
        return {cat: count * sample_interval for cat, count in rows}

    def buckets(
        self,
        start_ts: int,
        end_ts: int,
        bucket_seconds: int,
        sample_interval: int,
    ) -> list[tuple[int, dict[str, int]]]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT ((ts - ?) / ?) AS bucket, category, COUNT(*)"
                " FROM activity_log"
                " WHERE ts >= ? AND ts < ?"
                " GROUP BY bucket, category"
                " ORDER BY bucket",
                (start_ts, bucket_seconds, start_ts, end_ts),
            )
            rows = cur.fetchall()
        out: dict[int, dict[str, int]] = {}
        for bucket, cat, count in rows:
            out.setdefault(bucket, {})[cat] = count * sample_interval
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
            "SELECT COALESCE(process_name, '(unknown)'), COUNT(*) FROM activity_log"
            " WHERE ts >= ? AND ts < ?"
        )
        params: list = [start_ts, end_ts]
        if category:
            sql += " AND category = ?"
            params.append(category)
        sql += " GROUP BY process_name ORDER BY COUNT(*) DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            cur = self._conn.execute(sql, params)
            rows = cur.fetchall()
        return [(name, count * sample_interval) for name, count in rows]

    def top_urls(
        self,
        start_ts: int,
        end_ts: int,
        category: Optional[str],
        sample_interval: int,
        limit: int = 10,
    ) -> list[tuple[str, int]]:
        sql = (
            "SELECT url, COUNT(*) FROM activity_log"
            " WHERE ts >= ? AND ts < ? AND url IS NOT NULL AND url != ''"
        )
        params: list = [start_ts, end_ts]
        if category:
            sql += " AND category = ?"
            params.append(category)
        sql += " GROUP BY url ORDER BY COUNT(*) DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            cur = self._conn.execute(sql, params)
            rows = cur.fetchall()
        return [(url, count * sample_interval) for url, count in rows]

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
                "SELECT process_name, COUNT(*) FROM activity_log"
                " WHERE ts >= ? AND ts < ? AND category = ? AND is_idle = 0"
                " GROUP BY process_name"
                " ORDER BY COUNT(*) DESC"
                " LIMIT ?",
                (start_ts, end_ts, category, limit),
            )
            rows = cur.fetchall()
        return [(name, count * sample_interval) for name, count in rows]

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
            f"SELECT {bucket_expr} AS bucket, COUNT(*) FROM activity_log"
            " WHERE ts >= ? AND ts < ? AND category = ? AND is_idle = 0"
        )
        params: list = [start_ts, end_ts, category]
        if process_name is None:
            sql += " AND process_name IS NULL"
        else:
            sql += " AND LOWER(process_name) = ?"
            params.append(process_name.lower())
        sql += " GROUP BY bucket ORDER BY COUNT(*) DESC LIMIT ?"
        params.append(limit)

        with self._lock:
            cur = self._conn.execute(sql, params)
            rows = cur.fetchall()
        return [(str(name) if name is not None else "(空)", count * sample_interval) for name, count in rows]

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
