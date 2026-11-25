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

Setup (one-time, needs internet the FIRST time only):
    1. pip install vosk sounddevice
    2. Download & unzip the model into ./models/:
       https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip
       -> unzip so you have: models/vosk-model-small-en-us-0.15/
"""

import json
import logging
import os
import threading
import time

import numpy as np
import sounddevice as sd
from vosk import KaldiRecognizer, Model

logger = logging.getLogger("speech_engine")

MODEL_DIR = os.path.join(os.path.dirname(__file__), "models", "vosk-model-small-en-us-0.15")
MODEL_RATE = 16000  # Vosk's small English model expects 16 kHz audio
RECORD_SECONDS = 4  # length of each push-to-talk capture

# Restrict Vosk to the words our commands use. This makes recognition far
# more accurate (no more "pain on anyone") because it can't pick other words.
GRAMMAR = json.dumps(
    ["turn switch on off led leds all and one two three", "[unk]"]
)

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

    def listen_and_transcribe(self, seconds: int = RECORD_SECONDS) -> str:
        """
        Records `seconds` of audio from the microphone and returns the
        transcribed text (lowercase, empty string if nothing understood).
        """
        if not _capture_lock.acquire(blocking=False):
            raise RuntimeError("Already listening - wait for the current capture to finish.")
        try:
            return self._listen(seconds)
        finally:
            _capture_lock.release()

    def _listen(self, seconds: int) -> str:
        chunks = []
        lock = threading.Lock()

        def _callback(indata, frames, time_info, status):
            if status:
                logger.warning("Audio input status: %s", status)
            with lock:
                chunks.append(bytes(indata))

        stream, rate, channels = self._open_stream(_callback)
        with stream:
            logger.info("Listening for %s seconds...", seconds)
            time.sleep(seconds)

        with lock:
            raw = b"".join(chunks)
        audio = np.frombuffer(raw, dtype=np.int16)
        if channels > 1:
            audio = audio[: len(audio) - (len(audio) % channels)]
            audio = audio.reshape(-1, channels).mean(axis=1).astype(np.int16)
        if audio.size == 0:
            return ""

        logger.info("Captured %.1fs, peak level %d / 32767", audio.size / rate, int(np.abs(audio).max()))
        audio = _to_model_rate(audio, rate)

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


_capture_lock = threading.Lock()  # only one mic capture at a time
_engine = None


def get_speech_engine() -> SpeechEngine:
    """Lazily creates a single shared SpeechEngine (loading the model is slow,
    so we only want to do it once per process, not on every request)."""
    global _engine
    if _engine is None:
        _engine = SpeechEngine()
    return _engine