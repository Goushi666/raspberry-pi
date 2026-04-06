#!/bin/bash
# 可选：用 FFmpeg 从摄像头输出 HLS（文档推荐生产环境）。
# 用法：./scripts/hls_from_camera.sh
# 另开终端：cd /tmp/rpi-hls && python3 -m http.server 8888
# 后端配置 VIDEO_HLS_PLAYLIST_URL=http://<树莓派IP>:8888/index.m3u8
set -euo pipefail
OUT="${HLS_OUT:-/tmp/rpi-hls}"
mkdir -p "$OUT"
# Pi 上常见为 /dev/video0；CSI 摄像头在 Bookworm 上多为 v4l2
INPUT="${VIDEO_DEVICE:-/dev/video0}"
ffmpeg -y \
  -f v4l2 -framerate 15 -video_size 640x480 -i "$INPUT" \
  -c:v libx264 -preset veryfast -tune zerolatency -pix_fmt yuv420p \
  -f hls -hls_time 2 -hls_list_size 5 -hls_flags delete_segments+append_list \
  "$OUT/index.m3u8"
