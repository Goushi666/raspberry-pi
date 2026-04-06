"""
BH1750 光照传感器：与 doc/BH1750_树莓派使用指南.md 中「方案一」C 程序一致——
打开 /dev/i2c-N，ioctl(I2C_SLAVE)，write 命令字节，read(2) 取数据，lux=(hi<<8|lo)/1.2。

说明文档要点：
- J1：I2C + 供电；J2：DVI 须为高电平（接 Pin1 3.3V），否则部分模块 i2cdetect 无设备。
- 可选 dvi_bcm：若将 DVI 接到某 GPIO，由程序拉高（优先 pigpio，其次 RPi.GPIO）。
- 文档 §4.2.2 中 read_i2c_block_data(ADDR, 0x10, 2) 与芯片读数方式不符；应以 plain read(2) 为准（与 C 一致）。
"""

import errno
import fcntl
import os
import random
import sys
import time
from typing import Optional, Tuple

I2C_SLAVE = 0x0703
I2C_TENBIT = 0x0704


class BH1750Sensor:
    CMD_POWER_ON = 0x01
    CMD_RESET = 0x07  # 数据寄存器软复位（须在上电后）
    CMD_CONT_H_RES = 0x10
    CMD_ONE_SHOT_H_RES = 0x20
    CMD_POWER_DOWN = 0x00

    # 文档 C 示例：连续模式测量约 120ms，循环内 usleep(180000)
    INIT_AFTER_MODE_SEC = 0.18
    READ_BEFORE_DELAY_SEC = 0.12  # 略小于 180ms，与 120ms 分辨率匹配；采样间隔较大时可覆盖

    def __init__(self, bus: int = 1, address: int = 0x23, dvi_bcm: Optional[int] = None):
        self._bus_num = int(bus)
        self._cfg_addr = int(address)
        self._dvi_bcm = int(dvi_bcm) if dvi_bcm is not None else None
        self._pigpio_for_dvi = None
        self._rpi_dvi_pin: Optional[int] = None

        self._dev_path = f"/dev/i2c-{self._bus_num}"
        if os.path.exists(self._dev_path) and not os.access(self._dev_path, os.R_OK | os.W_OK):
            print(
                f"BH1750: 当前用户对 {self._dev_path} 无读写权限。\n"
                f"  请执行: sudo usermod -aG i2c $USER 后重新登录；或 sudo python3 运行。",
                file=sys.stderr,
            )

        self._setup_dvi_high()

        try:
            self._fd = os.open(self._dev_path, os.O_RDWR)
        except OSError as e:
            self._teardown_dvi()
            raise RuntimeError(f"无法打开 {self._dev_path}: {e}") from e

        self._chip_started = False
        self._addr, self._chip_responded = self._probe_address()

    def _setup_dvi_high(self) -> None:
        """文档：J2 DVI 须拉高。已用杜邦线接 Pin1(3.3V) 时可不设 dvi_bcm。"""
        if self._dvi_bcm is None:
            return
        pin = self._dvi_bcm
        try:
            import pigpio

            pi = pigpio.pi()
            if pi.connected:
                pi.set_mode(pin, pigpio.OUTPUT)
                pi.write(pin, 1)
                self._pigpio_for_dvi = pi
                return
        except Exception:
            pass
        try:
            import RPi.GPIO as GPIO

            GPIO.setwarnings(False)
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(pin, GPIO.OUT)
            GPIO.output(pin, GPIO.HIGH)
            self._rpi_dvi_pin = pin
        except Exception as e:
            print(
                f"BH1750: dvi_bcm={pin} 拉高失败（{e}）；请改将 J2 DVI 接树莓派 Pin1(3.3V)。",
                file=sys.stderr,
            )

    def _teardown_dvi(self) -> None:
        self._pigpio_for_dvi = None
        self._rpi_dvi_pin = None

    def _select_slave(self, addr: Optional[int] = None) -> None:
        a = self._addr if addr is None else addr
        fcntl.ioctl(self._fd, I2C_TENBIT, 0)
        fcntl.ioctl(self._fd, I2C_SLAVE, a)

    def _wr_cmd(self, cmd: int, addr: Optional[int] = None) -> None:
        self._select_slave(addr)
        os.write(self._fd, bytes([cmd]))

    def _try_power_on(self, addr: int) -> bool:
        try:
            self._wr_cmd(self.CMD_POWER_ON, addr)
            return True
        except OSError:
            return False

    def _probe_address(self) -> Tuple[int, bool]:
        order = [self._cfg_addr] + [a for a in (0x23, 0x5C) if a != self._cfg_addr]
        for addr in order:
            if self._try_power_on(addr):
                if addr != self._cfg_addr:
                    print(
                        f"BH1750: 配置 0x{self._cfg_addr:02x} 无应答，已改用 0x{addr:02x}",
                        file=sys.stderr,
                    )
                return addr, True
        print(
            f"BH1750: i2c-{self._bus_num} 上 0x23/0x5C 均无应答。\n"
            f"  执行: sudo i2cdetect -y {self._bus_num} 应出现 23 或 5C。\n"
            f"  接线见 doc/BH1750_树莓派使用指南.md：J1 SDA/SCL/GND/VCC；部分模块 **J2 DVI 须接 3.3V(Pin1)**，"
            f"或在 config 设置 bh1750.dvi_bcm 用 GPIO 拉高。",
            file=sys.stderr,
        )
        try:
            self._select_slave(self._cfg_addr)
        except OSError:
            pass
        return self._cfg_addr, False

    def _open_bus(self) -> None:
        self._fd = os.open(self._dev_path, os.O_RDWR)
        self._select_slave()

    def _read_raw(self) -> Tuple[int, int]:
        self._select_slave()
        raw = os.read(self._fd, 2)
        if len(raw) != 2:
            raise OSError(errno.EIO, "BH1750 read returned fewer than 2 bytes")
        return raw[0], raw[1]

    def _reopen_bus(self) -> None:
        try:
            try:
                self._wr_cmd(self.CMD_POWER_DOWN)
            except OSError:
                pass
            os.close(self._fd)
        except Exception:
            pass
        time.sleep(0.03)
        self._open_bus()
        self._chip_started = False

    def _ensure_continuous(self) -> None:
        if self._chip_started:
            return
        self._wr_cmd(self.CMD_POWER_ON)
        try:
            self._wr_cmd(self.CMD_RESET)
            time.sleep(0.002)
        except OSError:
            pass
        self._wr_cmd(self.CMD_CONT_H_RES)
        time.sleep(self.INIT_AFTER_MODE_SEC)
        self._chip_started = True

    def _read_continuous(self) -> dict:
        self._ensure_continuous()
        time.sleep(self.READ_BEFORE_DELAY_SEC)
        hi, lo = self._read_raw()
        light = (hi << 8 | lo) / 1.2
        return {
            "light": round(light, 1),
            "timestamp": int(time.time() * 1000),
        }

    def _read_oneshot(self) -> dict:
        self._chip_started = False
        self._wr_cmd(self.CMD_POWER_ON)
        self._wr_cmd(self.CMD_ONE_SHOT_H_RES)
        time.sleep(self.INIT_AFTER_MODE_SEC)
        hi, lo = self._read_raw()
        light = (hi << 8 | lo) / 1.2
        return {
            "light": round(light, 1),
            "timestamp": int(time.time() * 1000),
        }

    def read(self):
        """成功返回 dict；I2C 失败返回 None。"""
        if not self._chip_responded:
            order = [self._addr] + [a for a in (0x23, 0x5C) if a != self._addr]
            for addr in order:
                if self._try_power_on(addr):
                    self._addr = addr
                    self._chip_responded = True
                    self._chip_started = False
                    break
            if not self._chip_responded:
                if not getattr(self, "_logged_skip_read", False):
                    self._logged_skip_read = True
                    print(
                        "BH1750: 仍无应答，不上报光照；接好 J1/J2(DVI) 后下一周期重试。",
                        file=sys.stderr,
                    )
                return None
        try:
            for _ in range(3):
                try:
                    return self._read_continuous()
                except OSError:
                    self._reopen_bus()
            return self._read_oneshot()
        except OSError as e:
            if not getattr(self, "_logged_read_err", False):
                self._logged_read_err = True
                print(f"BH1750: I2C 读失败（{e}）。", file=sys.stderr)
            return None

    def close(self) -> None:
        try:
            if self._chip_started:
                try:
                    self._wr_cmd(self.CMD_POWER_DOWN)
                except OSError:
                    pass
        except Exception:
            pass
        self._chip_started = False
        try:
            os.close(self._fd)
        except OSError:
            pass
        self._teardown_dvi()


class MockBH1750:
    """光照传感器未接线时模拟 lux。"""

    def __init__(self, base_lux: float = 320.0):
        self.base_lux = base_lux

    def read(self):
        return {
            "light": round(self.base_lux + random.uniform(-40, 40), 1),
            "timestamp": int(time.time() * 1000),
        }
