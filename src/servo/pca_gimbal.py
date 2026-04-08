"""
PCA9685 摄像头云台：仅 pan/tilt（默认通道 6、7）。

driver（config.servo.driver）：
  - smbus（默认）：/dev/i2c-* + 寄存器写 PWM，不依赖 Blinka，与常见树莓派示例一致
  - pca9685：adafruit_pca9685 + adafruit_motor.servo
  - servokit：Adafruit ServoKit

启动：一次写入 pan/tilt 目标角（home_angle、tilt_home_angle），无慢速插值归位。

MQTT 遥控时仍为分段插值慢转。进程退出时默认将 PCA9685 置 MODE1 睡眠以停 PWM（servo.sleep_on_exit，默认 true）；下次启动时 init_chip / 写 PWM 前会唤醒，不影响初始化。

若 PCA9685 曾被置为睡眠，振荡器停、写通道路也无 PWM；SMBus 路径下写舵机前会周期性检测并唤醒；init_chip 会清除睡眠位。
"""

from __future__ import annotations

import logging
import math
import queue
import threading
import time
from typing import Any, Dict, Optional, Tuple


def _smbus_module():
    try:
        import smbus2 as smbus  # type: ignore
        return smbus
    except ImportError:
        import smbus  # type: ignore
        return smbus


class PCA9685Gimbal:
    MODE1 = 0x00
    PRESCALE = 0xFE
    LED0_ON_L = 0x06
    # MODE1：bit4=SLEEP（关振荡器无 PWM）；bit7=RESTART
    _MODE1_SLEEP = 0x10
    _MODE1_RESTART = 0x80
    # LEDn_OFF_H bit4：该通道完全关断输出（再配合 MODE1 睡眠）
    _LED_OFF_H_FULL_OFF = 0x10

    def __init__(self, log: logging.Logger, servo_cfg: Dict[str, Any]):
        self.log = log
        sc = servo_cfg or {}
        raw_drv = str(sc.get("driver", "smbus")).lower().strip()
        if raw_drv in ("servokit", "kit", "servo_kit"):
            self._driver = "servokit"
        elif raw_drv in ("pca9685", "pca", "direct", "adafruit"):
            self._driver = "pca9685"
        elif raw_drv in ("smbus", "smbus2", "i2c"):
            self._driver = "smbus"
        else:
            self.log.warning("servo.driver=%r 未识别，使用 smbus", raw_drv)
            self._driver = "smbus"

        self._cfg = sc.get("gimbal") or {}
        self._pan_ch = int(self._cfg.get("pan_channel", 6))
        self._tilt_ch = int(self._cfg.get("tilt_channel", 7))
        self._home = float(self._cfg.get("home_angle", 90))
        _th = self._cfg.get("tilt_home_angle")
        self._tilt_home = (
            max(0.0, min(180.0, float(_th)))
            if _th is not None
            else self._home
        )
        self._min_move_sec = float(self._cfg.get("mqtt_move_min_duration_sec", 0.4))
        self._max_move_sec = float(self._cfg.get("mqtt_move_max_duration_sec", 6.0))
        self._mqtt_move_duration_scale = float(
            self._cfg.get("mqtt_move_duration_scale", 2.0)
        )
        self._mqtt_speed_floor = int(self._cfg.get("mqtt_speed_floor", 12))

        addr_raw = sc.get("pca9685_address", 0x40)
        self._address = int(addr_raw, 0) if isinstance(addr_raw, str) else int(addr_raw)
        self._i2c_bus_num = int(sc.get("i2c_bus", 1))
        self._sleep_on_exit = bool(sc.get("sleep_on_exit", True))
        self._pwm_freq_hz = float(self._cfg.get("pwm_freq_hz", 50))
        self._pulse_lo = int(self._cfg.get("servo_pulse_min_us", 500))
        self._pulse_hi = int(self._cfg.get("servo_pulse_max_us", 2500))

        self._smbus_bus: Any = None
        self._kit: Any = None
        self._pca: Any = None
        self._servo_objs: Dict[int, Any] = {}
        self._angles: Dict[int, float] = {
            self._pan_ch: self._home,
            self._tilt_ch: self._tilt_home,
        }
        self._q: queue.Queue[Tuple[int, float, int]] = queue.Queue()
        self._worker: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._smbus_last_wake_check: float = 0.0

    def _is_ready(self) -> bool:
        if self._driver == "servokit":
            return self._kit is not None
        if self._driver == "smbus":
            return self._smbus_bus is not None
        return self._pca is not None and len(self._servo_objs) == 2

    def _angle_to_pulse_us(self, angle_deg: float) -> int:
        a = max(0.0, min(180.0, float(angle_deg)))
        span = float(self._pulse_hi - self._pulse_lo)
        return int(round(self._pulse_lo + (a / 180.0) * span))

    def _pulse_us_to_off_ticks(self, pulse_us: int) -> int:
        period_us = int(round(1_000_000.0 / self._pwm_freq_hz))
        t = int(round(pulse_us * 4096.0 / float(period_us)))
        return max(0, min(4095, t))

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
                    "PCA9685 I2C 写通道 %d 失败 (%s)，将重开 /dev/i2c-%d 并重试一次",
                    channel,
                    e,
                    self._i2c_bus_num,
                )
                self._smbus_release_bus()
                if attempt == 0:
                    continue
                raise

    def _smbus_release_bus(self) -> None:
        if self._smbus_bus is None:
            return
        try:
            self._smbus_bus.close()
        except Exception:
            pass
        self._smbus_bus = None

    def _smbus_ensure_awake(self) -> None:
        """若 MODE1 处于睡眠（如 pca9685_servo_test --sleep-on-exit），写通道路无效，须先唤醒。"""
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
            self.log.info("PCA9685 处于睡眠，正在唤醒（MODE1=0x%02x）", m)
            w = (m & 0x7F) & ~self._MODE1_SLEEP
            self._smbus_bus.write_byte_data(self._address, self.MODE1, w)
            time.sleep(0.002)
            m2 = self._smbus_bus.read_byte_data(self._address, self.MODE1)
            self._smbus_bus.write_byte_data(
                self._address, self.MODE1, (m2 & 0x7F) | self._MODE1_RESTART
            )
        except OSError as e:
            self.log.warning("PCA9685 唤醒失败: %s", e)
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
        # 勿原样写回 mode0：若此前为睡眠态，会一直保持睡眠、无 PWM 输出
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
                "PCA9685 云台已打开（SMBus 寄存器）/dev/i2c-%d address=0x%02x "
                "pan=ch%d tilt=ch%d %.0fHz 脉宽=%d–%dµs",
                self._i2c_bus_num,
                self._address,
                self._pan_ch,
                self._tilt_ch,
                self._pwm_freq_hz,
                self._pulse_lo,
                self._pulse_hi,
            )
            return True
        except Exception as e:
            self.log.warning("PCA9685 SMBus 打开失败: %s", e, exc_info=self.log.isEnabledFor(logging.DEBUG))
            self._smbus_release_bus()
            return False

    def _open_pca9685(self) -> bool:
        try:
            import board
            from adafruit_motor import servo as ada_servo
            from adafruit_pca9685 import PCA9685
        except ImportError as e:
            self.log.warning("云台依赖缺失（adafruit pca9685 / motor / blinka）: %s", e)
            return False
        try:
            i2c = board.I2C()
            self._pca = PCA9685(i2c, address=int(self._address))
            self._pca.frequency = int(self._pwm_freq_hz)
            self._servo_objs.clear()
            for ch in (self._pan_ch, self._tilt_ch):
                self._servo_objs[ch] = ada_servo.Servo(
                    self._pca.channels[ch],
                    actuation_range=180,
                    min_pulse=self._pulse_lo,
                    max_pulse=self._pulse_hi,
                )
            self.log.info(
                "PCA9685 云台已打开（adafruit_pca9685）0x%02x ch%d/ch%d",
                self._address,
                self._pan_ch,
                self._tilt_ch,
            )
            return True
        except Exception as e:
            self.log.warning("PCA9685 adafruit 驱动失败: %s", e, exc_info=self.log.isEnabledFor(logging.DEBUG))
            self._pca = None
            self._servo_objs.clear()
            return False

    def _open_servokit(self) -> bool:
        try:
            import board
            from adafruit_servokit import ServoKit
        except ImportError as e:
            self.log.warning("云台依赖缺失（servokit）: %s", e)
            return False
        try:
            i2c = board.I2C()
            self._kit = ServoKit(channels=16, i2c=i2c, address=int(self._address))
            for ch in (self._pan_ch, self._tilt_ch):
                try:
                    self._kit.servo[ch].set_pulse_width_range(self._pulse_lo, self._pulse_hi)
                except Exception:
                    break
            self.log.info("PCA9685 云台已打开（ServoKit）0x%02x", self._address)
            return True
        except Exception as e:
            self.log.warning("ServoKit 失败: %s", e, exc_info=self.log.isEnabledFor(logging.DEBUG))
            self._kit = None
            return False

    def _ensure_hardware(self) -> bool:
        if self._is_ready():
            return True
        if self._driver == "servokit":
            return self._open_servokit()
        if self._driver == "smbus":
            return self._open_smbus()
        return self._open_pca9685()

    def _home_for_channel(self, channel: int) -> float:
        return self._tilt_home if channel == self._tilt_ch else self._home

    def joint_to_channel(self, joint: int) -> Optional[int]:
        if joint == 6:
            return self._pan_ch
        if joint == 7:
            return self._tilt_ch
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

    def _write_angle(self, channel: int, angle: float) -> None:
        if not self._is_ready():
            return
        a = max(0.0, min(180.0, float(angle)))
        if self._driver == "smbus":
            pus = self._angle_to_pulse_us(a)
            off = self._pulse_us_to_off_ticks(pus)
            self._smbus_set_pwm(channel, 0, off)
        elif self._driver == "servokit":
            self._kit.servo[channel].angle = a
        else:
            obj = self._servo_objs.get(channel)
            if obj is None:
                return
            obj.angle = a
        self._angles[channel] = a

    def _ramp(self, channel: int, target: float, duration_sec: float) -> None:
        start = self._angles.get(channel, self._home_for_channel(channel))
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
                "云台未初始化：PCA9685 未打开（/dev/i2c-%d、0x%02x、driver=%s）",
                self._i2c_bus_num,
                self._address,
                self._driver,
            )
            return
        self.log.info(
            "云台一次到位：ch%d=%.0f° ch%d=%.0f°",
            self._pan_ch,
            self._home,
            self._tilt_ch,
            self._tilt_home,
        )
        with self._lock:
            self._write_angle(self._pan_ch, self._home)
            self._write_angle(self._tilt_ch, self._tilt_home)
        if abs(self._tilt_home - self._home) < 0.5:
            self.log.info("云台已到达 %.0f°", self._home)
        else:
            self.log.info(
                "云台已到达 ch%d=%.0f° ch%d=%.0f°",
                self._pan_ch,
                self._home,
                self._tilt_ch,
                self._tilt_home,
            )

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
                    cur = self._angles.get(channel, self._home_for_channel(channel))
                    dur = self._duration_for_move(target - cur, speed)
                    self._ramp(channel, float(target), dur)
            except Exception as e:
                self.log.warning("云台运动异常 ch=%s: %s", channel, e)
            finally:
                self._q.task_done()

    def start_worker(self) -> None:
        if self._worker is not None:
            return
        self._stop.clear()
        self._worker = threading.Thread(target=self._worker_loop, name="gimbal-worker", daemon=True)
        self._worker.start()

    def submit_move(self, joint: int, angle: int, speed: int) -> bool:
        ch = self.joint_to_channel(joint)
        if ch is None:
            return False
        self._q.put((ch, float(angle), int(speed)))
        return True

    def _smbus_write_channel_full_off(self, channel: int) -> None:
        """寄存器手册：LEDn_OFF_H 的 bit4=1 表示该路 PWM 完全关闭。"""
        if self._smbus_bus is None:
            return
        base = self.LED0_ON_L + 4 * channel
        data = [0, 0, 0, self._LED_OFF_H_FULL_OFF]
        self._smbus_bus.write_i2c_block_data(self._address, base, data)

    def _smbus_sleep_on_ephemeral_bus(self, channels: Tuple[int, ...]) -> None:
        """不经过 self._smbus_bus，用于 worker 仍占用锁或主总线不可用时的退出兜底。"""
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
                self.log.info("PCA9685(云台) 已通过独立 I2C 完成关断与睡眠")
            finally:
                bus.close()
        except OSError as e:
            self.log.warning("PCA9685(云台) 独立 I2C 睡眠失败: %s", e)

    def _chip_sleep_stop_pwm(self) -> None:
        """先关各通道输出再 MODE1 睡眠；调用方须已持有 self._lock（与 worker 互斥）。"""
        if self._driver == "smbus" and self._smbus_bus is not None:
            try:
                for ch in (self._pan_ch, self._tilt_ch):
                    try:
                        self._smbus_write_channel_full_off(ch)
                    except OSError:
                        pass
                m = self._smbus_bus.read_byte_data(self._address, self.MODE1)
                self._smbus_bus.write_byte_data(
                    self._address, self.MODE1, m | self._MODE1_SLEEP
                )
                self.log.info("PCA9685(云台) 通道已关断并已睡眠（控制线无有效 PWM）")
            except OSError as e:
                self.log.warning("PCA9685(云台) 睡眠失败: %s", e)
            return
        if self._driver == "servokit" and self._kit is not None:
            pca = None
            for attr in ("_pca", "pca", "_pwm", "pwm"):
                pca = getattr(self._kit, attr, None)
                if pca is not None and hasattr(pca, "sleep"):
                    break
                pca = None
            if pca is not None:
                try:
                    pca.sleep = True
                    self.log.info("PCA9685(云台) 已睡眠（ServoKit）")
                except Exception as e:
                    self.log.warning("PCA9685(云台) ServoKit 睡眠失败: %s", e)
            return
        if self._pca is not None:
            try:
                self._pca.sleep = True
                self.log.info("PCA9685(云台) 已睡眠（adafruit）")
            except Exception as e:
                self.log.warning("PCA9685(云台) adafruit 睡眠失败: %s", e)

    def close(self) -> None:
        self._stop.set()
        worker = self._worker
        if worker is not None:
            join_wait = max(35.0, float(self._max_move_sec) + 8.0)
            worker.join(timeout=join_wait)
            if worker.is_alive():
                self.log.warning(
                    "云台 worker 在 %.0fs 内仍未结束（Ctrl+C 时可能正在慢转），"
                    "将尝试不经锁停 PWM",
                    join_wait,
                )
            self._worker = None
        if self._sleep_on_exit:
            try:
                if self._driver == "smbus":
                    chans = (self._pan_ch, self._tilt_ch)
                    if self._lock.acquire(timeout=3.0):
                        try:
                            self._chip_sleep_stop_pwm()
                        finally:
                            self._lock.release()
                    else:
                        self.log.warning("云台：未拿到线程锁，改用独立 I2C 写 PCA9685 睡眠")
                        self._smbus_sleep_on_ephemeral_bus(chans)
                else:
                    with self._lock:
                        self._chip_sleep_stop_pwm()
            except Exception as e:
                self.log.warning("退出时停云台 PWM 异常: %s", e)
        self._kit = None
        if self._pca is not None:
            try:
                self._pca.deinit()
            except Exception:
                pass
        self._pca = None
        self._servo_objs.clear()
        self._smbus_release_bus()
