#!/usr/bin/env python3
"""
Hold-to-talk patch for Act7-Embedded.

Makes the mic record WHILE the button is held (press = start, release = stop
and transcribe), instead of recording 4 seconds after release.

Usage (from the project folder):
    python3 patch_hold_to_talk.py
    python3 patch_hold_to_talk.py /path/to/Act7-Embedded

Safe to run twice. Originals are saved as <file>.bak (first run only).
"""
import os
import re
import shutil
import sys

root = sys.argv[1] if len(sys.argv) > 1 else "."


def path_of(*parts):
    p = os.path.join(root, *parts)
    if not os.path.isfile(p):
        sys.exit(f"ERROR: {p} not found. Run this from the Act7-Embedded folder.")
    return p


def save(p, text):
    if not os.path.exists(p + ".bak"):
        shutil.copyfile(p, p + ".bak")
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)


# ---------------------------------------------------------------- speech_engine.py
NEW_SPEECH_ENGINE = r'''"""
speech_engine.py
-----------------
Offline speech-to-text using Vosk.

Fully offline once the model is downloaded once (no internet needed at
runtime). Works identically on your PC and on the Raspberry Pi -- the
only difference is which physical microphone index gets used, and
sounddevice/PyAudio picks that up automatically from the OS.

Model used: vosk-model-small-en-us-0.15 (~40MB, good speed/accuracy
balance for short voice commands on a Pi 5; also fine on a PC).

Push-to-talk: start_recording() opens the mic when the button is pressed,
stop_and_transcribe() closes it when the button is released and returns
the text. Audio is only captured while the button is held.

Setup (one-time, needs internet the FIRST time only):
    1. pip install vosk sounddevice
    2. Download & unzip the model into ./models/:
       https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip
       -> unzip so you have: models/vosk-model-small-en-us-0.15/
"""

import json
import logging
import os
import tempfile
import threading
import time
import wave

import numpy as np
import sounddevice as sd
from vosk import KaldiRecognizer, Model

logger = logging.getLogger("speech_engine")

MODEL_DIR = os.path.join(os.path.dirname(__file__), "models", "vosk-model-small-en-us-0.15")
MODEL_RATE = 16000  # Vosk's small English model expects 16 kHz audio
RECORD_SECONDS = 4  # length of the fixed-length capture (listen_and_transcribe)
MAX_RECORD_SECONDS = 15  # safety limit while the button is held
MIN_RECORD_SECONDS = 0.3  # shorter than this is treated as an accidental tap

# Restrict Vosk to the words our commands use. This makes recognition far
# more accurate (no more "pain on anyone") because it can't pick other words.
GRAMMAR = json.dumps(
    ["turn switch on off led leds all and one two three", "[unk]"]
)

# Last capture (what Vosk heard) is saved here so the web page can play it.
LAST_CAPTURE_PATH = os.path.join(tempfile.gettempdir(), "act7_last_capture.wav")

# Optional: force a specific mic by index, e.g.  MIC_DEVICE=2 python app.py
# (list devices with: python -m sounddevice)
MIC_DEVICE = int(os.environ["MIC_DEVICE"]) if os.environ.get("MIC_DEVICE") else None


def _to_model_rate(samples: np.ndarray, rate: int) -> np.ndarray:
    """Resample mono int16 audio from `rate` to 16 kHz."""
    if rate == MODEL_RATE:
        return samples
    if rate % MODEL_RATE == 0:  # e.g. 48000 -> 16000: average groups of 3
        factor = rate // MODEL_RATE
        usable = len(samples) - (len(samples) % factor)
        return samples[:usable].reshape(-1, factor).mean(axis=1).astype(np.int16)
    # e.g. 44100 -> 16000: linear interpolation
    n_out = int(len(samples) * MODEL_RATE / rate)
    x_old = np.linspace(0, 1, num=len(samples), endpoint=False)
    x_new = np.linspace(0, 1, num=n_out, endpoint=False)
    return np.interp(x_new, x_old, samples).astype(np.int16)


class SpeechEngine:
    def __init__(self, model_path: str = MODEL_DIR):
        if not os.path.isdir(model_path):
            raise FileNotFoundError(
                f"Vosk model not found at '{model_path}'.\n"
                "Download it from:\n"
                "  https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip\n"
                "and unzip it into the 'models/' folder so the path above exists."
            )
        logger.info("Loading Vosk model from %s", model_path)
        self._model = Model(model_path)
        logger.info("Vosk model loaded.")

        # push-to-talk state
        self._state_lock = threading.Lock()
        self._recording = False
        self._stream = None
        self._chunks = []
        self._chunks_lock = threading.Lock()
        self._rate = MODEL_RATE
        self._channels = 1
        self._started_at = 0.0
        self._timer = None

    # ------------------------------------------------------------------ mic
    def _open_stream(self, callback):
        """
        Open the mic at a rate/channel count it actually supports.
        Many USB mics reject 16 kHz mono on the raw ALSA device, so we
        try 16 kHz first, then the device's native rate, then common rates.
        """
        info = sd.query_devices(MIC_DEVICE, "input")
        native = int(info["default_samplerate"])
        max_ch = int(info["max_input_channels"])
        rates = []
        for r in (MODEL_RATE, native, 48000, 44100, 32000, 22050, 8000):
            if r not in rates:
                rates.append(r)
        channel_options = [c for c in (1, 2) if c <= max_ch] or [1]

        last_err = None
        for rate in rates:
            for ch in channel_options:
                try:
                    stream = sd.RawInputStream(
                        device=MIC_DEVICE,
                        samplerate=rate,
                        dtype="int16",
                        channels=ch,
                        callback=callback,
                    )
                    logger.info("Mic '%s' opened at %d Hz, %d ch", info["name"], rate, ch)
                    return stream, rate, ch
                except sd.PortAudioError as e:
                    last_err = e
        raise RuntimeError(f"Could not open microphone '{info['name']}': {last_err}")

    # --------------------------------------------- hold-to-talk (press/release)
    def start_recording(self):
        """Called when the button is pressed: open the mic and start capturing."""
        with self._state_lock:
            if self._recording:
                if time.time() - self._started_at > MAX_RECORD_SECONDS + 5:
                    logger.warning("Discarding a stale recording that was never stopped.")
                    self._discard_locked()
                else:
                    raise RuntimeError("Already recording.")

            with self._chunks_lock:
                self._chunks = []

            def _callback(indata, frames, time_info, status):
                if status:
                    logger.warning("Audio input status: %s", status)
                with self._chunks_lock:
                    self._chunks.append(bytes(indata))

            stream, rate, channels = self._open_stream(_callback)
            stream.start()
            self._stream, self._rate, self._channels = stream, rate, channels
            self._started_at = time.time()
            self._recording = True
            # Safety: stop capturing (but keep the audio) if the button is held too long.
            self._timer = threading.Timer(MAX_RECORD_SECONDS, self._halt_stream)
            self._timer.daemon = True
            self._timer.start()
            logger.info("Recording started (hold the button, speak, then release)...")

    def _halt_stream(self):
        """Stop and close the mic stream. Keeps the captured audio."""
        stream, self._stream = self._stream, None
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                logger.exception("Error while closing the mic stream")

    def _discard_locked(self):
        self._halt_stream()
        with self._chunks_lock:
            self._chunks = []
        self._recording = False

    def stop_and_transcribe(self) -> str:
        """Called when the button is released: stop capturing and transcribe."""
        with self._state_lock:
            if not self._recording:
                raise RuntimeError("Not recording - press and hold the button first.")
            self._halt_stream()
            self._recording = False
            with self._chunks_lock:
                raw = b"".join(self._chunks)
                self._chunks = []
            rate, channels = self._rate, self._channels
        return self._transcribe(raw, rate, channels)

    # ------------------------------------------------ fixed-length (legacy)
    def listen_and_transcribe(self, seconds: int = RECORD_SECONDS) -> str:
        """Records a fixed number of seconds, then transcribes it."""
        self.start_recording()
        time.sleep(seconds)
        return self.stop_and_transcribe()

    # ---------------------------------------------------------- transcribing
    def _transcribe(self, raw: bytes, rate: int, channels: int) -> str:
        audio = np.frombuffer(raw, dtype=np.int16)
        if channels > 1:
            audio = audio[: len(audio) - (len(audio) % channels)]
            audio = audio.reshape(-1, channels).mean(axis=1).astype(np.int16)
        if audio.size == 0 or audio.size / rate < MIN_RECORD_SECONDS:
            logger.info("Recording too short - ignored.")
            return ""

        logger.info(
            "Captured %.1fs, peak level %d / 32767",
            audio.size / rate,
            int(np.abs(audio).max()),
        )
        audio = _to_model_rate(audio, rate)

        try:
            with wave.open(LAST_CAPTURE_PATH, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(MODEL_RATE)
                w.writeframes(audio.tobytes())
        except OSError as e:
            logger.warning("Could not save last capture: %s", e)

        recognizer = KaldiRecognizer(self._model, MODEL_RATE, GRAMMAR)
        final_text = ""
        step = 4000  # samples per chunk fed to Vosk
        for i in range(0, len(audio), step):
            if recognizer.AcceptWaveform(audio[i : i + step].tobytes()):
                text = json.loads(recognizer.Result()).get("text")
                if text:
                    final_text = (final_text + " " + text).strip()

        tail = json.loads(recognizer.FinalResult()).get("text")
        if tail:
            final_text = (final_text + " " + tail).strip()

        logger.info("Transcribed: '%s'", final_text)
        return final_text


_engine = None
_engine_lock = threading.Lock()


def get_speech_engine() -> SpeechEngine:
    """Lazily creates a single shared SpeechEngine (loading the model is slow,
    so we only want to do it once per process, not on every request)."""
    global _engine
    with _engine_lock:
        if _engine is None:
            _engine = SpeechEngine()
        return _engine
'''

p = path_of("speech_engine.py")
if "def start_recording" in open(p, encoding="utf-8").read():
    print("already  speech_engine.py")
else:
    save(p, NEW_SPEECH_ENGINE)
    print("patched  speech_engine.py")

# ---------------------------------------------------------------- app.py
NEW_ROUTES = r'''# ---------------------------------------------------------------------------
# Hold-to-talk: the button press starts the mic, the release stops it and
# returns the transcript.
# ---------------------------------------------------------------------------
import threading as _threading

_warm_started = False


@app.before_request
def _warm_up_speech_model():
    """Load the Vosk model in the background on the first request so the first
    button press doesn't have to wait for it."""
    global _warm_started
    if _warm_started:
        return
    _warm_started = True

    def _load():
        try:
            from speech_engine import get_speech_engine

            get_speech_engine()
        except Exception:
            logger.exception("Speech engine warm-up failed")

    _threading.Thread(target=_load, daemon=True).start()


def _speech_engine_or_error():
    try:
        from speech_engine import get_speech_engine

        return get_speech_engine(), None
    except FileNotFoundError as e:
        return None, (jsonify({"ok": False, "error": str(e)}), 500)
    except OSError as e:
        msg = (
            "Audio input library not available on this machine "
            f"({e}). On Linux/Raspberry Pi OS, install it with: "
            "sudo apt install -y portaudio19-dev"
        )
        return None, (jsonify({"ok": False, "error": msg}), 500)


@app.route("/api/record/start", methods=["POST"])
def api_record_start():
    engine, err = _speech_engine_or_error()
    if err:
        return err
    try:
        engine.start_recording()
    except Exception as e:
        logger.exception("Could not start recording")
        return jsonify({"ok": False, "error": f"Microphone error: {e}"}), 500
    return jsonify({"ok": True})


@app.route("/api/record/stop", methods=["POST"])
def api_record_stop():
    engine, err = _speech_engine_or_error()
    if err:
        return err
    try:
        transcript = engine.stop_and_transcribe()
    except Exception as e:
        logger.exception("Voice capture/transcription failed")
        return jsonify({"ok": False, "error": f"Microphone/transcription error: {e}"}), 500

    if not transcript:
        return jsonify({"ok": False, "transcript": "", "error": "No speech detected. Try again."})

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


'''

p = path_of("app.py")
text = open(p, encoding="utf-8").read()
if "/api/record/start" in text:
    print("already  app.py")
else:
    anchor = 'if __name__ == "__main__":'
    if anchor not in text:
        sys.exit("ERROR: couldn't find the __main__ block in app.py")
    save(p, text.replace(anchor, NEW_ROUTES + anchor, 1))
    print("patched  app.py")

# ---------------------------------------------------------------- static/js/app.js
NEW_JS = r'''let isBusy = false; // server is transcribing
let isRecording = false; // button is currently held
let startRequest = null; // promise for the /api/record/start call

function showResult(data) {
  // Let the user hear exactly what the mic captured (cache-busted).
  lastAudio.src = `/api/last-audio?t=${Date.now()}`;
  lastAudio.onloadeddata = () => { lastAudio.hidden = false; };
  lastAudio.onerror = () => { lastAudio.hidden = true; };

  transcriptText.textContent = data.transcript ? `"${data.transcript}"` : "(nothing heard)";

  if (data.ok) {
    setStatus("ok", `Applied: ${data.command.action.toUpperCase()} LED ${data.command.leds.join(", ")}`);
    renderLeds(data.leds);
  } else {
    setStatus("error", data.error || "Command not recognized");
  }
}

// Button pressed: tell the server to start recording right now.
function beginRecording(e) {
  if (isRecording || isBusy) return;
  isRecording = true;
  talkBtn.classList.add("recording");
  setStatus("listening", "Recording… release when done");
  transcriptText.textContent = "…";
  try { talkBtn.setPointerCapture(e.pointerId); } catch (_) {}

  startRequest = fetch("/api/record/start", { method: "POST" })
    .then((r) => r.json())
    .catch(() => ({ ok: false, error: "Request failed — is the server running?" }));
}

// Button released: stop recording and get the transcript.
async function endRecording() {
  if (!isRecording) return;
  isRecording = false;
  isBusy = true;
  talkBtn.classList.remove("recording");
  setStatus("processing", "Transcribing…");

  try {
    const started = await startRequest; // make sure start finished first
    if (!started.ok) {
      transcriptText.textContent = "—";
      setStatus("error", started.error || "Could not start recording");
      return;
    }
    const res = await fetch("/api/record/stop", { method: "POST" });
    showResult(await res.json());
  } catch (err) {
    setStatus("error", "Request failed — is the server running?");
  } finally {
    isBusy = false;
  }
}

// Push-to-talk: hold to record, release to process.
// Pointer events so it works for mouse, touch, and pen alike.
talkBtn.addEventListener("pointerdown", beginRecording);
talkBtn.addEventListener("pointerup", endRecording);
talkBtn.addEventListener("pointercancel", endRecording);

'''

p = path_of("static", "js", "app.js")
text = open(p, encoding="utf-8").read()
if "/api/record/start" in text:
    print("already  static/js/app.js")
else:
    m = re.search(r"let isBusy = false;.*?(?=// Manual per-LED buttons)", text, re.S)
    if not m:
        sys.exit("ERROR: couldn't find the push-to-talk section in static/js/app.js")
    save(p, text[: m.start()] + NEW_JS + text[m.end():])
    print("patched  static/js/app.js")

print("\nDone. Restart the server (Ctrl+C, then: python app.py) and hard-refresh the browser (Ctrl+Shift+R).")
print("Now: HOLD the button, speak, then RELEASE.")