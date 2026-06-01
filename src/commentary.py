"""今日点评：预设风格 prompt + 占位符安全构造。

被 overview_tab（生成今日点评）和 commentary_dialog（预览/配置）共用，
不依赖 AIClassifier，仅负责把占比数据填进 prompt 模板。
"""
from __future__ import annotations

from .settings import CommentarySettings

# key -> (中文标签, prompt 模板)
COMMENTARY_STYLES: dict[str, tuple[str, str]] = {
    "humorous": (
        "幽默吐槽",
        "你是一个毒舌又可爱的摸鱼监督员。根据今天的活动数据，用一句话（30 字以内）"
        "幽默地点评今天的工作状态，可调侃可鼓励，语气轻松。\n"
        "工作 {work_pct}%、摸鱼 {fishing_pct}%、中立 {neutral_pct}%、空闲 {idle_pct}%。\n"
        "直接返回点评文本，不要引号、不要 JSON、不要解释。",
    ),
    "serious": (
        "严肃总结",
        "你是一位简洁专业的效率教练。根据今天的活动占比，用一句话（30 字以内）"
        "客观地总结今天的工作状态，并给出一句中肯建议。\n"
        "工作 {work_pct}%、摸鱼 {fishing_pct}%、中立 {neutral_pct}%、空闲 {idle_pct}%。\n"
        "直接返回点评文本，不要引号、不要 JSON、不要解释。",
    ),
    "sarcastic": (
        "毒舌损友",
        "你是一个嘴上不饶人的损友。根据今天的活动占比，用一句话（30 字以内）"
        "狠狠吐槽今天的摸鱼程度（可以犀利，但不要人身攻击）。\n"
        "工作 {work_pct}%、摸鱼 {fishing_pct}%、中立 {neutral_pct}%、空闲 {idle_pct}%。\n"
        "直接返回点评文本，不要引号、不要 JSON、不要解释。",
    ),
    "encouraging": (
        "元气鼓励",
        "你是一个元气满满的啦啦队长。根据今天的活动占比，用一句话（30 字以内）"
        "温暖地鼓励对方，给人继续加油的力量。\n"
        "工作 {work_pct}%、摸鱼 {fishing_pct}%、中立 {neutral_pct}%、空闲 {idle_pct}%。\n"
        "直接返回点评文本，不要引号、不要 JSON、不要解释。",
    ),
}

DEFAULT_STYLE = "humorous"

PLACEHOLDER_KEYS = (
    "work_pct", "fishing_pct", "neutral_pct", "idle_pct",
    "work_min", "fishing_min", "neutral_min", "idle_min",
)


class _SafeDict(dict):
    """缺失占位符时原样保留 \"{key}\"，避免用户自定义 prompt 写错占位符导致崩溃。"""

    def __missing__(self, key):  # noqa: ANN001
        return "{" + key + "}"


def preset_prompt(style: str) -> str:
    """返回预设风格的 prompt 模板；未知风格回退到默认。"""
    label_prompt = COMMENTARY_STYLES.get(style) or COMMENTARY_STYLES[DEFAULT_STYLE]
    return label_prompt[1]


def _template_for(cs: CommentarySettings) -> str:
    if cs.style == "custom" and cs.custom_prompt.strip():
        return cs.custom_prompt
    return preset_prompt(cs.style)


def build_prompt(cs: CommentarySettings, totals: dict[str, int]) -> str:
    """根据点评设置 + 今日各类别秒数构造最终 prompt。"""
    work = int(totals.get("work", 0))
    fishing = int(totals.get("fishing", 0))
    neutral = int(totals.get("neutral", 0))
    idle = int(totals.get("idle", 0))
    total_all = work + fishing + neutral + idle or 1

    vals = {
        "work_pct": round(work / total_all * 100),
        "fishing_pct": round(fishing / total_all * 100),
        "neutral_pct": round(neutral / total_all * 100),
        "idle_pct": round(idle / total_all * 100),
        "work_min": work // 60,
        "fishing_min": fishing // 60,
        "neutral_min": neutral // 60,
        "idle_min": idle // 60,
    }
    return _template_for(cs).format_map(_SafeDict(vals))
