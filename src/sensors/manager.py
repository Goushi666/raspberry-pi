import threading
import time
from collections import deque
from typing import Any, Deque, Dict, Optional

class SensorManager:
    def __init__(self, dht22, light_sensor, interval: float = 5.0):
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
            try:
                temp_hum = self.dht22.read()
                light_data = self.light.read()

                data: dict[str, Any] = {"timestamp": int(time.time() * 1000)}

                if temp_hum and self.dht22.is_valid(temp_hum):
                    data["temperature"] = temp_hum["temperature"]
                    data["humidity"] = temp_hum["humidity"]
                else:
                    data["temperature"] = None
                    data["humidity"] = None

                if light_data:
                    data["light"] = light_data["light"]
                else:
                    data["light"] = None

                filtered = self._filter(data)
                with self._lock:
                    self.data_queue.append(filtered)
            except Exception as e:
                print(f"采集错误: {e}")

            time.sleep(self.interval)

    def _filter(self, data: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            if len(self.data_queue) < 3:
                return data

            recent = list(self.data_queue)[-3:]

        def avg_float(key: str) -> Optional[float]:
            vals = [d[key] for d in recent if d.get(key) is not None]
            if not vals:
                return data.get(key)
            return round(sum(vals) / len(vals), 1)

        out = {
            "timestamp": data["timestamp"],
            "temperature": avg_float("temperature"),
            "humidity": avg_float("humidity"),
            "light": avg_float("light"),
        }
        return out

    def get_latest(self) -> Optional[dict[str, Any]]:
        with self._lock:
            return self.data_queue[-1] if self.data_queue else None

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=self.interval + 2)
