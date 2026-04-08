"""
机械臂 / 云台 MQTT 载荷：doc/MQTT通信配置手册_树莓派与Web后端.md §3.4。
机械臂 joint=0–5；云台 joint=6（水平）、joint=7（俯仰）。见 servo.pca_arm / pca_gimbal。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


def merge_arm_payload(data: Dict[str, Any]) -> Dict[str, Any]:
    if not data:
        return {}
    out = dict(data)
    params = out.get("params")
    if isinstance(params, dict):
        merged = dict(params)
        merged.update(out)
        out = merged
    return out


@dataclass(frozen=True)
class ArmControlMessage:
    joint: int
    angle: int
    speed: int


def parse_arm_control_message(data: Dict[str, Any]) -> Optional[ArmControlMessage]:
    d = merge_arm_payload(data)
    raw_j = d.get("joint")
    if raw_j is None:
        return None
    try:
        joint = int(raw_j)
    except (TypeError, ValueError):
        return None

    raw_a = d.get("angle", 90)
    try:
        angle = int(float(raw_a))
    except (TypeError, ValueError):
        angle = 90
    angle = max(0, min(180, angle))

    raw_s = d.get("speed", 50)
    try:
        speed = int(float(raw_s))
    except (TypeError, ValueError):
        speed = 50
    speed = max(0, min(100, speed))

    return ArmControlMessage(joint=joint, angle=angle, speed=speed)
