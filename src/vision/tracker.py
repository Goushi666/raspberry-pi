from utils.pid import PIDController


class LineTracker:
    def __init__(self, camera, processor, controller):
        self.camera = camera
        self.processor = processor
        self.controller = controller
        self.pid = PIDController(kp=0.5, ki=0.1, kd=0.2)
        self.running = False
        self.base_speed = 40

    def start(self):
        self.running = True
        self.camera.start()

    def update(self):
        if not self.running:
            return None

        frame = self.camera.read()
        if frame is None:
            return None

        binary = self.processor.preprocess(frame)
        center_x = self.processor.find_line_center(binary)

        if center_x is None:
            self.controller.stop()
            return None

        frame_center = frame.shape[1] // 2
        deviation = center_x - frame_center
        correction = self.pid.compute(deviation)

        left_speed = self.base_speed - correction
        right_speed = self.base_speed + correction

        self.controller.driver.set_left_motor(int(left_speed))
        self.controller.driver.set_right_motor(int(right_speed))

        return {"deviation": deviation, "correction": correction}

    def stop(self):
        self.running = False
        self.controller.stop()
        self.camera.stop()
