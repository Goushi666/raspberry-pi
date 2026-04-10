"""
地面黑线循迹（差速 PWM）+ 叠加可视化 — 与 scripts/line_follow_steering_preview.py 同源逻辑。

由视频编码线程在每帧上调用 process_frame：循迹关闭时原样返回；开启时算左右轮速并写 L298N，返回带 HUD 的画面供 MJPEG。

分辨率说明：预览脚本默认 640 宽；主程序 video_stream 常为 1280。corr = kp * deviation 里 deviation 为像素，
同样物理偏差在宽图上约按宽度比例变大，等效 P 增益过高易左右晃。故对用于 corr 的偏差按 kp_reference_width
缩放（默认 640，与预览一致）；设为 0 则关闭缩放（自行按当前分辨率调 kp）。
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any, Optional, Tuple

import cv2
import numpy as np

log = logging.getLogger(__name__)


@dataclass
class LineFollowConfig:
    # 以下默认与 scripts/line_follow_steering_preview.py 的 argparse 默认一致
    threshold: int = 66
    roi_y0: float = 0.6
    base_speed: int = 28
    max_speed: int = 30
    kp: float = 0.10
    # kp 按「标定宽度」缩放：预览默认 width=640；0 表示不缩放
    kp_reference_width: float = 640.0
    blur: int = 3
    min_line_area: int = 1200
    min_line_area_frac: float = 0.012
    morph_open: bool = True
    off_center_slow: bool = True
    # 与预览脚本一致：scale = 1 - off_center_slow_gain * t（t 已为 |dx|/半幅）
    off_center_slow_gain: float = 0.72
    turn_slow: bool = True
    turn_slow_gain: float = 0.5
    turn_slow_min_scale: float = 0.28

    @classmethod
    def from_config(cls, root: dict) -> LineFollowConfig:
        d = (root.get("line_follow") or {}) if isinstance(root, dict) else {}
        blur = int(d.get("blur", 3))
        if blur not in (0, 3, 5, 7):
            blur = 3
        kpref = d.get("kp_reference_width", 640.0)
        try:
            kpref_f = float(kpref)
        except (TypeError, ValueError):
            kpref_f = 640.0
        return cls(
            threshold=int(d.get("threshold", 66)),
            roi_y0=float(d.get("roi_y0", 0.6)),
            base_speed=max(0, min(100, int(d.get("base_speed", 28)))),
            max_speed=max(1, min(100, int(d.get("max_speed", 30)))),
            kp=float(d.get("kp", 0.10)),
            kp_reference_width=kpref_f,
            blur=blur,
            min_line_area=max(0, int(d.get("min_line_area", 1200))),
            min_line_area_frac=max(0.0, float(d.get("min_line_area_frac", 0.012))),
            morph_open=bool(d.get("morph_open", True)),
            off_center_slow=bool(d.get("off_center_slow", True)),
            off_center_slow_gain=max(0.0, min(1.0, float(d.get("off_center_slow_gain", 0.72)))),
            turn_slow=bool(d.get("turn_slow", True)),
            turn_slow_gain=max(0.0, min(1.0, float(d.get("turn_slow_gain", 0.5)))),
            turn_slow_min_scale=max(0.15, min(1.0, float(d.get("turn_slow_min_scale", 0.28)))),
        )


def build_line_mask(
    gray: np.ndarray,
    threshold: int,
    blur_ksize: Optional[Tuple[int, int]],
    morph_open: bool,
) -> np.ndarray:
    if blur_ksize is not None:
        g = cv2.GaussianBlur(gray, blur_ksize, 0)
    else:
        g = gray
    _, binary = cv2.threshold(g, threshold, 255, cv2.THRESH_BINARY_INV)
    if morph_open:
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, k, iterations=1)
    return binary


def line_cx_in_roi(
    binary_roi: np.ndarray,
    min_area_abs: int,
    min_area_frac: float,
) -> Tuple[Optional[int], Optional[Any]]:
    contours, _ = cv2.findContours(binary_roi, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None, None
    rh, rw = binary_roi.shape[:2]
    roi_area = max(1, rh * rw)
    min_a = max(int(min_area_abs), int(roi_area * float(min_area_frac)))
    big = [c for c in contours if cv2.contourArea(c) >= min_a]
    if not big:
        return None, None
    largest = max(big, key=cv2.contourArea)
    M = cv2.moments(largest)
    if M["m00"] == 0:
        return None, None
    cx = int(M["m10"] / M["m00"])
    return cx, largest


def overlay_and_speeds(
    frame_bgr: np.ndarray,
    cfg: LineFollowConfig,
    base_speed_override: Optional[int] = None,
) -> Tuple[np.ndarray, int, int, str, bool]:
    """
    返回 (叠加后 BGR, left_pwm, right_pwm, status, 是否检出中线)。
    base_speed_override：非 None 时用其代替 cfg.base_speed（便于热调，当前未接 MQTT）。
    """
    h, w = frame_bgr.shape[:2]
    roi_frac = max(0.05, min(0.95, float(cfg.roi_y0)))
    y0 = int(h * roi_frac)
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    blur_k = int(cfg.blur)
    ksize = (blur_k, blur_k) if blur_k > 0 else None
    binary_full = build_line_mask(gray, int(cfg.threshold), ksize, bool(cfg.morph_open))
    roi_bin = binary_full[y0:h, :]
    cx, cnt = line_cx_in_roi(roi_bin, cfg.min_line_area, cfg.min_line_area_frac)
    mid_x = w // 2

    out = frame_bgr.copy()
    cv2.rectangle(out, (0, y0), (w - 1, h - 1), (80, 180, 255), 2)
    cv2.line(out, (mid_x, y0), (mid_x, h - 1), (200, 200, 200), 1)

    left_sp = right_sp = 0
    status = "LOST"
    ok = False
    base = int(base_speed_override) if base_speed_override is not None else int(cfg.base_speed)
    base = max(0, min(100, base))
    max_sp = max(1, min(100, int(cfg.max_speed)))

    if cx is not None:
        ok = True
        shift = np.array([[[0, y0]]], dtype=np.int32)
        cv2.drawContours(out, [cnt + shift], -1, (0, 255, 100), 2)
        cv2.line(out, (cx, y0), (cx, h - 1), (0, 255, 255), 2)

        deviation = float(cx - mid_x)
        ref_w = float(cfg.kp_reference_width)
        if ref_w > 1.0:
            deviation_for_kp = deviation * (ref_w / float(w))
        else:
            deviation_for_kp = deviation
        corr = float(cfg.kp) * deviation_for_kp
        ls = float(base - corr)
        rs = float(base + corr)
        if cfg.off_center_slow:
            half = max(1.0, w / 2.0)
            t = min(1.0, abs(deviation) / half)
            g = float(cfg.off_center_slow_gain)
            scale = max(0.05, 1.0 - g * t)
            ls *= scale
            rs *= scale
        if cfg.turn_slow and base > 0:
            pair_diff = abs(ls - rs)
            max_pair = 2.0 * float(base)
            turn_t = min(1.0, pair_diff / max(max_pair, 1.0))
            floor = float(cfg.turn_slow_min_scale)
            turn_scale = max(floor, 1.0 - float(cfg.turn_slow_gain) * turn_t)
            ls *= turn_scale
            rs *= turn_scale
        left_sp = int(max(0, min(max_sp, round(ls))))
        right_sp = int(max(0, min(max_sp, round(rs))))
        status = f"dx={deviation:+.0f}px  L={left_sp} R={right_sp} base={base}"

    bar_y = h - 36
    bar_h = 22
    bw = (w - 60) // 2
    cv2.rectangle(out, (10, bar_y), (10 + bw, bar_y + bar_h), (60, 60, 60), 1)
    cv2.rectangle(out, (30 + bw, bar_y), (30 + 2 * bw, bar_y + bar_h), (60, 60, 60), 1)
    lw = int(bw * left_sp / 100)
    rw = int(bw * right_sp / 100)
    cv2.rectangle(out, (10, bar_y), (10 + lw, bar_y + bar_h), (80, 160, 255), -1)
    cv2.rectangle(out, (30 + bw, bar_y), (30 + bw + rw, bar_y + bar_h), (80, 160, 255), -1)

    cv2.putText(
        out,
        f"thr={cfg.threshold} minA={cfg.min_line_area}  TRACK  {status}",
        (10, 28),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.55,
        (0, 240, 0) if ok else (0, 80, 255),
        2,
        cv2.LINE_AA,
    )
    return out, left_sp, right_sp, status, ok


class LineFollowController:
    """
    循迹开关与每帧处理。process_frame 在 FrameSource 线程内调用，与 MQTT 回调用同一把锁保护 _enabled。
    """

    def __init__(
        self,
        logger: logging.Logger,
        driver: Any,
        cfg: LineFollowConfig,
    ):
        self.log = logger
        self._driver = driver
        self.cfg = cfg
        self._enabled = False
        self._lock = threading.Lock()

    def is_enabled(self) -> bool:
        with self._lock:
            return self._enabled

    def set_enabled(self, on: bool) -> None:
        with self._lock:
            self._enabled = bool(on)
            if not self._enabled:
                self._safe_stop_motors()

    def _safe_stop_motors(self) -> None:
        if self._driver is None:
            return
        try:
            self._driver.stop()
        except Exception as e:
            self.log.debug("循迹停轮: %s", e)

    def process_frame(self, frame_bgr: np.ndarray) -> np.ndarray:
        with self._lock:
            en = self._enabled
        if not en:
            return frame_bgr
        try:
            out, left_sp, right_sp, status, ok = overlay_and_speeds(frame_bgr, self.cfg)
        except Exception as e:
            self.log.warning("循迹帧处理失败: %s", e)
            return frame_bgr
        if self._driver is not None:
            try:
                if ok:
                    self._driver.set_left_motor(left_sp)
                    self._driver.set_right_motor(right_sp)
                else:
                    self._driver.stop()
            except Exception as e:
                self.log.warning("循迹写电机失败: %s", e)
        return out
