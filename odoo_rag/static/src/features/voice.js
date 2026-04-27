import { dom } from "../dom.js";
import { showToast } from "../ui/toast.js";
import { autoResizeComposer } from "../ui/composer.js";

/** @type {SpeechRecognition | null} */
let recognition = null;
let listening = false;
let speechFinalTail = "";
let speechPrefix = "";

const VOICE_SILENCE_MS = 2000;
let voiceSilenceTimer = null;

function clearVoiceSilenceTimer() {
  if (voiceSilenceTimer !== null) {
    clearTimeout(voiceSilenceTimer);
    voiceSilenceTimer = null;
  }
}

function speechSupported() {
  return !!(window.SpeechRecognition || window.webkitSpeechRecognition);
}

function speechLang() {
  const nav = navigator.language || "";
  return nav.toLowerCase().startsWith("es") ? nav : "es-ES";
}

function setListeningUI(active) {
  listening = active;
  dom.btnMic.classList.toggle("listening", active);
  dom.btnMic.setAttribute("aria-pressed", active ? "true" : "false");
  dom.micLabel.textContent = active ? "Parar" : "Dictar";
  dom.micHint.hidden = !active;
  dom.micHint.textContent = active ? "Escuchando… al callar un momento se envía la pregunta." : "";
}

function scheduleVoiceAutoSubmit() {
  clearVoiceSilenceTimer();
  voiceSilenceTimer = setTimeout(() => {
    voiceSilenceTimer = null;
    if (!listening) return;
    const text = dom.inputEl.value.trim();
    if (!text) return;
    stopDictation(true);
    dom.composer.requestSubmit();
  }, VOICE_SILENCE_MS);
}

function attachSpeechHandlers(rec) {
  rec.onresult = (event) => {
    let interim = "";
    let finals = "";
    for (let i = event.resultIndex; i < event.results.length; i++) {
      const phrase = event.results[i][0].transcript;
      if (event.results[i].isFinal) finals += phrase;
      else interim += phrase;
    }
    speechFinalTail += finals;
    dom.inputEl.value = speechPrefix + speechFinalTail + interim;
    dom.inputEl.scrollTop = dom.inputEl.scrollHeight;
    autoResizeComposer();
    scheduleVoiceAutoSubmit();
  };

  rec.onerror = (event) => {
    if (event.error === "aborted" || event.error === "no-speech") return;
    showToast(
      event.error === "not-allowed"
        ? "Permiso de micrófono denegado. Revísalo en la barra del navegador."
        : `Micrófono: ${event.error}`,
      true
    );
    stopDictation(true);
  };

  rec.onend = () => {
    if (!listening) return;
    try {
      rec.start();
    } catch {
      /* algunos navegadores ya reinician solos */
    }
  };
}

function startDictation() {
  if (!speechSupported()) {
    showToast("Tu navegador no expone reconocimiento de voz (prueba Chrome o Edge).", true);
    return;
  }
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  recognition = new SR();
  recognition.lang = speechLang();
  recognition.continuous = true;
  recognition.interimResults = true;
  recognition.maxAlternatives = 1;

  speechPrefix = dom.inputEl.value;
  speechFinalTail = "";
  attachSpeechHandlers(recognition);

  try {
    recognition.start();
    setListeningUI(true);
    showToast("Micrófono activo: al dejar de hablar se envía solo.");
  } catch (err) {
    recognition = null;
    showToast(`No se pudo iniciar el micrófono: ${err}`, true);
  }
}

function stopDictation(silent) {
  clearVoiceSilenceTimer();
  listening = false;
  const rec = recognition;
  recognition = null;
  if (rec) {
    try {
      rec.stop();
    } catch {
      /* ignore */
    }
  }
  setListeningUI(false);
  speechFinalTail = "";
  speechPrefix = "";
  if (!silent) dom.inputEl.focus();
}

export function attachVoice() {
  if (!dom.btnMic) return;
  if (!speechSupported()) {
    dom.btnMic.disabled = true;
    dom.btnMic.title = "El reconocimiento de voz solo está en Chrome/Edge y contexto seguro.";
    return;
  }
  dom.btnMic.addEventListener("click", () => {
    if (listening) stopDictation(false);
    else startDictation();
  });
}

