"""弹窗分类与处置决策。

本模块只包含纯函数与枚举，不访问截图、窗口句柄或平台 API。
"""

from __future__ import annotations

import re
from enum import StrEnum


class PopupKind(StrEnum):
    """运行时可能遇到的弹窗类型。"""

    NONE = "NONE"
    STEAM_EULA = "STEAM_EULA"
    STEAM_UPDATE = "STEAM_UPDATE"
    STEAM_AD = "STEAM_AD"
    GAME_CRASH = "GAME_CRASH"
    UNKNOWN = "UNKNOWN"


class PopupDisposition(StrEnum):
    """弹窗处置决策。"""

    IGNORE = "IGNORE"
    CLOSE = "CLOSE"
    PRESERVE = "PRESERVE"
    MANUAL_INTERVENTION = "MANUAL_INTERVENTION"


def classify_popup(title: str, text: str) -> PopupKind:
    """基于标题与正文关键词分类弹窗。"""
    normalized_title = title.strip().casefold()
    normalized_text = text.strip().casefold()
    combined = f"{normalized_title} {normalized_text}"

    if not normalized_title and not normalized_text:
        return PopupKind.NONE

    if "slay the spire 2" in combined or _contains_crash_text(normalized_text):
        return PopupKind.GAME_CRASH

    if "steam" in combined:
        if "end user license agreement" in combined or "eula" in combined:
            return PopupKind.STEAM_EULA
        if "update required" in combined or "update before launch" in combined:
            return PopupKind.STEAM_UPDATE
        if (
            re.search(r"\bad\b", combined) is not None
            or "sale" in combined
            or "special offer" in combined
            or "advertisement" in combined
        ):
            return PopupKind.STEAM_AD

    return PopupKind.UNKNOWN


def decide_popup_disposition(kind: PopupKind) -> PopupDisposition:
    """把弹窗类型映射为恢复流程可执行的处置方式。"""
    if kind == PopupKind.GAME_CRASH:
        return PopupDisposition.PRESERVE
    if kind in {PopupKind.STEAM_EULA, PopupKind.STEAM_UPDATE, PopupKind.STEAM_AD}:
        return PopupDisposition.CLOSE
    if kind == PopupKind.NONE:
        return PopupDisposition.IGNORE
    return PopupDisposition.MANUAL_INTERVENTION


def _contains_crash_text(text: str) -> bool:
    return "application has crashed" in text or "crashed" in text
