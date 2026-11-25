"""
gpio_controller.py
-------------------
Hardware abstraction layer for the LED controller.

On a Raspberry Pi (where the RPi.GPIO library is importable), this drives
real GPIO pins. On any other machine (e.g. your PC in VS Code), it falls
back to an in-memory mock so you can develop and test the Flask app, the
web UI, and the voice command parsing without any hardware attached.

Nothing else in the app needs to know which mode it's in -- it just calls
led_on(), led_off(), get_states(), etc.
"""

import logging

logger = logging.getLogger("gpio_controller")

# GPIO pin assignments (BCM numbering) -- matches the manual's example
# (GPIO 17, 27, 22 for LED 1, 2, 3)
LED_PINS = {
    1: 17,
    2: 27,
    3: 22,
}

IS_RASPBERRY_PI = False
GPIO = None

try:
    import RPi.GPIO as _GPIO  # type: ignore

    GPIO = _GPIO
    IS_RASPBERRY_PI = True
except (ImportError, RuntimeError):
    # ImportError -> not on a Pi at all (e.g. your PC)
    # RuntimeError -> RPi.GPIO installed but not actually running on Pi hardware
    IS_RASPBERRY_PI = False


class _RealGPIOBackend:
    """Drives actual GPIO pins on a Raspberry Pi."""

    def __init__(self):
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        self._state = {}
        for led_id, pin in LED_PINS.items():
            GPIO.setup(pin, GPIO.OUT)
            GPIO.output(pin, GPIO.LOW)
            self._state[led_id] = False
        logger.info("Real GPIO backend initialized on pins %s", LED_PINS)

    def set_led(self, led_id: int, on: bool):
        pin = LED_PINS[led_id]
        GPIO.output(pin, GPIO.HIGH if on else GPIO.LOW)
        self._state[led_id] = on

    def get_states(self):
        return dict(self._state)

    def cleanup(self):
        GPIO.cleanup()


class _MockGPIOBackend:
    """Simulates LEDs in memory -- used on your PC when there's no real GPIO."""

    def __init__(self):
        self._state = {led_id: False for led_id in LED_PINS}
        logger.info("Mock GPIO backend initialized (no real hardware detected)")

    def set_led(self, led_id: int, on: bool):
        self._state[led_id] = on
        pin = LED_PINS[led_id]
        logger.info(
            "[MOCK] LED %s (GPIO %s) -> %s", led_id, pin, "ON" if on else "OFF"
        )

    def get_states(self):
        return dict(self._state)

    def cleanup(self):
        logger.info("[MOCK] cleanup (no-op)")


class LEDController:
    """
    Public interface used by the rest of the app.
    Picks the real or mock backend automatically.
    """

    def __init__(self):
        self.is_hardware = IS_RASPBERRY_PI
        self._backend = _RealGPIOBackend() if IS_RASPBERRY_PI else _MockGPIOBackend()

    def set_led(self, led_id: int, on: bool) -> bool:
        if led_id not in LED_PINS:
            logger.warning("Unknown LED id: %s", led_id)
            return False
        self._backend.set_led(led_id, on)
        return True

    def set_many(self, led_ids, on: bool):
        results = {}
        for led_id in led_ids:
            results[led_id] = self.set_led(led_id, on)
        return results

    def set_all(self, on: bool):
        return self.set_many(LED_PINS.keys(), on)

    def get_states(self) -> dict:
        """Returns {1: True/False, 2: True/False, 3: True/False}"""
        return self._backend.get_states()

    def cleanup(self):
        self._backend.cleanup()


# Single shared instance used by the Flask app
led_controller = LEDController()
