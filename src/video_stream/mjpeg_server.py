"""
MJPEG over HTTP（multipart/x-mixed-replace），与《硬件端-Software通讯与对接说明》§5 一致。

- 流：GET {path}（默认 /mjpeg）
- 与业务后端同路径的配置接口（便于联调/本地验证）：GET /api/video/stream-config
- 巡检页风格预览：GET /preview
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Optional
from urllib.parse import urlparse

from video_stream.frame_source import FrameSource

STREAM_CONFIG_PATH = "/api/video/stream-config"
PREVIEW_PATH = "/preview"


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
    mjpeg_path: str,
    frame_getter: Callable[[], Optional[bytes]],
    boundary: bytes,
) -> type:
    mjpeg_path = mjpeg_path if mjpeg_path.startswith("/") else "/" + mjpeg_path

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

            if path != mjpeg_path:
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
            try:
                while True:
                    jpeg = frame_getter()
                    if jpeg:
                        self.wfile.write(sep + jpeg + b"\r\n")
                    else:
                        import time

                        time.sleep(0.02)
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception:
                pass

        def _send_stream_config(self) -> None:
            """与业务后端 GET /api/video/stream-config 语义对齐：返回浏览器可拉的 MJPEG/HLS URL。"""
            base = _request_public_base(self)
            mjpeg_url = base + mjpeg_path
            body = {
                "hls_playlist_url": None,
                "mjpeg_url": mjpeg_url,
                "hlsPlaylistUrl": None,
                "mjpegUrl": mjpeg_url,
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
                f"<li>流：<a href=\"{mjpeg_path}\">{mjpeg_path}</a></li>"
                f"<li><a href=\"{PREVIEW_PATH}\">巡检预览页</a>（拉 stream-config 显示画面）</li>"
                f"<li><a href=\"{STREAM_CONFIG_PATH}\">stream-config JSON</a>（写入后端 VIDEO_MJPEG_URL 用）</li>"
                f"</ul>"
                f'<img src="{mjpeg_path}" style="max-width:100%%" alt="video"/></body></html>'
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
        const u = cfg.mjpeg_url || cfg.mjpegUrl;
        document.getElementById("status").textContent = u ? "播放中" : "未配置 mjpeg_url";
        if (u) {{
          document.getElementById("url").innerHTML = "MJPEG URL：<code>" + u + "</code>";
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
    ):
        self.host = host
        self.port = int(port)
        self.path = path if path.startswith("/") else "/" + path
        self._src = FrameSource(
            device=camera_index,
            width=width,
            height=height,
            fps=fps,
            jpeg_quality=jpeg_quality,
        )
        self._httpd: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._src.start()
        self._httpd = run_server(self._src, self.host, self.port, self.path)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True, name="mjpeg-http")
        self._thread.start()
        print(
            f"视频流: http://{self.host}:{self.port}{self.path}  | 预览 http://{self.host}:{self.port}{PREVIEW_PATH}  | {STREAM_CONFIG_PATH}",
            flush=True,
        )
        print(
            "后端 .env 设置 VIDEO_MJPEG_URL=（上列 MJPEG 完整 URL，浏览器须能访问；本机试跑可用 http://127.0.0.1:8080/mjpeg）",
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
) -> None:
    import time

    svc = MjpegStreamService(host, port, path, camera_index, width, height, fps, jpeg_quality)
    svc.start()
    try:
        while svc._thread and svc._thread.is_alive():
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        svc.stop()
