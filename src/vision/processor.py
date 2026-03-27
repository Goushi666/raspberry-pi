try:
    import cv2
except ImportError:
    cv2 = None


class ImageProcessor:
    def __init__(self, threshold=127):
        if cv2 is None:
            raise RuntimeError("请安装 OpenCV")
        self.threshold = threshold

    def preprocess(self, frame):
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)
        _, binary = cv2.threshold(blurred, self.threshold, 255, cv2.THRESH_BINARY)
        return binary

    def find_line_center(self, binary):
        height, width = binary.shape
        roi = binary[int(height * 0.6) : height, :]
        contours, _ = cv2.findContours(roi, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        if not contours:
            return None

        largest = max(contours, key=cv2.contourArea)
        M = cv2.moments(largest)

        if M["m00"] == 0:
            return None

        cx = int(M["m10"] / M["m00"])
        return cx
