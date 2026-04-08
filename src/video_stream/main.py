import argparse
import logging
import sys
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

ROOT = Path(__file__).resolve().parent.parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from servo.pca_arm import PCA9685Arm

from video_stream.mjpeg_server import serve_forever


def _load_full_config() -> Dict[str, Any]:
    cfg_path = ROOT / "config" / "config.yaml"
    if not cfg_path.is_file():
        return {}
    with open(cfg_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _maybe_start_arm(cfg: Dict[str, Any]) -> Optional[PCA9685Arm]:
    sc = cfg.get("servo") or {}
    if not sc.get("enabled") or not (sc.get("arm") or {}).get("enabled"):
        return None
    log = logging.getLogger("video_stream.arm")
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        arm = PCA9685Arm(log, sc)
        arm.initialize_startup()
        arm.start_worker()
        log.info("机械臂已初始化；MQTT 关节指令请使用 src/main.py 全功能进程")
        return arm
    except Exception as e:
        log.warning("机械臂初始化失败，已跳过: %s", e)
        return None


def main() -> None:
    cfg = _load_full_config()
    vs = cfg.get("video_stream") or {}
    ap = argparse.ArgumentParser(description="车载 MJPEG HTTP 视频流（见硬件端对接说明 §5）")
    ap.add_argument("--host", default=vs.get("bind", "0.0.0.0"))
    ap.add_argument("--port", type=int, default=int(vs.get("port", 8080)))
    ap.add_argument("--path", default=vs.get("path", "/video_feed"))
    ap.add_argument("--camera", type=int, default=int(vs.get("camera_index", 0)))
    ap.add_argument("--width", type=int, default=int(vs.get("width", 640)))
    ap.add_argument("--height", type=int, default=int(vs.get("height", 480)))
    ap.add_argument("--fps", type=float, default=float(vs.get("fps", 12)))
    ap.add_argument("--quality", type=int, default=int(vs.get("jpeg_quality", 75)))
    ap.add_argument("--no-mjpg", action="store_true", help="不请求 MJPG 像素格式")
    ap.add_argument("--buffer-size", type=int, default=int(vs.get("buffer_size", 1)))
    ap.add_argument("--open-retry", type=float, default=float(vs.get("open_retry_sec", 2.0)))
    args = ap.parse_args()

    arm = _maybe_start_arm(cfg)
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
    finally:
        if arm is not None:
            try:
                arm.close()
            except Exception:
                pass


if __name__ == "__main__":
    main()
