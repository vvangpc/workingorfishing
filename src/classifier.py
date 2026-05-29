"""分类规则加载、匹配、持久化。

新规则文件格式（GUI 写入）：
    default: neutral
    rules:
      - id: "uuid"
        category: work | fishing | neutral
        process: "Code.exe"        # 可选
        title_regex: "..."         # 可选
        url_regex: "..."           # 可选
        priority: 10               # 小的先匹配
        enabled: true
        source: builtin | user | ai
        note: ""

兼容旧格式（categories: { work: [...], fishing: [...] }）：读到时自动转换并落盘。
匹配 fall-through 到 unknown（不再用 default 直接归 neutral）；
当所有规则都不命中时返回 ('unknown', None)，由调用方决定后续处理。
"""
from __future__ import annotations

import logging
import re
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Pattern

import yaml

from .paths import bundled_default_rules, rules_file

logger = logging.getLogger(__name__)

VALID_CATEGORIES = ("work", "fishing", "neutral")
SOURCE_BUILTIN = "builtin"
SOURCE_USER = "user"
SOURCE_AI = "ai"


@dataclass
class Rule:
    id: str
    category: str
    process: Optional[str] = None
    title_regex: Optional[str] = None
    url_regex: Optional[str] = None
    priority: int = 100
    enabled: bool = True
    source: str = SOURCE_USER
    note: str = ""

    # 运行期缓存的编译后正则，不入 YAML
    _title_re: Optional[Pattern] = field(default=None, repr=False, compare=False)
    _url_re: Optional[Pattern] = field(default=None, repr=False, compare=False)

    @classmethod
    def new(
        cls,
        *,
        category: str,
        process: Optional[str] = None,
        title_regex: Optional[str] = None,
        url_regex: Optional[str] = None,
        priority: int = 100,
        source: str = SOURCE_USER,
        note: str = "",
    ) -> "Rule":
        return cls(
            id=uuid.uuid4().hex,
            category=category,
            process=process or None,
            title_regex=title_regex or None,
            url_regex=url_regex or None,
            priority=priority,
            enabled=True,
            source=source,
            note=note,
        )

    def compile(self) -> bool:
        """编译正则，返回是否成功。失败的规则后续会被跳过。"""
        try:
            self._title_re = re.compile(self.title_regex) if self.title_regex else None
            self._url_re = re.compile(self.url_regex) if self.url_regex else None
            return True
        except re.error as e:
            logger.warning("bad regex in rule %s: %s", self.id, e)
            self._title_re = None
            self._url_re = None
            return False

    def has_any_field(self) -> bool:
        return any((self.process, self.title_regex, self.url_regex))

    def matches(
        self,
        process: Optional[str],
        title: Optional[str],
        url: Optional[str],
    ) -> bool:
        if not self.has_any_field():
            return False
        if self.process is not None:
            if not process or process.lower() != self.process.lower():
                return False
        if self.title_regex is not None:
            if self._title_re is None or not title or not self._title_re.search(title):
                return False
        if self.url_regex is not None:
            if self._url_re is None or not url or not self._url_re.search(url):
                return False
        return True

    def to_yaml_dict(self) -> dict:
        d = {
            "id": self.id,
            "category": self.category,
            "priority": int(self.priority),
            "enabled": bool(self.enabled),
            "source": self.source,
            "note": self.note or "",
        }
        if self.process:
            d["process"] = self.process
        if self.title_regex:
            d["title_regex"] = self.title_regex
        if self.url_regex:
            d["url_regex"] = self.url_regex
        return d


def _ensure_user_rules_exist() -> Path:
    user_path = rules_file()
    if not user_path.exists():
        src = bundled_default_rules()
        if src.exists():
            shutil.copy(src, user_path)
        else:
            user_path.write_text("default: neutral\nrules: []\n", encoding="utf-8")
    return user_path


def _convert_legacy(data: dict) -> tuple[list[Rule], bool]:
    """旧格式 categories: { work: [...] } → 扁平 rules: [...]。
    返回 (rules, changed)。changed=True 表示发生了迁移，调用方应回写文件。"""
    if "rules" in data and isinstance(data["rules"], list):
        return [], False  # 已是新格式
    cats = data.get("categories") or {}
    if not cats:
        return [], False
    rules: list[Rule] = []
    priority = 10
    for cat_name, items in cats.items():
        if cat_name not in VALID_CATEGORIES:
            continue
        for item in items or []:
            if not isinstance(item, dict):
                continue
            r = Rule.new(
                category=cat_name,
                process=item.get("process"),
                title_regex=item.get("title_regex"),
                url_regex=item.get("url_regex"),
                priority=priority,
                source=SOURCE_BUILTIN,
            )
            rules.append(r)
            priority += 10
    return rules, True


class Classifier:
    """规则容器 + 匹配引擎。

    线程模型：GUI 线程修改规则（add/update/delete/reload），collector 线程只读 classify()。
    用一份不可变的 rules 列表（每次写都重建），读不加锁也安全。
    """

    def __init__(self) -> None:
        self._path: Path = _ensure_user_rules_exist()
        self._mtime: float = 0.0
        self._default: str = "neutral"
        self._rules: list[Rule] = []
        self.reload(force=True)

    # --- 加载 / 保存 ---

    def _check_reload(self) -> None:
        try:
            m = self._path.stat().st_mtime
        except OSError:
            return
        if m != self._mtime:
            self.reload(force=True)

    def reload(self, force: bool = False) -> None:
        try:
            text = self._path.read_text(encoding="utf-8")
            data = yaml.safe_load(text) or {}
        except Exception:
            logger.exception("failed to load rules; keeping previous")
            return

        rules, migrated = _convert_legacy(data)
        if not migrated:
            # 新格式
            for item in data.get("rules") or []:
                if not isinstance(item, dict):
                    continue
                r = self._build_from_dict(item)
                if r is not None:
                    rules.append(r)

        # 编译正则
        for r in rules:
            r.compile()

        # 按 priority 升序，相同 priority 保留原顺序
        rules.sort(key=lambda r: r.priority)
        self._rules = rules
        default = data.get("default") or "neutral"
        self._default = default if default in VALID_CATEGORIES else "neutral"

        try:
            self._mtime = self._path.stat().st_mtime
        except OSError:
            self._mtime = 0.0

        logger.info("classifier loaded %d rules from %s", len(rules), self._path)
        if migrated:
            logger.info("migrated rules from legacy format; saving new format")
            self.save()

    @staticmethod
    def _build_from_dict(item: dict) -> Optional[Rule]:
        category = item.get("category")
        if category not in VALID_CATEGORIES:
            return None
        return Rule(
            id=item.get("id") or uuid.uuid4().hex,
            category=category,
            process=item.get("process") or None,
            title_regex=item.get("title_regex") or None,
            url_regex=item.get("url_regex") or None,
            priority=int(item.get("priority", 100)),
            enabled=bool(item.get("enabled", True)),
            source=item.get("source") or SOURCE_USER,
            note=item.get("note") or "",
        )

    def save(self) -> None:
        data = {
            "default": self._default,
            "rules": [r.to_yaml_dict() for r in self._rules],
        }
        text = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
        self._path.write_text(text, encoding="utf-8")
        try:
            self._mtime = self._path.stat().st_mtime
        except OSError:
            self._mtime = 0.0

    # --- 规则增删改（GUI 用） ---

    def get_rules(self) -> list[Rule]:
        return list(self._rules)

    def get_rule(self, rule_id: str) -> Optional[Rule]:
        for r in self._rules:
            if r.id == rule_id:
                return r
        return None

    def add_rule(self, rule: Rule, save: bool = True) -> None:
        rule.compile()
        new = list(self._rules) + [rule]
        new.sort(key=lambda r: r.priority)
        self._rules = new
        if save:
            self.save()

    def update_rule(self, rule: Rule, save: bool = True) -> None:
        rule.compile()
        new = [r for r in self._rules if r.id != rule.id]
        new.append(rule)
        new.sort(key=lambda r: r.priority)
        self._rules = new
        if save:
            self.save()

    def delete_rule(self, rule_id: str, save: bool = True) -> None:
        self._rules = [r for r in self._rules if r.id != rule_id]
        if save:
            self.save()

    def set_enabled(self, rule_id: str, enabled: bool, save: bool = True) -> None:
        for r in self._rules:
            if r.id == rule_id:
                r.enabled = enabled
                break
        if save:
            self.save()

    def merge_rules_file(self, other_path: Path) -> int:
        """把另一个 rules.yaml 里本地没有的规则（按 id 去重）并入当前规则集。
        返回新增条数。WebDAV 合并同步用。"""
        p = Path(other_path)
        if not p.exists():
            return 0
        try:
            data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except Exception:
            logger.exception("merge_rules_file: failed to read %s", p)
            return 0

        incoming, _ = _convert_legacy(data)
        if not incoming:
            for item in data.get("rules") or []:
                if isinstance(item, dict):
                    r = self._build_from_dict(item)
                    if r is not None:
                        incoming.append(r)

        existing_ids = {r.id for r in self._rules}
        added = [r for r in incoming if r.id not in existing_ids]
        if not added:
            return 0
        for r in added:
            r.compile()
        new = list(self._rules) + added
        new.sort(key=lambda r: r.priority)
        self._rules = new
        self.save()
        return len(added)

    def replace_all(self, rules: list[Rule], save: bool = True) -> None:
        for r in rules:
            r.compile()
        rules.sort(key=lambda r: r.priority)
        self._rules = rules
        if save:
            self.save()

    # --- 匹配 ---

    def classify(
        self,
        process: Optional[str],
        title: Optional[str],
        url: Optional[str],
    ) -> tuple[str, Optional[str]]:
        """返回 (category, rule_id)。未命中返回 ('unknown', None)。"""
        self._check_reload()
        for r in self._rules:
            if not r.enabled:
                continue
            if r.matches(process, title, url):
                return r.category, r.id
        return "unknown", None

    def find_matching_rule(
        self,
        process: Optional[str],
        title: Optional[str],
        url: Optional[str],
    ) -> Optional[Rule]:
        """测试匹配用：返回首个命中的规则（含 disabled），不命中返回 None。"""
        for r in self._rules:
            if r.matches(process, title, url):
                return r
        return None

    @property
    def default_category(self) -> str:
        return self._default
