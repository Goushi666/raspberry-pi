"""后台线程从 USB 摄像头取帧并编码为 JPEG，供 MJPEG HTTP 流复用。"""

import logging
import struct
import threading
import time
from typing import Optional

try:
    import cv2
except ImportError:
    cv2 = None

log = logging.getLogger(__name__)


def _fourcc_to_str(code: float) -> str:
    try:
        c = int(code) & 0xFFFFFFFF
        return struct.pack("<I", c).decode("ascii", errors="replace")
    except Exception:
        return "?"


def _try_mjpg(cap: "cv2.VideoCapture") -> None:
    """UVC 在 MJPG 下 720p 才能跑满 USB2 带宽；未设置时易退回 YUYV 导致极低帧率、画面糊。"""
    try:
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    except Exception:
        pass


class FrameSource:
    def __init__(
        self,
        device: int = 0,
        width: int = 640,
        height: int = 480,
        fps: float = 20.0,
        jpeg_quality: int = 88,
        prefer_mjpg: bool = True,
        buffer_size: int = 1,
        open_retry_sec: float = 2.0,
    ):
        if cv2 is None:
            raise RuntimeError("视频流需要 OpenCV：pip install opencv-python-headless")
        self.device = int(device)
        self.width = int(width)
        self.height = int(height)
        self.fps = max(1.0, float(fps))
        self.jpeg_quality = max(1, min(100, int(jpeg_quality)))
        self.prefer_mjpg = bool(prefer_mjpg)
        self.buffer_size = max(1, int(buffer_size))
        self.open_retry_sec = max(0.5, float(open_retry_sec))
        self._lock = threading.Lock()
        self._jpeg: Optional[bytes] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="frame-source")
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=3.0)
            self._thread = None

    def get_jpeg(self) -> Optional[bytes]:
        with self._lock:
            return self._jpeg

    def _open_capture(self) -> Optional["cv2.VideoCapture"]:
        cap: Optional[cv2.VideoCapture] = None
        try:
            if hasattr(cv2, "CAP_V4L2"):
                cap = cv2.VideoCapture(self.device, cv2.CAP_V4L2)
        except Exception:
            cap = None
        if cap is None or not cap.isOpened():
            if cap is not None:
                try:
                    cap.release()
                except Exception:
                    pass
            cap = cv2.VideoCapture(self.device)
        if not cap.isOpened():
            try:
                cap.release()
            except Exception:
                pass
            return None

        try:
            cap.set(cv2.CAP_PROP_BUFFERSIZE, self.buffer_size)
        except Exception:
            pass

        # 必须先设 MJPG，再设分辨率（顺序反了部分驱动不生效）
        if self.prefer_mjpg:
            _try_mjpg(cap)

        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        cap.set(cv2.CAP_PROP_FPS, min(60.0, self.fps))

        aw = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        ah = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        af = cap.get(cv2.CAP_PROP_FPS)
        fc = _fourcc_to_str(cap.get(cv2.CAP_PROP_FOURCC))
        log.info(
            "摄像头实际输出约 %dx%d @ %.1f fps，FOURCC=%s（MJPG 有利于 720p 流畅）",
            aw,
            ah,
            float(af) if af else 0.0,
            fc,
        )
        if self.prefer_mjpg and fc.strip().upper() not in ("MJPG", "MJPEG"):
            log.warning(
                "未处于 MJPG 模式，高分辨率下可能严重掉帧或模糊；可检查摄像头是否支持 MJPG，或降低 width/height。"
            )
        return cap

    def _loop(self) -> None:
        cap: Optional[cv2.VideoCapture] = None
        interval = 1.0 / self.fps
        # 关闭 JPEG 优化/渐进可缩短 imencode 时间，提高有效帧率（部分 OpenCV 仅支持 QUALITY）
        encode_params: list = [cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality]
        # 提高色度量化，减轻彩色边缘马赛克（OpenCV 4.x 部分版本支持）
        if hasattr(cv2, "IMWRITE_JPEG_CHROMA_QUALITY"):
            cq = min(100, self.jpeg_quality + 6)
            encode_params += [cv2.IMWRITE_JPEG_CHROMA_QUALITY, cq]
        if hasattr(cv2, "IMWRITE_JPEG_OPTIMIZE"):
            encode_params += [cv2.IMWRITE_JPEG_OPTIMIZE, 0]
        if hasattr(cv2, "IMWRITE_JPEG_PROGRESSIVE"):
            encode_params += [cv2.IMWRITE_JPEG_PROGRESSIVE, 0]

        while self._running:
            if cap is None or not cap.isOpened():
                if cap is not None:
                    try:
                        cap.release()
                    except Exception:
                        pass
                    cap = None
                cap = self._open_capture()
                if cap is None:
                    log.warning("无法打开摄像头 %s，%.1fs 后重试", self.device, self.open_retry_sec)
                    t0 = time.monotonic()
                    while self._running and time.monotonic() - t0 < self.open_retry_sec:
                        time.sleep(0.1)
                    continue

            t0 = time.monotonic()
            ok, frame = cap.read()
            if ok and frame is not None:
                _, buf = cv2.imencode(".jpg", frame, encode_params)
                blob = buf.tobytes()
                with self._lock:
                    self._jpeg = blob
            else:
                log.warning("读帧失败，将重新打开摄像头")
                try:
                    cap.release()
                except Exception:
                    pass
                cap = None
                time.sleep(0.2)
                continue

            dt = time.monotonic() - t0
            # 若本帧已超时，不额外睡满整格，避免越积越慢
            slack = interval - dt
            if slack > 0:
                time.sleep(slack)

        if cap is not None:
            try:
                cap.release()
            except Exception:
                pass
