import threading

try:
    import cv2
except ImportError:
    cv2 = None


class Camera:
    def __init__(self, resolution=(640, 480), framerate=30):
        if cv2 is None:
            raise RuntimeError("请安装 OpenCV: pip install opencv-python-headless")
        self.cap = cv2.VideoCapture(0)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, resolution[0])
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, resolution[1])
        self.cap.set(cv2.CAP_PROP_FPS, framerate)
        self.frame = None
        self.running = False

    def start(self):
        self.running = True
        threading.Thread(target=self._capture_loop, daemon=True).start()

    def _capture_loop(self):
        while self.running:
            ret, frame = self.cap.read()
            if ret:
                self.frame = frame

    def read(self):
        return self.frame

    def stop(self):
        self.running = False
        self.cap.release()
