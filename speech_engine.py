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
import queue

import sounddevice as sd
from vosk import KaldiRecognizer, Model

logger = logging.getLogger("speech_engine")

MODEL_DIR = os.path.join(os.path.dirname(__file__), "models", "vosk-model-small-en-us-0.15")
SAMPLE_RATE = 16000
RECORD_SECONDS = 4  # length of each push-to-talk capture


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

    def listen_and_transcribe(self, seconds: int = RECORD_SECONDS) -> str:
        """
        Records `seconds` of audio from the default microphone and
        returns the transcribed text (lowercase, may be empty string
        if nothing was understood).
        """
        recognizer = KaldiRecognizer(self._model, SAMPLE_RATE)
        audio_q: queue.Queue = queue.Queue()

        def _callback(indata, frames, time_info, status):
            if status:
                logger.warning("Audio input status: %s", status)
            audio_q.put(bytes(indata))

        final_text = ""
        with sd.RawInputStream(
            samplerate=SAMPLE_RATE,
            blocksize=8000,
            dtype="int16",
            channels=1,
            callback=_callback,
        ):
            logger.info("Listening for %s seconds...", seconds)
            frames_needed = int(SAMPLE_RATE * seconds / 8000) + 1
            for _ in range(frames_needed):
                try:
                    data = audio_q.get(timeout=seconds + 1)
                except queue.Empty:
                    break
                if recognizer.AcceptWaveform(data):
                    result = json.loads(recognizer.Result())
                    if result.get("text"):
                        final_text = (final_text + " " + result["text"]).strip()

        # Grab whatever partial/final result is left in the recognizer
        tail = json.loads(recognizer.FinalResult())
        if tail.get("text"):
            final_text = (final_text + " " + tail["text"]).strip()

        logger.info("Transcribed: '%s'", final_text)
        return final_text


_engine = None


def get_speech_engine() -> SpeechEngine:
    """Lazily creates a single shared SpeechEngine (loading the model is slow,
    so we only want to do it once per process, not on every request)."""
    global _engine
    if _engine is None:
        _engine = SpeechEngine()
    return _engine
