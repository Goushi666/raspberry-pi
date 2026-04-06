import os
import signal
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

SRC = Path(__file__).resolve().parent
ROOT = SRC.parent
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from communication.mqtt_client import MQTTClient
from sensors.bh1750 import BH1750Sensor, MockBH1750
from sensors.dht22 import DHTSensor
from sensors.manager import SensorManager
from utils.logger import setup_logging


def _mqtt_topics_for_mode(config: Dict[str, Any], sensor_mqtt_only: bool) -> Dict[str, str]:
    raw = (config.get("mqtt") or {}).get("topics") or {}
    if not sensor_mqtt_only:
        return dict(raw)
    sensor_key = "sensor_data"
    topic = (raw.get(sensor_key) or "sensor/data") if isinstance(raw, dict) else "sensor/data"
    return {sensor_key: str(topic)}


class MainApp:
    def __init__(self, config_path: Optional[Path] = None, pins_path: Optional[Path] = None):
        self.log = setup_logging(log_dir=ROOT / "logs")
        cfg_file = config_path or (ROOT / "config" / "config.yaml")
        pin_file = pins_path or (ROOT / "config" / "pins.yaml")
        self.config = self._load_yaml(cfg_file)
        self.pins = self._load_yaml(pin_file)
        self._apply_env_overrides()

        feats = self.config.get("features") or {}
        self.sensor_mqtt_only = bool(feats.get("sensor_mqtt_only", True))
        self.motor_enabled = bool(feats.get("motor_enabled")) and not self.sensor_mqtt_only
        self.vision_enabled = bool(feats.get("vision_enabled")) and not self.sensor_mqtt_only

        self.sensor_manager: Optional[SensorManager] = None
        self.mqtt: Optional[MQTTClient] = None
        self.controller = None
        self.driver = None
        self.tracker = None
        self._mjpeg = None
        self._cleaned = False

        self._init_sensors()
        self._init_mqtt()
        self._init_video_stream_optional()
        self._init_motor_vision_optional()

    def _load_yaml(self, path: Path) -> Dict[str, Any]:
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def _apply_env_overrides(self) -> None:
        m = self.config.setdefault("mqtt", {})
        if os.environ.get("MQTT_PASSWORD"):
            m["password"] = os.environ["MQTT_PASSWORD"]
        if os.environ.get("MQTT_USERNAME"):
            m["username"] = os.environ["MQTT_USERNAME"]
        if os.environ.get("MQTT_HOST"):
            m["host"] = os.environ["MQTT_HOST"]
        if os.environ.get("MQTT_DEVICE_ID"):
            m["device_id"] = os.environ["MQTT_DEVICE_ID"]

    def _mqtt_sensor_payload(self, raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """按《硬件端-Software通讯与对接说明》§3.2 组 JSON；无温/湿/光有效值时不发布（后端会忽略）。"""
        out: Dict[str, Any] = {"timestamp": raw.get("timestamp")}
        m = self.config.get("mqtt") or {}
        did = (m.get("device_id") or "").strip()
        if did:
            out["deviceId"] = did
        for key in ("temperature", "humidity", "light"):
            val = raw.get(key)
            if val is not None:
                out[key] = val
        if not any(k in out for k in ("temperature", "humidity", "light")):
            return None
        return out

    def _init_sensors(self) -> None:
        pins = self.pins
        dht_pin = pins.get("dht_bcm_pin") or pins.get("dht22_bcm_pin") or pins.get("dht22", 4)
        sens = self.config.get("sensors") or {}
        dht_model = str(sens.get("dht_model", "dht22")).lower().strip()
        self.dht22 = DHTSensor(int(dht_pin), model=dht_model)

        light = None
        bh_cfg = sens.get("bh1750") or {}
        if bh_cfg.get("enabled"):
            addr_raw = bh_cfg.get("i2c_address", 0x23)
            addr = int(addr_raw, 0) if isinstance(addr_raw, str) else int(addr_raw)
            dvi = bh_cfg.get("dvi_bcm")
            if dvi is None:
                dvi = pins.get("bh1750_dvi_bcm_pin")
            dvi_int = int(dvi) if dvi is not None and str(dvi).strip() != "" else None
            light = BH1750Sensor(
                bus=int(bh_cfg.get("i2c_bus", 1)),
                address=addr,
                dvi_bcm=dvi_int,
            )
        elif not self.sensor_mqtt_only:
            light = MockBH1750()

        interval = float(self.config.get("sensors", {}).get("sample_interval_sec", 5))
        self.sensor_manager = SensorManager(self.dht22, light, interval=interval)

    def _init_video_stream_optional(self) -> None:
        vs = self.config.get("video_stream") or {}
        if not vs.get("enabled"):
            return
        try:
            from video_stream.mjpeg_server import MjpegStreamService
        except ImportError as e:
            self.log.warning("视频流依赖缺失，跳过: %s", e)
            return
        try:
            path = str(vs.get("path", "/video_feed"))
            self._mjpeg = MjpegStreamService(
                host=str(vs.get("bind", "0.0.0.0")),
                port=int(vs.get("port", 8080)),
                path=path,
                camera_index=int(vs.get("camera_index", 0)),
                width=int(vs.get("width", 640)),
                height=int(vs.get("height", 480)),
                fps=float(vs.get("fps", 12)),
                jpeg_quality=int(vs.get("jpeg_quality", 75)),
                prefer_mjpg=bool(vs.get("prefer_mjpg", True)),
                buffer_size=int(vs.get("buffer_size", 1)),
                open_retry_sec=float(vs.get("open_retry_sec", 2.0)),
            )
        except Exception as e:
            self.log.warning("视频流初始化失败: %s", e)
            self._mjpeg = None

    def _init_mqtt(self) -> None:
        m = self.config["mqtt"]
        topics = _mqtt_topics_for_mode(self.config, self.sensor_mqtt_only)
        self.mqtt = MQTTClient(
            host=str(m["host"]),
            port=int(m["port"]),
            client_id=str(m.get("client_id", "ailang")),
            username=m.get("username"),
            password=m.get("password"),
            keepalive=int(m.get("keepalive", 60)),
            topics=topics,
        )
        if not self.sensor_mqtt_only:
            ctrl = (m.get("topics") or {}).get("vehicle_control")
            if ctrl:
                self.mqtt.register_callback(str(ctrl), self.handle_control)

    def _init_motor_vision_optional(self) -> None:
        if self.sensor_mqtt_only or not self.motor_enabled:
            return
        from motor.controller import VehicleController
        from motor.driver import L298NDriver

        motor_pins = self.pins.get("motor", {})
        self.driver = L298NDriver(**motor_pins)
        self.controller = VehicleController(self.driver)

        if self.vision_enabled and self.controller:
            from vision.camera import Camera
            from vision.processor import ImageProcessor
            from vision.tracker import LineTracker

            self.camera = Camera()
            self.processor = ImageProcessor()
            self.tracker = LineTracker(self.camera, self.processor, self.controller)

    def handle_control(self, data: Dict[str, Any]) -> None:
        if not self.motor_enabled or self.controller is None:
            self.log.info("收到控制指令(电机未启用): %s", data)
            return

        action = data.get("action")
        speed = int(data.get("speed", 50))

        if action == "forward":
            self.controller.forward(speed)
        elif action == "backward":
            self.controller.backward(speed)
        elif action == "left":
            self.controller.turn_left(speed)
        elif action == "right":
            self.controller.turn_right(speed)
        elif action == "stop":
            self.controller.stop()
        elif action == "auto" and self.tracker:
            self.tracker.start()
        elif action == "manual" and self.tracker:
            self.tracker.stop()

    def run(self) -> None:
        assert self.sensor_manager and self.mqtt
        self.sensor_manager.start()
        self.mqtt.connect()
        topics = self.config.get("mqtt", {}).get("topics", {})
        topic = topics.get("sensor_data", "sensor/data")
        bh_on = bool((self.config.get("sensors") or {}).get("bh1750", {}).get("enabled"))
        if self.sensor_mqtt_only:
            if bh_on:
                self.log.info("传感器上报（温湿度+光照）→ %s（无电机/视觉/遥控订阅）", topic)
            else:
                self.log.info("仅温湿度：上报到主题 %s", topic)
        else:
            self.log.info("开始上报传感器到主题 %s", topic)

        if self._mjpeg:
            self._mjpeg.start()
            self.log.info("视频流已启动（硬件端对接说明 §5）；浏览器访问 /preview 或配置后端 VIDEO_MJPEG_URL")

        mq = self.config.get("mqtt") or {}
        pub_qos = int(mq.get("publish_qos", 0))
        try:
            while True:
                sensor_data = self.sensor_manager.get_latest()
                if sensor_data:
                    payload = self._mqtt_sensor_payload(sensor_data)
                    if payload is not None:
                        self.mqtt.publish(topic, payload, qos=pub_qos)
                        self.log.debug("已发布: %s", payload)
                if self.motor_enabled and self.controller:
                    self.controller.check_timeout()
                time.sleep(1)
        finally:
            self.cleanup()

    def cleanup(self) -> None:
        if self._cleaned:
            return
        self._cleaned = True
        self.log.info("正在退出…")
        if self.sensor_manager:
            self.sensor_manager.stop()
        if self.tracker:
            try:
                self.tracker.stop()
            except Exception:
                pass
        if self.controller:
            try:
                self.controller.stop()
            except Exception:
                pass
        if self.driver:
            try:
                self.driver.cleanup()
            except Exception:
                pass
        if self.mqtt:
            self.mqtt.disconnect()
        if self._mjpeg:
            try:
                self._mjpeg.stop()
            except Exception:
                pass
            self._mjpeg = None


def main() -> None:
    app = MainApp()

    def signal_handler(sig, frame):
        app.cleanup()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    app.run()


if __name__ == "__main__":
    main()
