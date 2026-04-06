"""车端 HTTP 视频（MJPEG）；实现见 `video_stream` 包，与《硬件端-Software通讯与对接说明》§5 一致。"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from video_stream.mjpeg_server import MjpegStreamService

__all__: list = []
