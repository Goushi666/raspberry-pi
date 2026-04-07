import json
import threading
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

import paho.mqtt.client as mqtt

# 控制类回调：(topic, payload)
ControlMessageCallback = Callable[[str, Dict[str, Any]], None]


def _control_topic_list(raw: Union[str, List[str], None]) -> List[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        s = raw.strip()
        return [s] if s else []
    out: List[str] = []
    for item in raw:
        if item is None:
            continue
        s = str(item).strip()
        if s:
            out.append(s)
    return out


class MQTTClient:
    def __init__(
        self,
        host: str,
        port: int = 1883,
        client_id: str = "ailang",
        username: Optional[str] = None,
        password: Optional[str] = None,
        keepalive: int = 60,
        topics: Optional[Dict[str, Any]] = None,
        control_subscribe_qos: int = 1,
    ):
        self.host = host.strip()
        if self.host.startswith("mqtt://"):
            self.host = self.host.replace("mqtt://", "", 1).strip()
        self.port = int(port)
        self.keepalive = keepalive
        self.topics = topics or {}
        self._callbacks: Dict[str, ControlMessageCallback] = {}
        self._control_topics: List[str] = _control_topic_list(self.topics.get("vehicle_control"))
        self._control_subscribe_qos = int(control_subscribe_qos)
        self._connected = threading.Event()
        self._lock = threading.Lock()
        self.connected = False

        # 与《硬件端-Software通讯与对接说明》§3.1：MQTT 3.1.1
        self._client = mqtt.Client(client_id=client_id, protocol=mqtt.MQTTv311)
        if username:
            self._client.username_pw_set(username, password or "")

        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            with self._lock:
                self.connected = True
            self._connected.set()
            print("MQTT 已连接")
            if self._control_topics:
                subs: List[Tuple[str, int]] = [
                    (t, self._control_subscribe_qos) for t in self._control_topics
                ]
                client.subscribe(subs)
        else:
            print(f"MQTT 连接失败，返回码: {rc}")

    def _on_disconnect(self, client, userdata, rc):
        with self._lock:
            self.connected = False
        self._connected.clear()
        print("MQTT 已断开")

    def _on_message(self, client, userdata, msg):
        try:
            data = json.loads(msg.payload.decode("utf-8"))
            topic = msg.topic
            cb = self._callbacks.get(topic)
            if cb:
                cb(topic, data)
        except Exception as e:
            print(f"消息处理错误: {e}")

    def register_callback(self, topic: str, callback: ControlMessageCallback) -> None:
        self._callbacks[topic] = callback

    def register_control_callback(
        self, topics: Union[str, List[str], None], callback: ControlMessageCallback
    ) -> None:
        for t in _control_topic_list(topics):
            self._callbacks[t] = callback

    def subscribe(self, topic: str, callback: ControlMessageCallback) -> None:
        self._callbacks[topic] = callback
        self._client.subscribe(topic)

    def connect(self, wait_timeout: float = 20.0) -> None:
        self._connected.clear()
        self._client.connect(self.host, self.port, self.keepalive)
        self._client.loop_start()
        if not self._connected.wait(timeout=wait_timeout):
            raise TimeoutError(f"MQTT 在 {wait_timeout}s 内未连上 {self.host}:{self.port}")

    def publish(self, topic: str, data: Any, qos: int = 0) -> None:
        payload = json.dumps(data, ensure_ascii=False)
        self._client.publish(topic, payload, qos=qos)

    def disconnect(self) -> None:
        self._client.loop_stop()
        self._client.disconnect()
        self._connected.clear()
        with self._lock:
            self.connected = False
