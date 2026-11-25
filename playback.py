#!/usr/bin/env python3
"""
Adds an in-page audio player ("hear what the mic captured") to Act7-Embedded.

Usage (from the project folder):
    python3 patch_playback.py            # patches the current folder
    python3 patch_playback.py /path/to/Act7-Embedded

Safe to run twice. Originals are saved as <file>.bak.
"""
import os
import shutil
import sys

root = sys.argv[1] if len(sys.argv) > 1 else "."


def patch(rel, steps):
    path = os.path.join(root, rel)
    if not os.path.isfile(path):
        sys.exit(f"ERROR: {path} not found. Run this from the Act7-Embedded folder.")
    text = open(path, encoding="utf-8").read()
    original = text
    for marker, old, new in steps:
        if marker in text:
            continue  # already applied
        if old not in text:
            sys.exit(f"ERROR: couldn't find the expected code in {rel} for: {marker!r}")
        text = text.replace(old, new, 1)
    if text != original:
        shutil.copyfile(path, path + ".bak")
        open(path, "w", encoding="utf-8").write(text)
        print(f"patched  {rel}")
    else:
        print(f"already  {rel}")


# ---------------------------------------------------------------- speech_engine.py
patch("speech_engine.py", [
    ("import wave", "import threading\nimport time\n",
     "import tempfile\nimport threading\nimport time\nimport wave\n"),
    ("LAST_CAPTURE_PATH =", "MIC_DEVICE = int(",
     '# Last capture (what Vosk heard) is saved here so the web page can play it.\n'
     'LAST_CAPTURE_PATH = os.path.join(tempfile.gettempdir(), "act7_last_capture.wav")\n\n'
     "MIC_DEVICE = int("),
    ("w.setframerate(MODEL_RATE)", "        audio = _to_model_rate(audio, rate)\n",
     "        audio = _to_model_rate(audio, rate)\n\n"
     "        try:\n"
     "            with wave.open(LAST_CAPTURE_PATH, \"wb\") as w:\n"
     "                w.setnchannels(1)\n"
     "                w.setsampwidth(2)\n"
     "                w.setframerate(MODEL_RATE)\n"
     "                w.writeframes(audio.tobytes())\n"
     "        except OSError as e:\n"
     "            logger.warning(\"Could not save last capture: %s\", e)\n"),
])

# ---------------------------------------------------------------- app.py
patch("app.py", [
    ("render_template, send_file",
     "from flask import Flask, jsonify, render_template",
     "from flask import Flask, jsonify, render_template, send_file"),
    ('"/api/last-audio"', '@app.route("/api/voice-command", methods=["POST"])',
     '@app.route("/api/last-audio")\n'
     "def api_last_audio():\n"
     '    """The most recent mic capture, so you can hear what the app heard."""\n'
     "    try:\n"
     "        from speech_engine import LAST_CAPTURE_PATH\n"
     '        resp = send_file(LAST_CAPTURE_PATH, mimetype="audio/wav", max_age=0)\n'
     "    except (ImportError, OSError, FileNotFoundError):\n"
     '        return jsonify({"ok": False, "error": "No capture yet."}), 404\n'
     '    resp.headers["Cache-Control"] = "no-store"\n'
     "    return resp\n\n\n"
     '@app.route("/api/voice-command", methods=["POST"])'),
])

# ---------------------------------------------------------------- templates/index.html
patch(os.path.join("templates", "index.html"), [
    ('id="last-audio"',
     '<div id="transcript-text" class="transcript-text">&mdash;</div>',
     '<div id="transcript-text" class="transcript-text">&mdash;</div>\n'
     '        <audio id="last-audio" class="last-audio" controls hidden></audio>'),
])

# ---------------------------------------------------------------- static/js/app.js
patch(os.path.join("static", "js", "app.js"), [
    ("const lastAudio",
     'const transcriptText = document.getElementById("transcript-text");',
     'const transcriptText = document.getElementById("transcript-text");\n'
     'const lastAudio = document.getElementById("last-audio");'),
    ("/api/last-audio?t=",
     "    const data = await res.json();\n",
     "    const data = await res.json();\n\n"
     "    // Let the user hear exactly what the mic captured (cache-busted).\n"
     "    lastAudio.src = `/api/last-audio?t=${Date.now()}`;\n"
     "    lastAudio.onloadeddata = () => { lastAudio.hidden = false; };\n"
     "    lastAudio.onerror = () => { lastAudio.hidden = true; };\n"),
])

# ---------------------------------------------------------------- static/css/style.css
css = os.path.join(root, "static", "css", "style.css")
if not os.path.isfile(css):
    sys.exit(f"ERROR: {css} not found.")
if ".last-audio" in open(css, encoding="utf-8").read():
    print("already  static/css/style.css")
else:
    shutil.copyfile(css, css + ".bak")
    with open(css, "a", encoding="utf-8") as f:
        f.write("\n.last-audio {\n  display: block;\n  width: 100%;\n  margin-top: 10px;\n}\n")
    print("patched  static/css/style.css")

print("\nDone. Restart the server (Ctrl+C, then: python app.py) and hard-refresh the browser.")