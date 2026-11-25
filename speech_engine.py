"""
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
# "LED" is usually SAID as letters (L-E-D), which Vosk hears as "l e d" /
# "el e d" / "el ee dee", or as look-alike words (led, lead, let, red...).
# All of those variants must be allowed here, or Vosk outputs [unk].
GRAMMAR = json.dumps(
    [
        "turn switch on off all and",
        "led leds lead leads let red ed lid lit lad",
        "l el e ee d dee",
        "one won two to too three tree free",
        "[unk]",
    ]
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