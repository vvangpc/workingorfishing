"""OpenAI 兼容协议 LLM 兜底分类。

工作模型：
  - collector 把 unknown 样本通过 enqueue(sample) 投递
  - 后台线程按 batch_size / interval_seconds 取出 → 并发 N 个 HTTP 调用 → 发 suggestion_ready 信号
  - GUI 在概览 Tab 收集 suggestion，用户确认后转成规则 + 历史回填

任何错误（网络、JSON 解析、不合法 category）都不阻塞主流程，只记日志 + 计数。
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import dataclass
from queue import Empty, Queue
from typing import Optional

import httpx
from PySide6.QtCore import QObject, Signal

from .classifier import VALID_CATEGORIES
from .settings import AISettings

logger = logging.getLogger(__name__)


@dataclass
class AISuggestion:
    process: Optional[str]
    title: Optional[str]
    url: Optional[str]
    category: str
    reason: str
    suggested_process: Optional[str]
    suggested_title_regex: Optional[str]
    suggested_url_regex: Optional[str]


def _strip_md_fence(text: str) -> str:
    """模型偶尔会用 ```json ... ``` 包裹，去掉。"""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```\s*$", "", text)
    return text


class AIClassifier(QObject):
    suggestion_ready = Signal(object)   # AISuggestion
    error_occurred = Signal(str)
    stats_changed = Signal(dict)        # {calls, errors, last_error_ts}

    def __init__(self, settings: AISettings, parent=None):
        super().__init__(parent)
        self._settings = settings
        self._queue: Queue = Queue(maxsize=200)
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._stats = {"calls": 0, "errors": 0, "last_error": ""}

    # --- 控制 ---

    def configure(self, settings: AISettings) -> None:
        self._settings = settings

    @property
    def is_ready(self) -> bool:
        return bool(self._settings.enabled and self._settings.api_key)

    @property
    def settings(self) -> AISettings:
        return self._settings

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="AIClassifier")
        self._thread.start()
        logger.info("AIClassifier started")

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None
        logger.info("AIClassifier stopped")

    def enqueue(self, sample: dict) -> None:
        """sample: {process, title, url}。enabled=False 时不入队。"""
        if not self._settings.enabled:
            return
        try:
            self._queue.put_nowait(sample)
        except Exception:
            pass

    def queue_size(self) -> int:
        return self._queue.qsize()

    @property
    def stats(self) -> dict:
        return dict(self._stats)

    # --- 自然语言生成规则（ClassifyDialog 用） ---

    _RULE_GEN_PROMPT = (
        "你是一个分类规则生成助手。根据下面的自然语言描述，输出一条 JSON 格式的活动分类规则。\n\n"
        "用户描述：\n{description}\n\n"
        "可用字段：\n"
        "- category: 必填，必须是 'work' / 'fishing' / 'neutral' 之一\n"
        "- process: 进程名精确匹配（如 Code.exe）；用不到设 null\n"
        "- title_regex: 窗口标题 Python 正则（建议加 (?i) 前缀）；用不到设 null\n"
        "- url_regex: 浏览器 URL Python 正则（点 . 要转义为 \\.）；用不到设 null\n"
        "- priority: 数字优先级，默认 100，数字越小越先匹配\n"
        "- note: 一句话备注\n\n"
        "约束：\n"
        "- process / title_regex / url_regex 至少要有一个\n"
        "- 描述里出现网站名（如 LinkedIn、知乎、X、YouTube），优先用 url_regex\n"
        "- 描述里出现具体软件名（如 微信、QQ、PotPlayer），优先用 process\n"
        "- 多个候选 URL 用 (a|b|c) 分组合并到一条规则\n\n"
        "只返回 JSON 对象，不要 markdown 围栏。"
    )

    def generate_rule(
        self, description: str, timeout: float = 30.0
    ) -> tuple[Optional[dict], str]:
        """根据自然语言描述生成规则字段。返回 (rule_dict, raw_response)。失败时 rule_dict=None，raw=错误信息。"""
        if not self._settings.api_key:
            return None, "AI 未配置 api_key（设置 → 悬浮窗… 不是这里，去『设置 → AI 判断』填）"
        prompt = self._RULE_GEN_PROMPT.format(description=description)
        url = self._settings.base_url.rstrip("/") + "/chat/completions"
        body = {
            "model": self._settings.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": float(self._settings.temperature),
        }
        headers = {
            "Authorization": f"Bearer {self._settings.api_key}",
            "Content-Type": "application/json",
        }
        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.post(url, json=body, headers=headers)
                resp.raise_for_status()
                data = resp.json()
            content = data["choices"][0]["message"]["content"]
        except Exception as e:
            return None, f"调用失败: {e}"

        cleaned = _strip_md_fence(content)
        obj = None
        try:
            obj = json.loads(cleaned)
        except json.JSONDecodeError:
            m = re.search(r"\{[\s\S]*\}", cleaned)
            if m:
                try:
                    obj = json.loads(m.group(0))
                except Exception:
                    pass
        if not isinstance(obj, dict):
            return None, f"无法解析 JSON：{content[:200]}"

        cat = obj.get("category")
        if cat not in VALID_CATEGORIES:
            return None, f"无效的 category: {cat!r}\n原始返回:\n{content[:300]}"
        process = (obj.get("process") or None) or None
        title_regex = (obj.get("title_regex") or None) or None
        url_regex = (obj.get("url_regex") or None) or None
        if not any((process, title_regex, url_regex)):
            return None, "AI 未给出任何匹配字段（process / title_regex / url_regex 全为空）"

        return (
            {
                "category": cat,
                "process": process,
                "title_regex": title_regex,
                "url_regex": url_regex,
                "priority": int(obj.get("priority", 100) or 100),
                "note": str(obj.get("note") or "AI 生成"),
            },
            content,
        )

    # --- 同步测试（AI Tab 用） ---

    def test(self, sample: dict) -> tuple[Optional[AISuggestion], str]:
        """单次调用，返回 (suggestion, raw_response)。失败时 suggestion=None，raw_response 为错误信息。"""
        try:
            content = self._call_llm(sample, timeout=30.0)
            sug = self._parse(sample, content)
            return sug, content
        except Exception as e:
            return None, f"调用失败: {e}"

    # --- 后台线程 ---

    def _run(self) -> None:
        while not self._stop_event.is_set():
            interval = max(5, int(self._settings.interval_seconds))
            batch_size = max(1, int(self._settings.batch_size))
            if self._stop_event.wait(interval):
                break
            if not self._settings.enabled:
                continue

            batch: list[dict] = []
            for _ in range(batch_size):
                try:
                    batch.append(self._queue.get_nowait())
                except Empty:
                    break
            if not batch:
                continue
            for sample in batch:
                if self._stop_event.is_set():
                    return
                try:
                    content = self._call_llm(sample, timeout=20.0)
                    sug = self._parse(sample, content)
                    if sug:
                        self.suggestion_ready.emit(sug)
                    self._stats["calls"] += 1
                except Exception as e:
                    self._stats["errors"] += 1
                    self._stats["last_error"] = str(e)
                    logger.warning("AI call failed: %s", e)
                    self.error_occurred.emit(str(e))
                self.stats_changed.emit(dict(self._stats))

    # --- LLM 调用与解析 ---

    def _call_llm(self, sample: dict, timeout: float) -> str:
        s = self._settings
        if not s.api_key:
            raise RuntimeError("AI 未配置 api_key")
        prompt = s.prompt_template.format(
            process=sample.get("process") or "(无)",
            title=sample.get("title") or "(无)",
            url=sample.get("url") or "(无)",
        )
        url = s.base_url.rstrip("/") + "/chat/completions"
        body = {
            "model": s.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": float(s.temperature),
        }
        headers = {
            "Authorization": f"Bearer {s.api_key}",
            "Content-Type": "application/json",
        }
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(url, json=body, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise RuntimeError(f"unexpected response shape: {data!r}") from e

    def _parse(self, sample: dict, content: str) -> Optional[AISuggestion]:
        cleaned = _strip_md_fence(content)
        try:
            obj = json.loads(cleaned)
        except json.JSONDecodeError:
            # 尝试在文本里找第一个 { ... }
            m = re.search(r"\{[\s\S]*\}", cleaned)
            if not m:
                logger.warning("AI response not JSON: %s", content[:200])
                return None
            try:
                obj = json.loads(m.group(0))
            except json.JSONDecodeError:
                logger.warning("AI response JSON parse failed: %s", content[:200])
                return None

        cat = obj.get("category")
        if cat not in VALID_CATEGORIES:
            logger.warning("AI returned invalid category: %r", cat)
            return None
        sug = obj.get("suggested_rule") or {}
        return AISuggestion(
            process=sample.get("process"),
            title=sample.get("title"),
            url=sample.get("url"),
            category=cat,
            reason=str(obj.get("reason") or ""),
            suggested_process=(sug.get("process") or None),
            suggested_title_regex=(sug.get("title_regex") or None),
            suggested_url_regex=(sug.get("url_regex") or None),
        )
