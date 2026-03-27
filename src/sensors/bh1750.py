import random
import time

try:
    import smbus2
except ImportError:
    smbus2 = None


class BH1750Sensor:
    ADDR = 0x23
    ONE_TIME_HIGH_RES = 0x20

    def __init__(self, bus=1):
        if smbus2 is None:
            raise RuntimeError("smbus2 未安装")
        self.bus = smbus2.SMBus(bus)

    def read(self):
        self.bus.write_byte(self.ADDR, self.ONE_TIME_HIGH_RES)
        time.sleep(0.2)

        data = self.bus.read_i2c_block_data(self.ADDR, 0x00, 2)
        light = (data[0] << 8 | data[1]) / 1.2

        return {
            "light": round(light, 1),
            "timestamp": int(time.time() * 1000),
        }


class MockBH1750:
    """光照传感器未接线时模拟 lux，围绕室内典型值小幅波动。"""

    def __init__(self, base_lux: float = 320.0):
        self.base_lux = base_lux

    def read(self):
        return {
            "light": round(self.base_lux + random.uniform(-40, 40), 1),
            "timestamp": int(time.time() * 1000),
        }
