import time


class VehicleController:
    def __init__(self, driver, command_timeout_sec: float = 5.0):
        self.driver = driver
        self.mode = "manual"
        self.last_command_time = 0.0
        self.timeout = float(command_timeout_sec)

    @staticmethod
    def _sp(speed) -> int:
        return max(0, min(100, int(speed)))

    def forward(self, speed=50):
        s = self._sp(speed)
        self.driver.set_left_motor(s)
        self.driver.set_right_motor(s)
        self.last_command_time = time.time()

    def backward(self, speed=50):
        s = self._sp(speed)
        self.driver.set_left_motor(-s)
        self.driver.set_right_motor(-s)
        self.last_command_time = time.time()

    def turn_left(self, speed=50):
        """左转：左轮 speed、右轮停（与当前 L298N 接线一致；若反了可对调 turn_left/turn_right）。"""
        s = self._sp(speed)
        self.driver.set_left_motor(s)
        self.driver.set_right_motor(0)
        self.last_command_time = time.time()

    def turn_right(self, speed=50):
        """右转：左轮停、右轮 speed。"""
        s = self._sp(speed)
        self.driver.set_left_motor(0)
        self.driver.set_right_motor(s)
        self.last_command_time = time.time()

    def stop(self):
        self.driver.stop()
        self.last_command_time = time.time()

    def check_timeout(self):
        if time.time() - self.last_command_time > self.timeout:
            self.stop()
            return True
        return False
