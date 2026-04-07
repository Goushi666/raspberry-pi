import RPi.GPIO as GPIO


class L298NDriver:
    def __init__(self, ena, in1, in2, enb, in3, in4):
        self.ena = ena
        self.in1 = in1
        self.in2 = in2
        self.enb = enb
        self.in3 = in3
        self.in4 = in4

        GPIO.setmode(GPIO.BCM)
        GPIO.setup([ena, in1, in2, enb, in3, in4], GPIO.OUT)

        self.pwm_left = GPIO.PWM(ena, 1000)
        self.pwm_right = GPIO.PWM(enb, 1000)
        self.pwm_left.start(0)
        self.pwm_right.start(0)

    @staticmethod
    def _duty(speed: int) -> int:
        """MQTT speed 为 0~100 占空比；内部统一钳位。"""
        return max(0, min(100, abs(int(speed))))

    def set_left_motor(self, speed: int):
        duty = self._duty(speed)
        if speed > 0:
            GPIO.output(self.in1, GPIO.HIGH)
            GPIO.output(self.in2, GPIO.LOW)
        elif speed < 0:
            GPIO.output(self.in1, GPIO.LOW)
            GPIO.output(self.in2, GPIO.HIGH)
        else:
            GPIO.output(self.in1, GPIO.LOW)
            GPIO.output(self.in2, GPIO.LOW)

        self.pwm_left.ChangeDutyCycle(duty)

    def set_right_motor(self, speed: int):
        duty = self._duty(speed)
        if speed > 0:
            GPIO.output(self.in3, GPIO.HIGH)
            GPIO.output(self.in4, GPIO.LOW)
        elif speed < 0:
            GPIO.output(self.in3, GPIO.LOW)
            GPIO.output(self.in4, GPIO.HIGH)
        else:
            GPIO.output(self.in3, GPIO.LOW)
            GPIO.output(self.in4, GPIO.LOW)

        self.pwm_right.ChangeDutyCycle(duty)

    def stop(self):
        self.set_left_motor(0)
        self.set_right_motor(0)

    def cleanup(self):
        self.stop()
        self.pwm_left.stop()
        self.pwm_right.stop()
        GPIO.cleanup()
