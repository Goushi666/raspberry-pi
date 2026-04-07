"""
小车 MQTT 车控：与 doc/MQTT通信配置手册_树莓派与Web后端.md §3.3 一致。
action ∈ forward | backward | left | right | stop；speed 为 0~100 PWM 占空比。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, Optional

if TYPE_CHECKING:
    from motor.controller import VehicleController
    from vision.tracker import LineTracker


def merge_control_payload(data: Dict[str, Any]) -> Dict[str, Any]:
    if not data:
        return {}
    out = dict(data)
    params = out.get("params")
    if isinstance(params, dict):
        merged = dict(params)
        merged.update(out)
        out = merged
    return out


_ACTION_ALIASES: Dict[str, str] = {
    "forward": "forward",
    "fwd": "forward",
    "go": "forward",
    "backward": "backward",
    "back": "backward",
    "reverse": "backward",
    "left": "left",
    "turn_left": "left",
    "l": "left",
    "right": "right",
    "turn_right": "right",
    "r": "right",
    "stop": "stop",
    "brake": "stop",
    "auto": "auto",
    "manual": "manual",
    "前进": "forward",
    "后退": "backward",
    "左转": "left",
    "右转": "right",
    "停止": "stop",
}

_VALID_ACTIONS = frozenset(
    {"forward", "backward", "left", "right", "stop", "auto", "manual"}
)


@dataclass(frozen=True)
class CarControlMessage:
    action: str
    speed: int
    duration_sec: int


def parse_car_control_message(data: Dict[str, Any]) -> Optional[CarControlMessage]:
    """解析 MQTT JSON；无法识别时返回 None。"""
    d = merge_control_payload(data)
    raw_act: Any = None
    for key in ("action", "cmd", "command", "direction"):
        if d.get(key) is not None:
            raw_act = d.get(key)
            break
    if raw_act is None:
        return None
    s = str(raw_act).strip()
    if not s:
        return None
    lookup = s.lower() if s.isascii() else s
    action = _ACTION_ALIASES.get(lookup) or (
        lookup if lookup in _VALID_ACTIONS else None
    )
    if action is None:
        return None

    speed_raw = d.get("speed", d.get("velocity", 50))
    try:
        speed = int(float(speed_raw))
    except (TypeError, ValueError):
        speed = 50
    speed = max(0, min(100, speed))

    dur_raw = d.get("duration", 0)
    try:
        duration_sec = int(float(dur_raw))
    except (TypeError, ValueError):
        duration_sec = 0
    duration_sec = max(0, duration_sec)

    return CarControlMessage(action=action, speed=speed, duration_sec=duration_sec)


def apply_car_motion(
    controller: VehicleController,
    tracker: Optional[LineTracker],
    msg: CarControlMessage,
) -> bool:
    """
    按手册执行车轮动作。返回 True 表示已执行运动类指令（含 stop），
    False 表示未匹配（如 auto 但无 tracker）。
    """
    a = msg.action
    sp = msg.speed

    if a == "forward":
        controller.forward(sp)
        return True
    if a == "backward":
        controller.backward(sp)
        return True
    if a == "left":
        controller.turn_left(sp)
        return True
    if a == "right":
        controller.turn_right(sp)
        return True
    if a == "stop":
        controller.stop()
        return True
    if a == "auto":
        if tracker is not None:
            tracker.start()
            return True
        return False
    if a == "manual":
        if tracker is not None:
            tracker.stop()
            return True
        return False
    return False
