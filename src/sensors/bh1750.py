import os
import random
import sys
import time
from typing import Tuple

try:
    import smbus2
    from smbus2 import i2c_msg
except ImportError:
    smbus2 = None
    i2c_msg = None

# 与《BH1750光照传感器使用说明.md》一致：write_byte 发命令、read_i2c_block_data(0x00,2) 读值、初始化后等待 0.18s。


class BH1750Sensor:
    """
    BH1750 驱动，对齐仓库内《BH1750光照传感器使用说明.md》示例代码：
    SMBus(1)、0x01 上电、0x10 连续高分辨率、两次写后 sleep(0.18)、
    read_i2c_block_data(ADDR, 0x00, 2) 再按 (hi<<8|lo)/1.2 得 lux。
    树莓派上少数环境下 read_i2c_block_data 会 EIO，则自动改用 i2c_rdwr 读 2 字节。
    """

    CMD_POWER_ON = 0x01
    CMD_CONT_H_RES = 0x10
    CMD_ONE_SHOT_H_RES = 0x20
    CMD_POWER_DOWN = 0x00
    INIT_WAIT_SEC = 0.18  # 文档 init_sensor 中的等待

    def __init__(self, bus: int = 1, address: int = 0x23):
        if smbus2 is None or i2c_msg is None:
            raise RuntimeError("BH1750 需安装 smbus2：pip install smbus2（与说明文档一致）")
        self._bus_num = int(bus)
        self._cfg_addr = int(address)
        dev = f"/dev/i2c-{self._bus_num}"
        if os.path.exists(dev) and not os.access(dev, os.R_OK | os.W_OK):
            print(
                f"BH1750: 当前用户对 {dev} 无读写权限。\n"
                f"  请执行: sudo usermod -aG i2c $USER 然后重新登录；"
                f"或临时: sudo python3 src/main.py",
                file=sys.stderr,
            )
        self.bus = smbus2.SMBus(self._bus_num, force=True)
        self._chip_started = False
        self._prefer_block_read = True
        self._addr, self._chip_responded = self._probe_address()

    def _wr_byte(self, cmd: int) -> None:
        self.bus.write_byte(self._addr, cmd)

    def _try_power_on(self, addr: int) -> bool:
        try:
            self.bus.write_byte(addr, self.CMD_POWER_ON)
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
                self._addr = addr
                return addr, True
        print(
            f"BH1750: 在 i2c-{self._bus_num} 上对 0x23/0x5C 均无应答（与 [Errno 5] 相同：总线上无此从机）。\n"
            f"  请先执行: sudo i2cdetect -y {self._bus_num} — 正常应出现格子 23 或 5C。\n"
            f"  说明文档接线: VCC→Pin2(5V), GND→Pin6, SCL→Pin5, SDA→Pin3；勿接反 SDA/SCL。\n"
            f"  已安装 i2c-tools、加入 i2c 组仍 EIO 时检查杜邦线与模块。",
            file=sys.stderr,
        )
        self._addr = self._cfg_addr
        return self._cfg_addr, False

    def _open_bus(self) -> None:
        self.bus = smbus2.SMBus(self._bus_num, force=True)

    def _read_raw(self) -> Tuple[int, int]:
        if self._prefer_block_read:
            try:
                block = self.bus.read_i2c_block_data(self._addr, 0x00, 2)
                return int(block[0]), int(block[1])
            except OSError:
                self._prefer_block_read = False
        rd = i2c_msg.read(self._addr, 2)
        self.bus.i2c_rdwr(rd)
        raw = bytes(rd)
        return raw[0], raw[1]

    def _reopen_bus(self) -> None:
        try:
            try:
                self.bus.write_byte(self._addr, self.CMD_POWER_DOWN)
            except OSError:
                pass
            self.bus.close()
        except Exception:
            pass
        time.sleep(0.03)
        self._open_bus()
        self._chip_started = False
        self._prefer_block_read = True

    def _ensure_continuous(self) -> None:
        if self._chip_started:
            return
        # 与说明文档 init_sensor() 相同：连续两次 write_byte，再一次 sleep(0.18)
        self._wr_byte(self.CMD_POWER_ON)
        self._wr_byte(self.CMD_CONT_H_RES)
        time.sleep(self.INIT_WAIT_SEC)
        self._chip_started = True

    def _read_continuous(self) -> dict:
        self._ensure_continuous()
        hi, lo = self._read_raw()
        light = (hi << 8 | lo) / 1.2
        return {
            "light": round(light, 1),
            "timestamp": int(time.time() * 1000),
        }

    def _read_oneshot(self) -> dict:
        self._chip_started = False
        self._wr_byte(self.CMD_POWER_ON)
        self._wr_byte(self.CMD_ONE_SHOT_H_RES)
        time.sleep(0.18)
        hi, lo = self._read_raw()
        light = (hi << 8 | lo) / 1.2
        return {
            "light": round(light, 1),
            "timestamp": int(time.time() * 1000),
        }

    def read(self):
        """成功返回 dict；I2C 失败返回 None（不抛异常，避免采集线程每 interval 刷屏）。"""
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
                        "BH1750: 仍无 I2C 应答，本轮不上报光照。接好线后下一采样周期会自动重试。",
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
                print(
                    f"BH1750: I2C 读失败（{e}）。芯片已在总线上时请查线长/电平；仍不行可试 sudo 排除规则冲突。",
                    file=sys.stderr,
                )
            return None

    def close(self) -> None:
        try:
            if self._chip_started:
                try:
                    self._wr_byte(self.CMD_POWER_DOWN)
                except OSError:
                    pass
        except Exception:
            pass
        self._chip_started = False
        try:
            self.bus.close()
        except Exception:
            pass


class MockBH1750:
    """光照传感器未接线时模拟 lux，围绕室内典型值小幅波动。"""

    def __init__(self, base_lux: float = 320.0):
        self.base_lux = base_lux

    def read(self):
        return {
            "light": round(self.base_lux + random.uniform(-40, 40), 1),
            "timestamp": int(time.time() * 1000),
        }
