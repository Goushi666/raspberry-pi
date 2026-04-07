# Web 端：MJPEG 视频显示与对接说明

树莓派车端使用 **MJPEG over HTTP**（`multipart/x-mixed-replace; boundary=frame`），与常见 Flask 示例一致。前端用 **`<img>`** 即可显示，无需视频 JS 库。

---

## 1. 车端提供的地址（启用 `video_stream.enabled: true` 后）

假设车机 IP 为 `192.168.137.114`、端口为 `8080`（见 `config/config.yaml` → `video_stream.port`）：

| 用途 | URL |
|------|-----|
| **推荐（与 Flask `/video_feed` 习惯一致）** | `http://192.168.137.114:8080/video_feed` |
| 兼容旧文档 / 链接 | `http://192.168.137.114:8080/mjpeg` |
| 配置中的主路径 | `http://192.168.137.114:8080` + `video_stream.path`（默认与上列 `/video_feed` 相同） |

三者 **是同一路视频流**，任选其一即可。

---

## 2. 前端最小示例（零依赖）

### 2.1 写死车机地址（仅局域网联调）

```html
<img
  src="http://192.168.137.114:8080/video_feed"
  alt="车载摄像头"
  style="max-width: 100%; height: auto;"
/>
```

### 2.2 先拉配置再绑定（推荐：端口/路径变更时只改车端配置）

车端提供 **`GET /api/video/stream-config`**，返回 JSON（节选）：

```json
{
  "video_feed_url": "http://192.168.137.114:8080/video_feed",
  "videoFeedUrl": "http://192.168.137.114:8080/video_feed",
  "mjpeg_url": "http://192.168.137.114:8080/video_feed",
  "mjpegUrl": "http://192.168.137.114:8080/video_feed",
  "hls_playlist_url": null,
  "hlsPlaylistUrl": null
}
```

前端逻辑：**优先使用 `video_feed_url` / `videoFeedUrl`，否则回退 `mjpeg_url` / `mjpegUrl`**。

```html
<img id="cam" alt="车载摄像头" style="max-width:100%;height:auto;" />
<script>
  fetch("http://192.168.137.114:8080/api/video/stream-config")
    .then((r) => r.json())
    .then((cfg) => {
      const url =
        cfg.video_feed_url ||
        cfg.videoFeedUrl ||
        cfg.mjpeg_url ||
        cfg.mjpegUrl;
      if (url) document.getElementById("cam").src = url;
    });
</script>
```

注意：若前端页面与车机 **不同源**，`fetch` 可能被 CORS 限制；`<img src="http://车机...">` 一般仍可显示画面。联调可把车端与页面放在同源，或由 **业务后端** 代理 `stream-config` 与视频流（见下文）。

---

## 3. 业务后端（可选）要做什么

浏览器 **无法直接访问车机**（例如用户走公网 HTTPS，车机在内网）时，由后端：

1. 保存车机 MJPEG 完整 URL，例如：  
   `VIDEO_MJPEG_URL=http://192.168.137.114:8080/video_feed`
2. 向自己的前端下发 **同源** 地址，例如：  
   `https://api.example.com/video/proxy`（由后端 **字节流转发** 车机响应，**不要**再 JPEG 压缩）
3. 前端仍只用 `<img src="https://api.example.com/video/proxy">`

响应头需与车端一致，例如：

```http
Content-Type: multipart/x-mixed-replace; boundary=frame
Cache-Control: no-cache, no-store, must-revalidate
```

### 3.1 Flask 参考（后端自己从摄像头推流时）

若视频源在后端本机（非树莓派），可用与车端相同思路：

```python
import cv2
from flask import Flask, Response

app = Flask(__name__)
cap = cv2.VideoCapture(0)

def gen_frames():
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        _, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        yield (
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n\r\n" + buffer.tobytes() + b"\r\n"
        )

@app.route("/video_feed")
def video_feed():
    return Response(
        gen_frames(),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
```

树莓派车端 **已实现等价 HTTP 行为**（OpenCV 在车内采集，路径为 `/video_feed` 等），前端对接方式与上式一致。

---

## 4. HTTPS 与混合内容

若网站是 **`https://`**，而车机流是 **`http://`**，浏览器可能 **阻止** 在页面中加载该流（混合内容）。处理方式：

- 给视频流也走 **HTTPS**（反向代理 + 证书），或  
- 使用 **同源代理**（后端 `https` 转发车机 `http` 流）。

---

## 5. 联调检查

1. 车端：`video_stream.enabled: true`，`python src/main.py` 已运行。  
2. 浏览器能打开：`http://车机IP:端口/video_feed`（应持续加载，画面刷新）。  
3. 或使用：`http://车机IP:端口/preview` 内置预览页。  

---

## 6. 小结

- **显示**：`<img src="…/video_feed">` 即可。  
- **拿 URL**：`GET …/api/video/stream-config` → `video_feed_url` / `videoFeedUrl`。  
- **车端与 Flask MJPEG 示例** 在协议与 `boundary=frame` 上对齐，便于 Web 与后端统一理解。

更多与业务后端环境变量约定见：`config/backend.video.env.example`。

---

## 7. 画面卡顿、模糊时怎么排查

**车端（树莓派）**

- 高分辨率下若未使用摄像头 **MJPG** 压缩，USB2 上 **YUYV 带宽不够**，实际帧率会极低，画面像「糊在一起」。车端已默认 `prefer_mjpg: true`，启动后请看日志里 **FOURCC** 是否为 **MJPG**；若不是，可换支持 MJPG 的摄像头，或降低 `video_stream.width` / `height`。
- **分辨率与 JPEG 质量**直接决定「清不清」：曾为保帧率用过较低分辨率（如 960×540），大屏上会明显发糊。MJPG 稳定后建议使用 **1280×720**，并把 `jpeg_quality` 提到 **90～96**（再高收益递减、码率暴涨）。若改完后又卡顿，再降分辨率或 fps。
- 修改 `config/config.yaml` 后需 **重启** `python src/main.py`。

**业务后端代理**

- 若经 Nginx/网关转发 MJPEG，需 **关闭对响应体的缓冲**（如 `proxy_buffering off`），否则浏览器端会表现为延迟大、像幻灯片。
