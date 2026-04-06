import threading
import time
from collections import deque
from typing import Any, Deque, Dict, Optional

class SensorManager:
    def __init__(self, dht22, light_sensor=None, interval: float = 5.0):
        self.dht22 = dht22
        self.light = light_sensor
        self.interval = interval
        self.running = False
        self.data_queue: Deque[Dict[str, Any]] = deque(maxlen=100)
        self.thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    def start(self):
        self.running = True
        self.thread = threading.Thread(target=self._collect_loop, daemon=True)
        self.thread.start()

    def _collect_loop(self):
        while self.running:
            data: dict[str, Any] = {"timestamp": int(time.time() * 1000)}

            try:
                temp_hum = self.dht22.read()
            except Exception as e:
                print(f"采集错误 [DHT]: {e}")
                temp_hum = None

            if temp_hum and self.dht22.is_valid(temp_hum):
                data["temperature"] = temp_hum["temperature"]
                data["humidity"] = temp_hum["humidity"]
            else:
                data["temperature"] = None
                data["humidity"] = None

            if self.light is not None:
                try:
                    light_data = self.light.read()
                    if light_data is not None and "light" in light_data:
                        data["light"] = light_data["light"]
                    else:
                        data["light"] = None
                except Exception as e:
                    # BH1750Sensor.read() 已尽量返回 None；其它驱动仍可能抛错
                    print(f"采集错误 [BH1750/I2C]: {e}")
                    data["light"] = None

            try:
                filtered = self._filter(data)
                with self._lock:
                    self.data_queue.append(filtered)
            except Exception as e:
                print(f"采集错误 [滤波/队列]: {e}")

            time.sleep(self.interval)

    def _filter(self, data: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            prev = list(self.data_queue)

        # 用「队列里最近 2 条 + 当前当次」最多 3 点做滑动平均。
        # 旧实现只用队列里已有 3 条、不含当次读数，新读数会被旧均值拉回去，表现为跳变后回退一次。
        window = prev[-2:] + [dict(data)]

        def avg_float(key: str) -> Optional[float]:
            vals = [d[key] for d in window if d.get(key) is not None]
            if not vals:
                return data.get(key)
            return round(sum(vals) / len(vals), 1)

        out: dict[str, Any] = {
            "timestamp": data["timestamp"],
            "temperature": avg_float("temperature"),
            "humidity": avg_float("humidity"),
        }
        if self.light is not None and "light" in data:
            out["light"] = avg_float("light")
        return out

    def get_latest(self) -> Optional[dict[str, Any]]:
        with self._lock:
            return self.data_queue[-1] if self.data_queue else None

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=self.interval + 2)
        close = getattr(self.dht22, "close", None)
        if callable(close):
            close()
        lc = getattr(self.light, "close", None)
        if callable(lc):
            lc()
