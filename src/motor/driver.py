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

    def set_left_motor(self, speed: int):
        if speed > 0:
            GPIO.output(self.in1, GPIO.HIGH)
            GPIO.output(self.in2, GPIO.LOW)
        elif speed < 0:
            GPIO.output(self.in1, GPIO.LOW)
            GPIO.output(self.in2, GPIO.HIGH)
        else:
            GPIO.output(self.in1, GPIO.LOW)
            GPIO.output(self.in2, GPIO.LOW)

        self.pwm_left.ChangeDutyCycle(abs(speed))

    def set_right_motor(self, speed: int):
        if speed > 0:
            GPIO.output(self.in3, GPIO.HIGH)
            GPIO.output(self.in4, GPIO.LOW)
        elif speed < 0:
            GPIO.output(self.in3, GPIO.LOW)
            GPIO.output(self.in4, GPIO.HIGH)
        else:
            GPIO.output(self.in3, GPIO.LOW)
            GPIO.output(self.in4, GPIO.LOW)

        self.pwm_right.ChangeDutyCycle(abs(speed))

    def stop(self):
        self.set_left_motor(0)
        self.set_right_motor(0)

    def cleanup(self):
        self.stop()
        self.pwm_left.stop()
        self.pwm_right.stop()
        GPIO.cleanup()
