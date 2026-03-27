import time


class DHT22Sensor:
    def __init__(self, pin: int):
        self.pin = pin

    def read(self):
        try:
            import Adafruit_DHT
        except ImportError as e:
            raise RuntimeError("Adafruit_DHT 未安装，请执行: pip install Adafruit-DHT") from e

        humidity, temperature = Adafruit_DHT.read_retry(Adafruit_DHT.DHT22, self.pin)

        if humidity is not None and temperature is not None:
            return {
                "temperature": round(float(temperature), 1),
                "humidity": round(float(humidity), 1),
                "timestamp": int(time.time() * 1000),
            }
        return None

    def is_valid(self, data):
        if data is None:
            return False
        return (
            -40 <= data["temperature"] <= 80
            and 0 <= data["humidity"] <= 100
        )
