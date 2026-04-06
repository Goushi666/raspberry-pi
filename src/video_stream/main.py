import argparse
import sys
from pathlib import Path

import yaml

from video_stream.mjpeg_server import serve_forever


def _load_video_config() -> dict:
    root = Path(__file__).resolve().parent.parent.parent
    cfg_path = root / "config" / "config.yaml"
    if not cfg_path.is_file():
        return {}
    with open(cfg_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get("video_stream") or {}


def main() -> None:
    cfg = _load_video_config()
    ap = argparse.ArgumentParser(description="车载 MJPEG HTTP 视频流（见硬件端对接说明 §5）")
    ap.add_argument("--host", default=cfg.get("bind", "0.0.0.0"))
    ap.add_argument("--port", type=int, default=int(cfg.get("port", 8080)))
    ap.add_argument("--path", default=cfg.get("path", "/video_feed"))
    ap.add_argument("--camera", type=int, default=int(cfg.get("camera_index", 0)))
    ap.add_argument("--width", type=int, default=int(cfg.get("width", 640)))
    ap.add_argument("--height", type=int, default=int(cfg.get("height", 480)))
    ap.add_argument("--fps", type=float, default=float(cfg.get("fps", 12)))
    ap.add_argument("--quality", type=int, default=int(cfg.get("jpeg_quality", 75)))
    ap.add_argument("--no-mjpg", action="store_true", help="不请求 MJPG 像素格式")
    ap.add_argument("--buffer-size", type=int, default=int(cfg.get("buffer_size", 1)))
    ap.add_argument("--open-retry", type=float, default=float(cfg.get("open_retry_sec", 2.0)))
    args = ap.parse_args()

    try:
        serve_forever(
            host=str(args.host),
            port=args.port,
            path=str(args.path),
            camera_index=args.camera,
            width=args.width,
            height=args.height,
            fps=args.fps,
            jpeg_quality=args.quality,
            prefer_mjpg=not args.no_mjpg,
            buffer_size=args.buffer_size,
            open_retry_sec=args.open_retry,
        )
    except KeyboardInterrupt:
        sys.exit(0)
    except OSError as e:
        print(f"无法绑定 {args.host}:{args.port} — {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
