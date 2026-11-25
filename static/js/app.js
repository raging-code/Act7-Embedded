// app.js — Voice LED Control frontend
// Push-to-talk button records server-side (the mic is plugged into
// whatever machine runs app.py — the Pi or your PC), so the browser
// just tells the server "start listening" and waits for the result.

const talkBtn = document.getElementById("talk-btn");
const statusLine = document.getElementById("status-line");
const statusText = document.getElementById("status-text");
const transcriptText = document.getElementById("transcript-text");
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
    renderLeds(data.leds);
    connStatus.textContent = "• connected";
  } catch (err) {
    connStatus.textContent = "• disconnected";
  }
}

async function sendVoiceCommand() {
  setStatus("processing", "Listening & transcribing…");
  transcriptText.textContent = "…";

  try {
    const res = await fetch("/api/voice-command", { method: "POST" });
    const data = await res.json();

    transcriptText.textContent = data.transcript ? `"${data.transcript}"` : "(nothing heard)";

    if (data.ok) {
      setStatus("ok", `Applied: ${data.command.action.toUpperCase()} LED ${data.command.leds.join(", ")}`);
      renderLeds(data.leds);
    } else {
      setStatus("error", data.error || "Command not recognized");
    }
  } catch (err) {
    setStatus("error", "Request failed — is the server running?");
  }
}

// Push-to-talk: press and hold the button, release to process.
// Using pointer events so it works for mouse, touch, and pen alike.
let isRecording = false;

talkBtn.addEventListener("pointerdown", (e) => {
  if (isRecording) return;
  isRecording = true;
  talkBtn.classList.add("recording");
  setStatus("listening", "Recording…");
  talkBtn.setPointerCapture(e.pointerId);
});

talkBtn.addEventListener("pointerup", () => {
  if (!isRecording) return;
  isRecording = false;
  talkBtn.classList.remove("recording");
  sendVoiceCommand();
});

talkBtn.addEventListener("pointercancel", () => {
  isRecording = false;
  talkBtn.classList.remove("recording");
  setStatus("idle", "Ready");
});

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
