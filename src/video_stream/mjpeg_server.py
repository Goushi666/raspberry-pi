"""
MJPEG over HTTP（multipart/x-mixed-replace），与《硬件端-Software通讯与对接说明》§5 一致。

- 主流：GET /video_feed（与常见 Flask 示例一致，config 可改 canonical path）
- 兼容：GET /mjpeg 与 config 中的 path 始终指向同一路流（三者任一即可）
- 配置 JSON：GET /api/video/stream-config
- 巡检预览：GET /preview
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, FrozenSet, Optional, Union
from urllib.parse import urlparse

from video_stream.frame_source import FrameSource

STREAM_CONFIG_PATH = "/api/video/stream-config"
PREVIEW_PATH = "/preview"
VIDEO_FEED_PATH = "/video_feed"
LEGACY_MJPEG_PATH = "/mjpeg"


def _normalize_path(path: str) -> str:
    p = path.strip() or "/video_feed"
    return p if p.startswith("/") else "/" + p


def _stream_paths(canonical: str) -> FrozenSet[str]:
    """同一 MJPEG 流可经多个 URL 访问，便于与文档 / 旧链接对齐。"""
    c = _normalize_path(canonical)
    return frozenset({c, VIDEO_FEED_PATH, LEGACY_MJPEG_PATH})


def _request_public_base(handler: BaseHTTPRequestHandler) -> str:
    host = handler.headers.get("Host")
    if not host:
        sa = handler.server.server_address
        h, p = sa[0], sa[1]
        if h == "0.0.0.0" or h == "::":
            h = "127.0.0.1"
        host = f"{h}:{p}"
    proto = handler.headers.get("X-Forwarded-Proto", "http").split(",")[0].strip()
    if proto not in ("http", "https"):
        proto = "http"
    return f"{proto}://{host}"


def _build_handler(
    canonical_stream_path: str,
    frame_getter: Callable[[], Optional[bytes]],
    boundary: bytes,
) -> type:
    canonical = _normalize_path(canonical_stream_path)
    stream_paths = _stream_paths(canonical)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args) -> None:
            pass

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if not path:
                path = "/"

            if path == STREAM_CONFIG_PATH:
                self._send_stream_config()
                return

            if path == PREVIEW_PATH:
                self._send_preview_page()
                return

            if path == "/" or path == "":
                self._send_index_html()
                return

            if path not in stream_paths:
                self.send_error(404, "Not Found")
                return

            self.send_response(200)
            self.send_header(
                "Content-Type",
                f"multipart/x-mixed-replace; boundary={boundary.decode()}",
            )
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.end_headers()

            sep = b"--" + boundary + b"\r\nContent-Type: image/jpeg\r\n\r\n"
            # 仅在有新帧时写入。原先每圈都 write 同一 JPEG，会在慢客户端或网络上
            # 把 TCP 缓冲和 CPU 打满，表现为越播越卡。
            last_sent: Optional[bytes] = None
            try:
                while True:
                    jpeg = frame_getter()
                    if not jpeg:
                        time.sleep(0.02)
                        continue
                    if jpeg is last_sent:
                        time.sleep(0.005)
                        continue
                    last_sent = jpeg
                    self.wfile.write(sep + jpeg + b"\r\n")
                    try:
                        self.wfile.flush()
                    except Exception:
                        pass
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception:
                pass

        def _send_stream_config(self) -> None:
            """与业务后端 GET /api/video/stream-config 语义对齐：返回浏览器可拉的 MJPEG/HLS URL。"""
            base = _request_public_base(self)
            mjpeg_url = base + canonical
            video_feed_url = base + VIDEO_FEED_PATH
            body = {
                "hls_playlist_url": None,
                "mjpeg_url": mjpeg_url,
                "video_feed_url": video_feed_url,
                "hlsPlaylistUrl": None,
                "mjpegUrl": mjpeg_url,
                "videoFeedUrl": video_feed_url,
            }
            raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(raw)

        def do_OPTIONS(self) -> None:
            if urlparse(self.path).path == STREAM_CONFIG_PATH:
                self.send_response(204)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "*")
                self.end_headers()
            else:
                self.send_error(404)

        def _send_index_html(self) -> None:
            self.send_response(200)
            self.send_header("Content-type", "text/html; charset=utf-8")
            self.end_headers()
            body = (
                f"<!DOCTYPE html><html><head><meta charset=utf-8><title>车载视频</title></head>"
                f"<body><h1>MJPEG</h1><ul>"
                f"<li>推荐（Flask 风格路径）：<a href=\"{VIDEO_FEED_PATH}\">{VIDEO_FEED_PATH}</a></li>"
                f"<li>兼容：<a href=\"{LEGACY_MJPEG_PATH}\">{LEGACY_MJPEG_PATH}</a></li>"
                f"<li>配置中的主路径：<a href=\"{canonical}\">{canonical}</a></li>"
                f"<li><a href=\"{PREVIEW_PATH}\">巡检预览页</a>（拉 stream-config 显示画面）</li>"
                f"<li><a href=\"{STREAM_CONFIG_PATH}\">stream-config JSON</a>（写入后端 VIDEO_MJPEG_URL 用）</li>"
                f"</ul>"
                f'<p><img src="{VIDEO_FEED_PATH}" style="max-width:100%%" alt="video"/></p></body></html>'
            )
            self.wfile.write(body.encode("utf-8"))

        def _send_preview_page(self) -> None:
            self.send_response(200)
            self.send_header("Content-type", "text/html; charset=utf-8")
            self.end_headers()
            html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>视频巡检预览</title>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 1rem; background: #111; color: #eee; }}
    img {{ max-width: 100%; height: auto; border: 1px solid #444; }}
    code {{ background: #222; padding: 0.2em 0.4em; }}
    .err {{ color: #f66; }}
  </style>
</head>
<body>
  <h1>视频显示（对接说明 §5）</h1>
  <p id="status">正在请求 <code>{STREAM_CONFIG_PATH}</code>…</p>
  <p id="url"></p>
  <img id="feed" alt="video" />
  <script>
    fetch("{STREAM_CONFIG_PATH}")
      .then(r => r.json())
      .then(cfg => {{
        const u = cfg.video_feed_url || cfg.videoFeedUrl || cfg.mjpeg_url || cfg.mjpegUrl;
        document.getElementById("status").textContent = u ? "播放中" : "未配置视频 URL";
        if (u) {{
          document.getElementById("url").innerHTML = "视频 URL：<code>" + u + "</code>";
          document.getElementById("feed").src = u;
        }}
      }})
      .catch(e => {{
        document.getElementById("status").innerHTML = '<span class="err">加载失败：' + e + "</span>";
      }});
  </script>
</body>
</html>"""
            self.wfile.write(html.encode("utf-8"))

    return Handler


def run_server(
    frame_source: FrameSource,
    host: str,
    port: int,
    path: str,
) -> ThreadingHTTPServer:
    boundary = b"frame"
    handler = _build_handler(path, frame_source.get_jpeg, boundary)
    return ThreadingHTTPServer((host, port), handler)


class MjpegStreamService:
    """后台线程跑 ThreadingHTTPServer，供 main.py 与传感器同进程启动。"""

    def __init__(
        self,
        host: str,
        port: int,
        path: str,
        camera_index: int,
        width: int,
        height: int,
        fps: float,
        jpeg_quality: int,
        prefer_mjpg: bool = True,
        buffer_size: int = 1,
        open_retry_sec: float = 2.0,
        camera_device: Optional[str] = None,
    ):
        self.host = host
        self.port = int(port)
        self.path = path if path.startswith("/") else "/" + path
        dev: Union[int, str] = camera_index
        if isinstance(camera_device, str) and camera_device.strip():
            dev = camera_device.strip()
        self._src = FrameSource(
            device=dev,
            width=width,
            height=height,
            fps=fps,
            jpeg_quality=jpeg_quality,
            prefer_mjpg=prefer_mjpg,
            buffer_size=buffer_size,
            open_retry_sec=open_retry_sec,
        )
        self._httpd: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    def set_frame_processor(self, hook: Optional[Callable[[Any], Any]]) -> None:
        """循迹等：在 JPEG 编码前处理 BGR 帧；None 表示原始画面。"""
        self._src.set_pre_encode_hook(hook)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._src.start()
        self._httpd = run_server(self._src, self.host, self.port, self.path)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True, name="mjpeg-http")
        self._thread.start()
        print(
            f"视频流: http://{self.host}:{self.port}{VIDEO_FEED_PATH} （及 {self.path}、{LEGACY_MJPEG_PATH}）"
            f"  | 预览 http://{self.host}:{self.port}{PREVIEW_PATH}  | {STREAM_CONFIG_PATH}",
            flush=True,
        )
        print(
            "后端 .env 示例 VIDEO_MJPEG_URL=http://<车机IP>:8080/video_feed"
            "（或 /mjpeg；须与 config 端口一致，浏览器须能访问车机）",
            flush=True,
        )

    def stop(self) -> None:
        if self._httpd:
            try:
                self._httpd.shutdown()
            except Exception:
                pass
            try:
                self._httpd.server_close()
            except Exception:
                pass
            self._httpd = None
        self._src.stop()
        self._thread = None

    def wait_forever(self) -> None:
        """独立进程 CLI：主线程阻塞。"""
        try:
            while self._thread and self._thread.is_alive():
                self._thread.join(timeout=1.0)
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()


def serve_forever(
    host: str,
    port: int,
    path: str,
    camera_index: int,
    width: int,
    height: int,
    fps: float,
    jpeg_quality: int,
    prefer_mjpg: bool = True,
    buffer_size: int = 1,
    open_retry_sec: float = 2.0,
    camera_device: Optional[str] = None,
) -> None:
    import time

    svc = MjpegStreamService(
        host,
        port,
        path,
        camera_index,
        width,
        height,
        fps,
        jpeg_quality,
        prefer_mjpg=prefer_mjpg,
        buffer_size=buffer_size,
        open_retry_sec=open_retry_sec,
        camera_device=camera_device,
    )
    svc.start()
    try:
        while svc._thread and svc._thread.is_alive():
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        svc.stop()
