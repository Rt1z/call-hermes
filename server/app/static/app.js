import { createAuthClient } from "./auth.js";
import { createClientLogger } from "./client-log.js";
import { createCallController } from "./rtc.js";
import { createUi } from "./ui.js";

const TTS_VOICE_GROUPS = [
  {
    label: "女声",
    voices: [
      ["Cherry", "芊悦", "阳光积极、亲切自然的小姐姐音色。"],
      ["Serena", "苏瑶", "温柔自然的小姐姐音色。"],
      ["Jennifer", "詹妮弗", "品牌级、电影质感般的美语女声。"],
      ["Maia", "四月", "知性与温柔兼具的女声。"],
      ["Sohee", "素熙", "温柔开朗、情绪丰富的韩系女声。"],
      ["Sunny", "四川-晴儿", "甜美亲切的四川女声。"],
    ],
  },
  {
    label: "男声",
    voices: [
      ["Ethan", "晨煦", "标准普通话，带部分北方口音，阳光温暖、有活力。"],
      ["Nofish", "不吃鱼", "不会翘舌音的设计师男声。"],
      ["Ryan", "甜茶", "节奏感强、戏感鲜明、真实有张力的男声。"],
      ["Bodega", "博德加", "热情的西班牙大叔音色。"],
      ["Andre", "安德雷", "声音磁性、自然舒服、沉稳的男声。"],
      ["Radio Gol", "拉迪奥·戈尔", "足球解说风格，情绪饱满、有现场感。"],
      ["Dylan", "北京-晓东", "胡同里长大的北京小伙儿音色。"],
      ["Rocky", "粤语-阿强", "幽默风趣的粤语男声，适合轻松陪聊。"],
    ],
  },
];

const config = {
  bridgeUrl: localStorage.getItem("hermes.bridgeUrl") || window.location.origin,
  sharedSecret: localStorage.getItem("hermes.sharedSecret") || "",
  ttsVoice: localStorage.getItem("hermes.ttsVoice") || "Cherry",
  ttsSpeechRate: readSpeechRate(),
  vadSilenceMs: readVadSilenceMs(),
  debugMode: localStorage.getItem("hermes.debugMode") === "true",
  audioInputDeviceId: localStorage.getItem("hermes.audioInputDeviceId") || "",
};

const ui = createUi();
const clientLogger = createClientLogger({ ui, getBridgeUrl: () => config.bridgeUrl });
populateVoiceOptions();
syncSettingsForm();

if ("serviceWorker" in navigator) {
  disableServiceWorker();
}

window.addEventListener("error", (event) => {
  clientLogger.error("window error", {
    message: event.message,
    filename: event.filename,
    lineno: event.lineno,
    colno: event.colno,
  });
});

window.addEventListener("unhandledrejection", (event) => {
  clientLogger.error("unhandled rejection", errorDetails(event.reason));
});

let auth = createAuthClient(config, clientLogger);
let fallback = createFallbackController({ auth, ui, logger: clientLogger });
let call = createCallController({
  auth,
  ui,
  logger: clientLogger,
  getTtsOptions,
  isDebugMode,
  getAudioInputDeviceId,
  onAudioInputSelected,
  onFallback: activateFallback,
});

ui.settingsButton.addEventListener("click", () => {
  openSettings();
});

ui.reconnectButton.addEventListener("click", async () => {
  clientLogger.info("reconnect button tapped", { calling: call.isCalling });
  ui.reconnectButton.disabled = true;
  await call.reconnect();
});

ui.deviceStatusEl.addEventListener("click", async () => {
  await openAudioDevicePicker();
});

ui.audioDeviceList.addEventListener("click", async (event) => {
  const option = event.target.closest(".device-option");
  if (!option) {
    return;
  }
  const deviceId = option.dataset.deviceId || "";
  option.disabled = true;
  const switched = await call.switchAudioInput(deviceId);
  option.disabled = false;
  if (switched) {
    ui.audioDeviceDialog.close();
  }
});

ui.saveSettingsButton.addEventListener("click", async () => {
  config.bridgeUrl = ui.bridgeUrlInput.value.replace(/\/$/, "");
  config.sharedSecret = ui.secretInput.value;
  config.ttsVoice = ui.ttsVoiceSelect.value;
  config.ttsSpeechRate = normalizeSpeechRate(ui.speechRateInput.value);
  config.vadSilenceMs = normalizeVadSilenceMs(ui.vadSilenceInput.value);
  config.debugMode = ui.debugModeInput.checked;
  localStorage.setItem("hermes.bridgeUrl", config.bridgeUrl);
  localStorage.setItem("hermes.sharedSecret", config.sharedSecret);
  localStorage.setItem("hermes.ttsVoice", config.ttsVoice);
  localStorage.setItem("hermes.ttsSpeechRate", String(config.ttsSpeechRate));
  localStorage.setItem("hermes.vadSilenceMs", String(config.vadSilenceMs));
  localStorage.setItem("hermes.debugMode", String(config.debugMode));
  auth.clearToken();
  clientLogger.info("settings saved", {
    bridgeUrl: config.bridgeUrl,
    ttsVoice: config.ttsVoice,
    ttsSpeechRate: config.ttsSpeechRate,
    debugMode: config.debugMode,
  });
  auth = createAuthClient(config, clientLogger);
  if (call.isCalling) {
    await call.endCall("Ready");
  }
  fallback.deactivate();
  fallback = createFallbackController({ auth, ui, logger: clientLogger });
  call = createCallController({
    auth,
    ui,
    logger: clientLogger,
    getTtsOptions,
    isDebugMode,
    getAudioInputDeviceId,
    onAudioInputSelected,
    onFallback: activateFallback,
  });
  ui.settingsDialog.close();
  ui.setStatus("Ready");
});

ui.speechRateInput.addEventListener("input", () => {
  ui.setSpeechRateValue(ui.speechRateInput.value);
});

ui.vadSilenceInput.addEventListener("input", () => {
  ui.setVadSilenceValue(ui.vadSilenceInput.value);
});

ui.ttsVoiceSelect.addEventListener("change", () => {
  updateVoiceDescription();
});

ui.recordButton.addEventListener("click", async () => {
  clientLogger.info("record button tapped", { fallbackActive: fallback.isActive, calling: call.isCalling });
  if (fallback.isActive) {
    fallback.deactivate();
    ui.setStatus("Ready");
    return;
  }
  if (call.isCalling) {
    await call.endCall("Ready");
    return;
  }
  if (!config.sharedSecret) {
    openSettings();
    return;
  }
  await call.startCall();
});

ui.micButton.addEventListener("click", () => {
  clientLogger.info("mic button tapped", { fallbackActive: fallback.isActive, calling: call.isCalling });
  if (fallback.isActive) {
    fallback.toggleRecording();
    return;
  }
  if (call.isCalling) {
    call.toggleMicrophone();
  }
});

ui.debugForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const sent = call.sendDebugText(ui.debugInput.value);
  if (sent) {
    ui.debugInput.value = "";
    ui.setStatus("Thinking");
  }
});

ui.debugEndButton.addEventListener("click", async () => {
  if (call.isCalling) {
    await call.endCall("Ready");
  }
});

ui.replyAudio.addEventListener("playing", () => {
  if (call.isCalling || fallback.isActive) {
    ui.setStatus("Playing");
  }
});

function activateFallback(reason) {
  clientLogger.warn("fallback activated", { reason });
  fallback.activate(reason);
}

async function disableServiceWorker() {
  try {
    const registrations = await navigator.serviceWorker.getRegistrations();
    await Promise.all(registrations.map((registration) => registration.unregister()));
    if (window.caches?.keys) {
      const keys = await caches.keys();
      await Promise.all(keys.map((key) => caches.delete(key)));
    }
    clientLogger.info("service worker disabled", { registrations: registrations.length });
  } catch (error) {
    clientLogger.warn("service worker cleanup failed", errorDetails(error));
  }
}

function populateVoiceOptions() {
  ui.ttsVoiceSelect.replaceChildren(
    ...TTS_VOICE_GROUPS.map((group) => {
      const optgroup = document.createElement("optgroup");
      optgroup.label = group.label;
      group.voices.forEach(([value, name, description]) => {
        const option = document.createElement("option");
        option.value = value;
        option.textContent = `${value} - ${name}`;
        option.dataset.description = description;
        optgroup.append(option);
      });
      return optgroup;
    }),
  );
}

function openSettings() {
  syncSettingsForm();
  ui.settingsDialog.showModal();
  requestAnimationFrame(() => {
    ui.settingsFocusTarget.focus({ preventScroll: true });
  });
}

function syncSettingsForm() {
  ui.bridgeUrlInput.value = config.bridgeUrl;
  ui.secretInput.value = config.sharedSecret;
  if (!findVoice(config.ttsVoice)) {
    config.ttsVoice = "Cherry";
  }
  ui.ttsVoiceSelect.value = config.ttsVoice;
  updateVoiceDescription();
  ui.speechRateInput.value = String(config.ttsSpeechRate);
  ui.setSpeechRateValue(config.ttsSpeechRate);
  ui.vadSilenceInput.value = String(config.vadSilenceMs);
  ui.setVadSilenceValue(config.vadSilenceMs);
  ui.debugModeInput.checked = config.debugMode;
}

function getTtsOptions() {
  return {
    ttsVoice: config.ttsVoice,
    ttsSpeechRate: config.ttsSpeechRate,
    vadSilenceMs: config.vadSilenceMs,
  };
}

function isDebugMode() {
  return config.debugMode;
}

function getAudioInputDeviceId() {
  return config.audioInputDeviceId;
}

function onAudioInputSelected(deviceId) {
  config.audioInputDeviceId = deviceId;
  localStorage.setItem("hermes.audioInputDeviceId", deviceId);
}

async function openAudioDevicePicker() {
  ui.deviceStatusEl.disabled = true;
  ui.audioDeviceHelp.textContent = "Loading microphones...";
  ui.setAudioDevices([], config.audioInputDeviceId);
  ui.audioDeviceDialog.showModal();
  try {
    const devices = await call.listAudioInputs({ requestPermission: true });
    ui.setAudioDevices(devices, config.audioInputDeviceId);
    ui.audioDeviceHelp.textContent = devices.length > 1
      ? "Choose a microphone. Speaker output still follows the iOS audio route."
      : "Only one microphone is exposed by Safari. Connect the Bluetooth device in iOS Control Center, then reopen this list.";
  } catch (error) {
    ui.setStatus("Mic error");
    ui.setDebug(error.message || "Unable to list microphones.");
    ui.audioDeviceHelp.textContent = "Unable to load microphones. Open Settings to view Debug information.";
    clientLogger.error("audio device picker failed", errorDetails(error));
  } finally {
    ui.deviceStatusEl.disabled = false;
  }
}

function readSpeechRate() {
  return normalizeSpeechRate(localStorage.getItem("hermes.ttsSpeechRate") || "1");
}

function normalizeSpeechRate(value) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) {
    return 1;
  }
  return Math.min(2, Math.max(0.5, Number(parsed.toFixed(2))));
}

function readVadSilenceMs() {
  return normalizeVadSilenceMs(localStorage.getItem("hermes.vadSilenceMs") || "2500");
}

function normalizeVadSilenceMs(value) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) {
    return 2500;
  }
  return Math.min(5000, Math.max(500, Math.round(parsed / 100) * 100));
}

function updateVoiceDescription() {
  const voice = findVoice(ui.ttsVoiceSelect.value);
  ui.ttsVoiceDescription.textContent = voice ? voice.description : "";
}

function findVoice(value) {
  for (const group of TTS_VOICE_GROUPS) {
    const match = group.voices.find(([voiceValue]) => voiceValue === value);
    if (match) {
      return {
        value: match[0],
        name: match[1],
        description: match[2],
      };
    }
  }
  return null;
}

function createFallbackController({ auth, ui, logger = null }) {
  const state = {
    active: false,
    recording: false,
    mediaRecorder: null,
    stream: null,
    meter: null,
    chunks: [],
  };

  return {
    get isActive() {
      return state.active;
    },
    activate(reason = "HTTPS fallback active.") {
      logger?.warn("fallback mode active", { reason });
      state.active = true;
      ui.setFallbackMode(true);
      ui.setFallbackRecording(false);
      ui.setStatus("Fallback ready");
      ui.setDebug(`${reason} Tap the microphone once to record, then tap again to send.`);
    },
    deactivate,
    toggleRecording,
  };

  async function toggleRecording() {
    if (!state.active) {
      return;
    }
    if (state.recording) {
      stopRecording();
      return;
    }
    await startRecording();
  }

  async function startRecording() {
    try {
      logger?.info("fallback getUserMedia start");
      if (!window.MediaRecorder) {
        throw new Error("This browser does not support MediaRecorder fallback.");
      }
      state.stream = await openFallbackAudioInput();
      state.meter = startVoiceMeter(state.stream, ui);
      const mimeType = preferredRecorderMimeType();
      logger?.info("fallback recorder start", { mimeType: mimeType || "browser-default" });
      state.mediaRecorder = new MediaRecorder(state.stream, mimeType ? { mimeType } : undefined);
      state.chunks = [];
      state.mediaRecorder.ondataavailable = (event) => {
        if (event.data?.size) {
          state.chunks.push(event.data);
        }
      };
      state.mediaRecorder.onstop = () => {
        const blob = new Blob(state.chunks, { type: state.mediaRecorder?.mimeType || "audio/webm" });
        state.chunks = [];
        stopStream();
        sendFallbackTurn(blob);
      };
      state.mediaRecorder.start();
      state.recording = true;
      ui.setFallbackRecording(true);
      ui.setStatus("Recording");
      ui.setDebug("Fallback recording...");
    } catch (error) {
      ui.setStatus("Mic error");
      ui.setDebug(error.message || "Microphone permission failed.");
      logger?.error("fallback recorder failed", errorDetails(error));
    }
  }

  function stopRecording() {
    if (!state.mediaRecorder || state.mediaRecorder.state === "inactive") {
      return;
    }
    state.recording = false;
    ui.setFallbackRecording(false);
    ui.setStatus("Sending");
    state.mediaRecorder.stop();
  }

  async function openFallbackAudioInput() {
    const baseConstraints = {
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
      channelCount: 1,
    };
    try {
      return await navigator.mediaDevices.getUserMedia({
        audio: {
          ...baseConstraints,
          ...(config.audioInputDeviceId
            ? { deviceId: { exact: config.audioInputDeviceId } }
            : {}),
        },
      });
    } catch (error) {
      if (!config.audioInputDeviceId || !["NotFoundError", "OverconstrainedError"].includes(error?.name)) {
        throw error;
      }
      logger?.warn("saved fallback audio input unavailable; using browser default", {
        deviceId: config.audioInputDeviceId,
      });
      onAudioInputSelected("");
      return navigator.mediaDevices.getUserMedia({ audio: baseConstraints });
    }
  }

  async function sendFallbackTurn(blob) {
    if (!blob.size) {
      ui.setStatus("No audio");
      return;
    }
    try {
      ui.setStatus("Thinking");
      logger?.info("fallback turn upload start", { bytes: blob.size, mimeType: blob.type });
      const result = await auth.sendPwaTurn(blob);
      const turnId = result.turn_id || `fallback-${Date.now()}`;
      ui.setTurnUser(turnId, result.transcript || "-");
      ui.setTurnAnswer(turnId, result.answer || "-");
      ui.setTurnComplete(turnId);
      await playFallbackAudio(result);
      ui.setStatus("Fallback ready");
      ui.setDebug(`Fallback turn ${result.turn_id || ""}`.trim());
    } catch (error) {
      ui.setStatus("Fallback error");
      ui.setDebug(error.message || "Fallback turn failed.");
      logger?.error("fallback turn failed", errorDetails(error));
    }
  }

  async function playFallbackAudio(result) {
    if (!result.audio_base64) {
      return;
    }
    const audioBytes = Uint8Array.from(atob(result.audio_base64), (char) => char.charCodeAt(0));
    const audioBlob = new Blob([audioBytes], { type: result.audio_mime || "audio/wav" });
    const url = URL.createObjectURL(audioBlob);
    ui.replyAudio.srcObject = null;
    ui.replyAudio.src = url;
    try {
      await ui.replyAudio.play();
    } finally {
      window.setTimeout(() => URL.revokeObjectURL(url), 30000);
    }
  }

  function deactivate() {
    if (state.recording) {
      state.mediaRecorder?.stop();
    }
    stopStream();
    state.active = false;
    state.recording = false;
    state.mediaRecorder = null;
    state.chunks = [];
    ui.setFallbackMode(false);
    ui.setFallbackRecording(false);
  }

  function stopStream() {
    stopVoiceMeter(state.meter, ui);
    state.meter = null;
    if (state.stream) {
      state.stream.getTracks().forEach((track) => track.stop());
      state.stream = null;
    }
  }
}

function errorDetails(error) {
  if (!error) {
    return {};
  }
  if (error instanceof Error) {
    return {
      name: error.name,
      message: error.message,
      stack: error.stack,
    };
  }
  if (typeof error === "object") {
    return error;
  }
  return { message: String(error) };
}

function preferredRecorderMimeType() {
  const options = [
    "audio/mp4;codecs=mp4a.40.2",
    "audio/mp4",
    "audio/webm;codecs=opus",
    "audio/webm",
  ];
  return options.find((type) => MediaRecorder.isTypeSupported?.(type)) || "";
}

function startVoiceMeter(stream, ui) {
  const AudioContextClass = window.AudioContext || window.webkitAudioContext;
  if (!AudioContextClass) {
    return null;
  }
  const context = new AudioContextClass();
  const source = context.createMediaStreamSource(stream);
  const analyser = context.createAnalyser();
  analyser.fftSize = 512;
  source.connect(analyser);
  const samples = new Uint8Array(analyser.fftSize);
  let animationFrame = 0;
  let lastActiveAt = 0;

  const tick = () => {
    analyser.getByteTimeDomainData(samples);
    let sum = 0;
    for (const value of samples) {
      const centered = value - 128;
      sum += centered * centered;
    }
    const rms = Math.sqrt(sum / samples.length) / 128;
    const now = performance.now();
    if (rms > 0.018) {
      lastActiveAt = now;
    }
    ui.setVoiceActive(now - lastActiveAt < 350);
    animationFrame = requestAnimationFrame(tick);
  };
  tick();

  return { context, source, animationFrame };
}

function stopVoiceMeter(meter, ui) {
  if (!meter) {
    ui.setVoiceActive(false);
    return;
  }
  cancelAnimationFrame(meter.animationFrame);
  meter.source.disconnect();
  meter.context.close().catch(() => {});
  ui.setVoiceActive(false);
}
