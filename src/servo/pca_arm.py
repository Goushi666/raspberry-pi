"""
PCA9685 机械臂：MQTT joint 0–5 映射到配置的 PCA 通道（默认 0–5）。

与云台共用同一块 PCA9685 时，driver 须为 smbus（本模块当前仅实现 SMBus 路径）。
脉宽 / PWM 频率可从 servo.arm 读取，缺省继承 servo.gimbal。启动归位为一次写角到位；MQTT 仍为插值慢转。
退出时随 servo.sleep_on_exit（默认 true）对 PCA9685 写睡眠停 PWM；下次进程 init_chip 会重新唤醒。
"""

from __future__ import annotations

import logging
import math
import queue
import threading
import time
from typing import Any, Dict, List, Optional, Tuple


def _smbus_module():
    try:
        import smbus2 as smbus  # type: ignore
        return smbus
    except ImportError:
        import smbus  # type: ignore
        return smbus


class PCA9685Arm:
    MODE1 = 0x00
    PRESCALE = 0xFE
    LED0_ON_L = 0x06
    _MODE1_SLEEP = 0x10
    _MODE1_RESTART = 0x80
    _LED_OFF_H_FULL_OFF = 0x10

    def __init__(self, log: logging.Logger, servo_cfg: Dict[str, Any]):
        self.log = log
        sc = servo_cfg or {}
        raw_drv = str(sc.get("driver", "smbus")).lower().strip()
        if raw_drv in ("smbus", "smbus2", "i2c"):
            self._driver = "smbus"
        else:
            self.log.warning("机械臂当前仅支持 servo.driver=smbus，收到 %r，将仍尝试 smbus", raw_drv)
            self._driver = "smbus"

        g = sc.get("gimbal") or {}
        self._cfg = sc.get("arm") or {}
        ch_raw = self._cfg.get("joint_channels")
        if ch_raw is not None:
            self._joint_channels: List[int] = [int(x) for x in list(ch_raw)]
        else:
            self._joint_channels = list(range(6))
        if len(self._joint_channels) != 6:
            raise ValueError("servo.arm.joint_channels 须为长度 6 的通道号列表")

        pose = self._cfg.get("init_angles")
        if pose is None:
            self._init_pose = [90.0] * 6
        else:
            pl = [float(x) for x in list(pose)]
            if len(pl) != 6:
                raise ValueError("servo.arm.init_angles 须为长度 6 的角度列表")
            self._init_pose = [max(0.0, min(180.0, x)) for x in pl]

        self._min_move_sec = float(
            self._cfg.get("mqtt_move_min_duration_sec", g.get("mqtt_move_min_duration_sec", 0.4))
        )
        self._max_move_sec = float(
            self._cfg.get("mqtt_move_max_duration_sec", g.get("mqtt_move_max_duration_sec", 6.0))
        )
        self._mqtt_move_duration_scale = float(
            self._cfg.get("mqtt_move_duration_scale", g.get("mqtt_move_duration_scale", 2.0))
        )
        self._mqtt_speed_floor = int(self._cfg.get("mqtt_speed_floor", g.get("mqtt_speed_floor", 12)))

        addr_raw = sc.get("pca9685_address", 0x40)
        self._address = int(addr_raw, 0) if isinstance(addr_raw, str) else int(addr_raw)
        self._i2c_bus_num = int(sc.get("i2c_bus", 1))
        self._sleep_on_exit = bool(sc.get("sleep_on_exit", True))
        self._pwm_freq_hz = float(self._cfg.get("pwm_freq_hz", g.get("pwm_freq_hz", 50)))
        self._pulse_lo = int(self._cfg.get("servo_pulse_min_us", g.get("servo_pulse_min_us", 500)))
        self._pulse_hi = int(self._cfg.get("servo_pulse_max_us", g.get("servo_pulse_max_us", 2500)))

        self._smbus_bus: Any = None
        self._angles: Dict[int, float] = {
            self._joint_channels[j]: self._init_pose[j] for j in range(6)
        }
        self._q: queue.Queue[Tuple[int, float, int]] = queue.Queue()
        self._worker: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._smbus_last_wake_check: float = 0.0

    def _is_ready(self) -> bool:
        return self._driver == "smbus" and self._smbus_bus is not None

    def joint_to_channel(self, joint: int) -> Optional[int]:
        if 0 <= joint < 6:
            return self._joint_channels[joint]
        return None

    @staticmethod
    def _effective_speed(speed_pct: int, floor: int) -> int:
        if speed_pct <= 0:
            return floor
        return max(floor, min(100, int(speed_pct)))

    def _duration_for_move(self, delta_deg: float, speed_pct: int) -> float:
        sp = self._effective_speed(speed_pct, self._mqtt_speed_floor)
        base = self._mqtt_move_duration_scale * (100.0 / float(sp))
        t = base * (abs(delta_deg) / 90.0)
        return max(self._min_move_sec, min(self._max_move_sec, t))

    def _angle_to_pulse_us(self, angle_deg: float) -> int:
        a = max(0.0, min(180.0, float(angle_deg)))
        span = float(self._pulse_hi - self._pulse_lo)
        return int(round(self._pulse_lo + (a / 180.0) * span))

    def _pulse_us_to_off_ticks(self, pulse_us: int) -> int:
        period_us = int(round(1_000_000.0 / self._pwm_freq_hz))
        t = int(round(pulse_us * 4096.0 / float(period_us)))
        return max(0, min(4095, t))

    def _smbus_release_bus(self) -> None:
        if self._smbus_bus is None:
            return
        try:
            self._smbus_bus.close()
        except Exception:
            pass
        self._smbus_bus = None

    def _smbus_ensure_awake(self) -> None:
        if self._smbus_bus is None:
            return
        now = time.monotonic()
        if now - self._smbus_last_wake_check < 0.5:
            return
        self._smbus_last_wake_check = now
        try:
            m = self._smbus_bus.read_byte_data(self._address, self.MODE1)
            if not (m & self._MODE1_SLEEP):
                return
            self.log.info("PCA9685(机械臂) 处于睡眠，正在唤醒（MODE1=0x%02x）", m)
            w = (m & 0x7F) & ~self._MODE1_SLEEP
            self._smbus_bus.write_byte_data(self._address, self.MODE1, w)
            time.sleep(0.002)
            m2 = self._smbus_bus.read_byte_data(self._address, self.MODE1)
            self._smbus_bus.write_byte_data(
                self._address, self.MODE1, (m2 & 0x7F) | self._MODE1_RESTART
            )
        except OSError as e:
            self.log.warning("PCA9685(机械臂) 唤醒失败: %s", e)
            self._smbus_release_bus()

    def _smbus_init_chip(self) -> None:
        bus = self._smbus_bus
        addr = self._address
        freq = self._pwm_freq_hz
        prescale = int(math.floor(25_000_000.0 / 4096.0 / freq + 0.5)) - 1
        prescale &= 0xFF
        mode0 = bus.read_byte_data(addr, self.MODE1)
        bus.write_byte_data(addr, self.MODE1, (mode0 & 0x7F) | self._MODE1_SLEEP)
        bus.write_byte_data(addr, self.PRESCALE, prescale)
        wake = (mode0 & 0x7F) & ~self._MODE1_SLEEP
        bus.write_byte_data(addr, self.MODE1, wake)
        time.sleep(0.005)
        bus.write_byte_data(addr, self.MODE1, wake | self._MODE1_RESTART)

    def _open_smbus(self) -> bool:
        try:
            smbus = _smbus_module()
            self._smbus_bus = smbus.SMBus(self._i2c_bus_num)
            self._smbus_init_chip()
            self.log.info(
                "PCA9685 机械臂已打开 /dev/i2c-%d 0x%02x 关节通道=%s %.0fHz %d–%dµs",
                self._i2c_bus_num,
                self._address,
                self._joint_channels,
                self._pwm_freq_hz,
                self._pulse_lo,
                self._pulse_hi,
            )
            return True
        except Exception as e:
            self.log.warning(
                "PCA9685 机械臂 SMBus 打开失败: %s",
                e,
                exc_info=self.log.isEnabledFor(logging.DEBUG),
            )
            self._smbus_release_bus()
            return False

    def _ensure_hardware(self) -> bool:
        if self._is_ready():
            return True
        return self._open_smbus()

    def _smbus_set_pwm(self, channel: int, on_ticks: int, off_ticks: int) -> None:
        base = self.LED0_ON_L + 4 * channel
        data = [
            on_ticks & 0xFF,
            (on_ticks >> 8) & 0x0F,
            off_ticks & 0xFF,
            (off_ticks >> 8) & 0x0F,
        ]
        for attempt in range(2):
            if self._smbus_bus is None:
                if not self._open_smbus():
                    return
            self._smbus_ensure_awake()
            if self._smbus_bus is None:
                if attempt == 0:
                    continue
                return
            try:
                self._smbus_bus.write_i2c_block_data(self._address, base, data)
                return
            except OSError as e:
                self.log.warning(
                    "PCA9685(机械臂) I2C 写 ch%d 失败 (%s)，将重开总线",
                    channel,
                    e,
                )
                self._smbus_release_bus()
                if attempt == 0:
                    continue
                raise

    def _write_angle(self, channel: int, angle: float) -> None:
        if not self._is_ready():
            return
        a = max(0.0, min(180.0, float(angle)))
        pus = self._angle_to_pulse_us(a)
        off = self._pulse_us_to_off_ticks(pus)
        self._smbus_set_pwm(channel, 0, off)
        self._angles[channel] = a

    def _ramp(self, channel: int, target: float, duration_sec: float) -> None:
        start = self._angles.get(channel, 90.0)
        delta = target - start
        if abs(delta) < 0.25:
            self._write_angle(channel, target)
            return
        steps = max(16, min(80, int(abs(delta) * 2)))
        step_time = duration_sec / float(steps)
        step_time = max(0.012, step_time)
        for i in range(1, steps + 1):
            if self._stop.is_set():
                return
            ang = start + delta * (i / float(steps))
            self._write_angle(channel, ang)
            time.sleep(step_time)

    def initialize_startup(self) -> None:
        if not self._ensure_hardware():
            self.log.error(
                "机械臂未初始化：PCA9685 未打开（/dev/i2c-%d、0x%02x）",
                self._i2c_bus_num,
                self._address,
            )
            return
        self.log.info("机械臂一次到位：joint0–5 → %s", self._init_pose)
        with self._lock:
            for j, ch in enumerate(self._joint_channels):
                self._write_angle(ch, float(self._init_pose[j]))
        self.log.info("机械臂已到达初始角度")

    def _worker_loop(self) -> None:
        while not self._stop.is_set():
            try:
                channel, target, speed = self._q.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                with self._lock:
                    if not self._ensure_hardware():
                        continue
                    cur = self._angles.get(channel, 90.0)
                    dur = self._duration_for_move(target - cur, speed)
                    self._ramp(channel, float(target), dur)
            except Exception as e:
                self.log.warning("机械臂运动异常 ch=%s: %s", channel, e)
            finally:
                self._q.task_done()

    def start_worker(self) -> None:
        if self._worker is not None:
            return
        self._stop.clear()
        self._worker = threading.Thread(target=self._worker_loop, name="arm-worker", daemon=True)
        self._worker.start()

    def submit_move(self, joint: int, angle: int, speed: int) -> bool:
        ch = self.joint_to_channel(joint)
        if ch is None:
            return False
        self._q.put((ch, float(angle), int(speed)))
        return True

    def _smbus_write_channel_full_off(self, channel: int) -> None:
        if self._smbus_bus is None:
            return
        base = self.LED0_ON_L + 4 * channel
        data = [0, 0, 0, self._LED_OFF_H_FULL_OFF]
        self._smbus_bus.write_i2c_block_data(self._address, base, data)

    def _smbus_sleep_on_ephemeral_bus(self, channels: Tuple[int, ...]) -> None:
        try:
            smbus = _smbus_module()
            bus = smbus.SMBus(self._i2c_bus_num)
            try:
                for ch in channels:
                    try:
                        base = self.LED0_ON_L + 4 * ch
                        bus.write_i2c_block_data(
                            self._address, base, [0, 0, 0, self._LED_OFF_H_FULL_OFF]
                        )
                    except OSError:
                        pass
                m = bus.read_byte_data(self._address, self.MODE1)
                bus.write_byte_data(self._address, self.MODE1, m | self._MODE1_SLEEP)
                self.log.info("PCA9685(机械臂) 已通过独立 I2C 完成关断与睡眠")
            finally:
                bus.close()
        except OSError as e:
            self.log.warning("PCA9685(机械臂) 独立 I2C 睡眠失败: %s", e)

    def _chip_sleep_stop_pwm(self) -> None:
        """须在持有 self._lock 时调用。"""
        if self._smbus_bus is None:
            return
        try:
            for ch in self._joint_channels:
                try:
                    self._smbus_write_channel_full_off(ch)
                except OSError:
                    pass
            m = self._smbus_bus.read_byte_data(self._address, self.MODE1)
            self._smbus_bus.write_byte_data(
                self._address, self.MODE1, m | self._MODE1_SLEEP
            )
            self.log.info("PCA9685(机械臂) 通道已关断并已睡眠（控制线无有效 PWM）")
        except OSError as e:
            self.log.warning("PCA9685(机械臂) 睡眠失败: %s", e)

    def close(self) -> None:
        self._stop.set()
        worker = self._worker
        if worker is not None:
            join_wait = max(35.0, float(self._max_move_sec) + 8.0)
            worker.join(timeout=join_wait)
            if worker.is_alive():
                self.log.warning(
                    "机械臂 worker 在 %.0fs 内仍未结束（Ctrl+C 时可能正在慢转），"
                    "将尝试不经锁停 PWM",
                    join_wait,
                )
            self._worker = None
        if self._sleep_on_exit:
            try:
                chans = tuple(self._joint_channels)
                if self._lock.acquire(timeout=3.0):
                    try:
                        self._chip_sleep_stop_pwm()
                    finally:
                        self._lock.release()
                else:
                    self.log.warning("机械臂：未拿到线程锁，改用独立 I2C 写 PCA9685 睡眠")
                    self._smbus_sleep_on_ephemeral_bus(chans)
            except Exception as e:
                self.log.warning("退出时停机械臂 PWM 异常: %s", e)
        self._smbus_release_bus()
