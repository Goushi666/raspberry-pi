import time


class PIDController:
    def __init__(self, kp, ki, kd):
        self.kp = kp
        self.ki = ki
        self.kd = kd
        self.last_error = 0.0
        self.integral = 0.0
        self.last_time = time.time()

    def compute(self, error: float) -> float:
        current_time = time.time()
        dt = current_time - self.last_time

        if dt <= 0:
            dt = 0.01

        p = self.kp * error
        self.integral += error * dt
        i = self.ki * self.integral
        derivative = (error - self.last_error) / dt
        d = self.kd * derivative

        self.last_error = error
        self.last_time = current_time

        return p + i + d

    def reset(self):
        self.last_error = 0.0
        self.integral = 0.0
        self.last_time = time.time()
