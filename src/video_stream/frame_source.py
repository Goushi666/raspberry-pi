"""后台线程从摄像头取帧并编码为 JPEG，供 MJPEG HTTP 流复用。"""

import threading
import time
from typing import Optional

try:
    import cv2
except ImportError:
    cv2 = None


class FrameSource:
    def __init__(
        self,
        device: int = 0,
        width: int = 640,
        height: int = 480,
        fps: float = 12.0,
        jpeg_quality: int = 75,
    ):
        if cv2 is None:
            raise RuntimeError("视频流需要 OpenCV：pip install opencv-python-headless")
        self.device = int(device)
        self.width = int(width)
        self.height = int(height)
        self.fps = max(1.0, float(fps))
        self.jpeg_quality = max(1, min(100, int(jpeg_quality)))
        self._lock = threading.Lock()
        self._jpeg: Optional[bytes] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None

    def get_jpeg(self) -> Optional[bytes]:
        with self._lock:
            return self._jpeg

    def _loop(self) -> None:
        cap = cv2.VideoCapture(self.device)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        cap.set(cv2.CAP_PROP_FPS, min(30, self.fps))
        interval = 1.0 / self.fps
        encode_params = [cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality]

        while self._running:
            t0 = time.monotonic()
            ok, frame = cap.read()
            if ok:
                _, buf = cv2.imencode(".jpg", frame, encode_params)
                blob = buf.tobytes()
                with self._lock:
                    self._jpeg = blob
            dt = time.monotonic() - t0
            time.sleep(max(0.0, interval - dt))

        cap.release()
