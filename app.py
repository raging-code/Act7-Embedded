"""
app.py
------
Flask web application for the voice-controlled LED system
(Embedded Systems Laboratory Module, Activity 7).

Run with:
    python app.py

Then open http://localhost:5000 (or http://<pi-ip>:5000 from another
device on the same network) in a browser, and use the push-to-talk
button to issue voice commands like:

    "turn on led 1"
    "turn off led 2"
    "turn on led 1 and led 2"
    "turn off all leds"
"""

import logging

from flask import Flask, jsonify, render_template, send_file

from command_parser import parse_command
from gpio_controller import led_controller

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("app")

app = Flask(__name__)


@app.route("/")
def index():
    return render_template(
        "index.html",
        is_hardware=led_controller.is_hardware,
    )


@app.route("/api/status")
def api_status():
    """Current state of all LEDs + whether we're on real hardware."""
    return jsonify(
        {
            "leds": led_controller.get_states(),
            "is_hardware": led_controller.is_hardware,
        }
    )


@app.route("/api/led/<int:led_id>/<string:state>", methods=["POST"])
def api_set_led(led_id, state):
    """Manual on-screen toggle (not voice) -- lets you test LEDs directly."""
    if state not in ("on", "off"):
        return jsonify({"ok": False, "error": "state must be 'on' or 'off'"}), 400
    ok = led_controller.set_led(led_id, state == "on")
    if not ok:
        return jsonify({"ok": False, "error": f"invalid LED id {led_id}"}), 400
    return jsonify({"ok": True, "leds": led_controller.get_states()})


@app.route("/api/last-audio")
def api_last_audio():
    """The most recent mic capture, so you can hear what the app heard."""
    try:
        from speech_engine import LAST_CAPTURE_PATH
        resp = send_file(LAST_CAPTURE_PATH, mimetype="audio/wav", max_age=0)
    except (ImportError, OSError, FileNotFoundError):
        return jsonify({"ok": False, "error": "No capture yet."}), 404
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/api/voice-command", methods=["POST"])
def api_voice_command():
    """
    Triggered by the push-to-talk button. Records a few seconds of
    audio from the server-side microphone (the USB lavalier mic
    plugged into the Pi / PC), transcribes it offline with Vosk,
    parses the result, and applies it to the LEDs.
    """
    try:
        from speech_engine import get_speech_engine
    except FileNotFoundError as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    except OSError as e:
        return (
            jsonify(
                {
                    "ok": False,
                    "error": (
                        "Audio input library not available on this machine "
                        f"({e}). On Linux/Raspberry Pi OS, install it with: "
                        "sudo apt install -y portaudio19-dev"
                    ),
                }
            ),
            500,
        )

    try:
        engine = get_speech_engine()
        transcript = engine.listen_and_transcribe()
    except FileNotFoundError as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    except Exception as e:
        logger.exception("Voice capture/transcription failed")
        return jsonify({"ok": False, "error": f"Microphone/transcription error: {e}"}), 500

    if not transcript:
        return jsonify(
            {
                "ok": False,
                "transcript": "",
                "error": "No speech detected. Try again.",
            }
        )

    command = parse_command(transcript)

    if command is None:
        return jsonify(
            {
                "ok": False,
                "transcript": transcript,
                "error": "Could not understand that as a command.",
            }
        )

    led_controller.set_many(command["leds"], command["action"] == "on")

    return jsonify(
        {
            "ok": True,
            "transcript": transcript,
            "command": command,
            "leds": led_controller.get_states(),
        }
    )


if __name__ == "__main__":
    try:
        # host="0.0.0.0" so you can also reach it from your phone/laptop
        # over the LAN when running on the Pi.
        app.run(host="0.0.0.0", port=5000, debug=True)
    finally:
        led_controller.cleanup()
