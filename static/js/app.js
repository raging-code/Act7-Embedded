// app.js — Voice LED Control frontend
// Push-to-talk button records server-side (the mic is plugged into
// whatever machine runs app.py — the Pi or your PC), so the browser
// just tells the server "start listening" and waits for the result.

const talkBtn = document.getElementById("talk-btn");
const statusLine = document.getElementById("status-line");
const statusText = document.getElementById("status-text");
const transcriptText = document.getElementById("transcript-text");
const lastAudio = document.getElementById("last-audio");
const connStatus = document.getElementById("conn-status");

function setStatus(state, text) {
  statusLine.dataset.state = state;
  statusText.textContent = text;
}

function renderLeds(ledStates) {
  // ledStates: {"1": true, "2": false, "3": true} (keys may be strings from JSON)
  Object.entries(ledStates).forEach(([ledId, isOn]) => {
    const bulb = document.getElementById(`bulb-${ledId}`);
    const slot = document.querySelector(`.led-slot[data-led="${ledId}"]`);
    if (bulb) bulb.classList.toggle("is-on", isOn);
    if (slot) slot.classList.toggle("is-on", isOn);
  });
}

async function fetchStatus() {
  try {
    const res = await fetch("/api/status");
    const data = await res.json();

    // Let the user hear exactly what the mic captured (cache-busted).
    lastAudio.src = `/api/last-audio?t=${Date.now()}`;
    lastAudio.onloadeddata = () => { lastAudio.hidden = false; };
    lastAudio.onerror = () => { lastAudio.hidden = true; };
    renderLeds(data.leds);
    connStatus.textContent = "• connected";
  } catch (err) {
    connStatus.textContent = "• disconnected";
  }
}

let isBusy = false; // server is transcribing
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

// Manual per-LED buttons (direct test controls, bypassing voice)
document.querySelectorAll(".mini-btn").forEach((btn) => {
  btn.addEventListener("click", async () => {
    const led = btn.dataset.led;
    const state = btn.dataset.state;
    await fetch(`/api/led/${led}/${state}`, { method: "POST" });
    fetchStatus();
  });
});

// Bulk ALL ON / ALL OFF
document.getElementById("all-on").addEventListener("click", async () => {
  for (const led of [1, 2, 3]) {
    await fetch(`/api/led/${led}/on`, { method: "POST" });
  }
  fetchStatus();
});

document.getElementById("all-off").addEventListener("click", async () => {
  for (const led of [1, 2, 3]) {
    await fetch(`/api/led/${led}/off`, { method: "POST" });
  }
  fetchStatus();
});

// Initial load + light polling to stay in sync if LEDs are changed
// from elsewhere (e.g. another browser tab open on the same Pi).
fetchStatus();
setInterval(fetchStatus, 4000);