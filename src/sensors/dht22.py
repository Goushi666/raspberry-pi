import contextlib
import os
import time
from typing import Any, Dict, Iterator, Optional


@contextlib.contextmanager
def _suppress_stderr() -> Iterator[None]:
    """pigpio 在连不上 pigpiod 时会向 stderr(C 层) 打大段说明，探测时先静音。"""
    devnull = os.open(os.devnull, os.O_WRONLY)
    saved = os.dup(2)
    try:
        os.dup2(devnull, 2)
        yield
    finally:
        os.dup2(saved, 2)
        os.close(saved)
        os.close(devnull)


class DHTSensor:
    """
    DHT11 / DHT22 单总线温湿度，接线相同（VCC、GND、DATA），仅型号不同。
    优先 pigpio（需 pigpiod）；否则 Adafruit CircuitPython + Blinka。
    """

    def __init__(self, pin: int, model: str = "dht22"):
        self.pin = int(pin)
        m = str(model).lower().strip()
        if m in ("dht22", "am2302"):
            self._model = "dht22"
        elif m in ("dht11",):
            self._model = "dht11"
        else:
            raise ValueError(f"不支持的 dht_model: {model!r}，请使用 dht11 或 dht22")
        self._backend: Optional[str] = None
        self._pi: Any = None
        self._dht: Any = None

    def _board_pin_for_bcm(self) -> Any:
        import board

        name = f"D{self.pin}"
        if not hasattr(board, name):
            raise ValueError(
                f"BCM GPIO{self.pin} 无对应 board.{name}，请在 pins.yaml 使用常见数字口（如 4、17、22）"
            )
        return getattr(board, name)

    def _try_pigpio(self) -> bool:
        try:
            import pigpio
        except ImportError:
            return False

        with _suppress_stderr():
            pi = pigpio.pi()
            if pi.connected:
                self._pi = pi
                self._backend = "pigpio"
                return True
            pi.stop()
        return False

    def _ensure_backend(self) -> str:
        if self._backend:
            return self._backend

        if self._try_pigpio():
            return "pigpio"

        try:
            import adafruit_dht
        except ImportError as e:
            raise RuntimeError(
                "DHT：请安装 pip install adafruit-blinka adafruit-circuitpython-dht\n"
                "并执行: sudo apt install -y libgpiod2"
            ) from e

        pin = self._board_pin_for_bcm()
        if self._model == "dht11":
            self._dht = adafruit_dht.DHT11(pin)
        else:
            self._dht = adafruit_dht.DHT22(pin)
        self._backend = "adafruit"
        time.sleep(2.2)
        return self._backend

    def _read_once(self) -> Optional[Dict[str, Any]]:
        backend = self._ensure_backend()
        if backend == "pigpio" and self._pi is not None:
            if self._model == "dht11":
                st, humidity, temperature = self._pi.read_DHT11(self.pin)
            else:
                st, humidity, temperature = self._pi.read_DHT22(self.pin)
            if st == 0 and humidity is not None and temperature is not None:
                return {
                    "temperature": round(float(temperature), 1),
                    "humidity": round(float(humidity), 1),
                    "timestamp": int(time.time() * 1000),
                }
            return None

        if self._dht is not None:
            try:
                t = self._dht.temperature
                h = self._dht.humidity
                if t is not None and h is not None:
                    return {
                        "temperature": round(float(t), 1),
                        "humidity": round(float(h), 1),
                        "timestamp": int(time.time() * 1000),
                    }
            except RuntimeError:
                pass
        return None

    def read(self) -> Optional[Dict[str, Any]]:
        # DHT11/DHT22：两次有效采样至少间隔约 2s
        out = self._read_once()
        if out is not None:
            return out
        time.sleep(2.1)
        return self._read_once()

    def is_valid(self, data: Optional[Dict[str, Any]]) -> bool:
        if data is None:
            return False
        t, h = float(data["temperature"]), float(data["humidity"])
        if self._model == "dht11":
            if not (0 <= t <= 55 and 0 <= h <= 100):
                return False
        else:
            if not (-40 <= t <= 80 and 0 <= h <= 100):
                return False
        if abs(t) < 0.15 and h < 1.0:
            return False
        if abs(t) < 0.05 and abs(h) < 0.05:
            return False
        return True

    def close(self) -> None:
        if self._dht is not None:
            try:
                self._dht.deinit()
            except Exception:
                pass
            self._dht = None
        if self._pi is not None:
            with _suppress_stderr():
                try:
                    self._pi.stop()
                except Exception:
                    pass
            self._pi = None
        self._backend = None


class DHT22Sensor(DHTSensor):
    """兼容旧代码：等价于 DHTSensor(pin, \"dht22\")。"""

    def __init__(self, pin: int):
        super().__init__(pin, "dht22")
