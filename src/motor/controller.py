import time


class VehicleController:
    def __init__(self, driver):
        self.driver = driver
        self.mode = "manual"
        self.last_command_time = 0.0
        self.timeout = 5

    def forward(self, speed=50):
        self.driver.set_left_motor(speed)
        self.driver.set_right_motor(speed)
        self.last_command_time = time.time()

    def backward(self, speed=50):
        self.driver.set_left_motor(-speed)
        self.driver.set_right_motor(-speed)
        self.last_command_time = time.time()

    def turn_left(self, speed=50):
        self.driver.set_left_motor(int(speed * 0.3))
        self.driver.set_right_motor(speed)
        self.last_command_time = time.time()

    def turn_right(self, speed=50):
        self.driver.set_left_motor(speed)
        self.driver.set_right_motor(int(speed * 0.3))
        self.last_command_time = time.time()

    def stop(self):
        self.driver.stop()

    def check_timeout(self):
        if time.time() - self.last_command_time > self.timeout:
            self.stop()
            return True
        return False
