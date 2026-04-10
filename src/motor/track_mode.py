"""MQTT car/control/track 载荷解析 — 与 doc/MQTT通信配置手册 §3.4 一致。"""

from __future__ import annotations

from typing import Any, Dict, Literal, Optional

TrackMode = Literal["normal", "track"]


def parse_track_mode_message(data: Dict[str, Any]) -> Optional[TrackMode]:
    if not data:
        return None
    raw = data.get("mode")
    if raw is None:
        return None
    s = str(raw).strip().lower()
    if s == "normal":
        return "normal"
    if s == "track":
        return "track"
    return None
