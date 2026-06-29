import { handleBridgeEvent } from "./events.js";

const CONNECTION_RECOVERY_MS = 5000;
const MAX_RECONNECT_ATTEMPTS = 0;
const NETWORK_STATS_INTERVAL_MS = 2000;
const NETWORK_LOSS_WINDOW_MS = 12000;
const NETWORK_MIN_PACKET_SAMPLE = 50;
const QUALITY_RANK = { unknown: -1, excellent: 0, good: 1, fair: 2, poor: 3, offline: 4 };

export function createCallController({
  auth,
  ui,
  logger = null,
  getTtsOptions,
  isDebugMode,
  getAudioInputDeviceId,
  onAudioInputSelected,
  onFallback,
}) {
  const state = {
    peerConnection: null,
    eventsChannel: null,
    inputStream: null,
    callStartedAt: 0,
    statusTimer: null,
    recoveryTimer: null,
    networkStatsTimer: null,
    networkStatsBusy: false,
    networkCounters: null,
    networkLossSamples: [],
    smoothedNetworkMetrics: null,
    networkQuality: "unknown",
    pendingNetworkQuality: "unknown",
    pendingNetworkSamples: 0,
    requestedPrebufferSeconds: null,
    appliedPrebufferSeconds: null,
    adaptiveBuffer: defaultAdaptiveBufferConfig(),
    reconnectAttempts: 0,
    isReconnecting: false,
    isCalling: false,
    isSpeaking: false,
    isMuted: false,
    connectionTonePlayed: false,
    audioContext: null,
    meter: null,
    currentTurnId: null,
    currentTranscript: "",
    currentAnswer: "",
    turnAnswers: new Map(),
  };

  if (navigator.mediaDevices?.addEventListener) {
    navigator.mediaDevices.addEventListener("devicechange", () => {
      if (state.isCalling) {
        updateDeviceStatus();
      }
    });
  }

  async function startCall(options = {}) {
    try {
      logger?.info("call start", { preserveConversation: Boolean(options.preserveConversation) });
      ui.setStatus("Connecting");
      ui.recordButton.disabled = true;
      await primeTones();
      if (!options.preserveConversation) {
        resetConversationState();
        state.reconnectAttempts = 0;
      }
      const rtcConfig = await auth.fetchRtcConfig();
      const debugMode = Boolean(isDebugMode?.());
      state.adaptiveBuffer = normalizeAdaptiveBufferConfig(rtcConfig.audio);
      state.appliedPrebufferSeconds = state.adaptiveBuffer.initial;
      logger?.info("rtc config loaded", {
        iceServers: rtcConfig.ice_servers?.length || 0,
        debugMode,
        adaptiveBuffer: state.adaptiveBuffer.enabled,
        prebufferSeconds: state.adaptiveBuffer.initial,
      });

      state.peerConnection = new RTCPeerConnection({ iceServers: rtcConfig.ice_servers || [] });
      bindPeerConnection();

      state.eventsChannel = state.peerConnection.createDataChannel("events");
      state.eventsChannel.onopen = () => {
        logger?.info("events channel open");
        sendMicrophoneState();
        sendAdaptiveBuffer();
        ui.setStatus(state.isMuted ? "Mic off" : "Listening");
      };
      state.eventsChannel.onmessage = (event) => handleBridgeEvent(event.data, { state, ui, logger });
      state.eventsChannel.onerror = (event) => {
        logger?.error("events channel error", { type: event.type });
        ui.setStatus("Events error");
      };
      state.eventsChannel.onclose = () => {
        logger?.warn("events channel closed");
      };

      if (debugMode) {
        state.peerConnection.addTransceiver("audio", { direction: "recvonly" });
        setMicrophoneMuted(true);
        await updateDeviceStatus();
      } else {
        logger?.info("getUserMedia start");
        state.inputStream = await openInitialAudioInput();
        logger?.info("getUserMedia ok", {
          tracks: state.inputStream.getAudioTracks().length,
          trackLabel: state.inputStream.getAudioTracks()[0]?.label || "",
        });
        setMicrophoneMuted(false);
        state.meter = startVoiceMeter(state.inputStream, ui);
        state.inputStream.getAudioTracks().forEach((track) => {
          bindInputTrackEvents(track);
          state.peerConnection.addTrack(track, state.inputStream);
        });
        await updateDeviceStatus();
      }

      const offer = await state.peerConnection.createOffer({
        offerToReceiveAudio: true,
        offerToReceiveVideo: false,
      });
      logger?.info("local offer created", { sdpLength: offer.sdp?.length || 0 });
      await state.peerConnection.setLocalDescription(offer);
      logger?.info("local description set", { iceGatheringState: state.peerConnection.iceGatheringState });
      await waitForIceGathering(state.peerConnection);
      logger?.info("ice gathering wait complete", {
        iceGatheringState: state.peerConnection.iceGatheringState,
        localSdpLength: state.peerConnection.localDescription?.sdp?.length || 0,
      });

      const answer = await auth.sendOffer(
        state.peerConnection.localDescription,
        getTtsOptions ? getTtsOptions() : {},
      );
      await state.peerConnection.setRemoteDescription({
        type: answer.type,
        sdp: answer.sdp,
      });
      logger?.info("remote answer set", { sdpLength: answer.sdp?.length || 0 });

      setCallingState(true);
      state.callStartedAt = Date.now();
      state.statusTimer = window.setInterval(updateCallDuration, 1000);
      startNetworkMonitor();
      ui.setStatus(state.isMuted ? "Mic off" : "Listening");
    } catch (error) {
      logger?.error("call start failed", errorDetails(error));
      ui.recordButton.disabled = false;
      await endCall(error.message || "Call failed");
    }
  }

  function bindPeerConnection() {
    state.peerConnection.ontrack = (event) => {
      logger?.info("remote track received", {
        kind: event.track?.kind,
        streams: event.streams?.length || 0,
      });
      const [stream] = event.streams;
      if (stream) {
        ui.replyAudio.srcObject = stream;
        ui.replyAudio.play().catch((error) => {
          logger?.warn("remote audio play blocked", errorDetails(error));
          ui.setStatus("Tap speaker");
        });
      }
    };
    state.peerConnection.onconnectionstatechange = () => {
      const pcState = state.peerConnection?.connectionState || "closed";
      logger?.info("peer connection state", { state: pcState });
      handleConnectionState(pcState);
    };
    state.peerConnection.oniceconnectionstatechange = () => {
      const iceState = state.peerConnection?.iceConnectionState || "closed";
      logger?.info("ice connection state", { state: iceState });
      if (iceState === "failed" || iceState === "disconnected") {
        scheduleRecovery();
      }
    };
    state.peerConnection.onicegatheringstatechange = () => {
      logger?.debug("ice gathering state", { state: state.peerConnection?.iceGatheringState || "closed" });
    };
    state.peerConnection.onsignalingstatechange = () => {
      logger?.debug("signaling state", { state: state.peerConnection?.signalingState || "closed" });
    };
    state.peerConnection.onicecandidateerror = (event) => {
      logger?.warn("ice candidate error", {
        address: event.address,
        port: event.port,
        url: event.url,
        errorCode: event.errorCode,
        errorText: event.errorText,
      });
    };
  }

  async function endCall(statusText = "Ready") {
    logger?.info("call end", { statusText });
    clearRecoveryTimer();
    clearStatusTimer();
    stopNetworkMonitor();
    setCallingState(false);
    state.isSpeaking = false;
    state.isMuted = false;
    state.connectionTonePlayed = false;

    if (state.eventsChannel) {
      state.eventsChannel.close();
      state.eventsChannel = null;
    }
    if (state.peerConnection) {
      state.peerConnection.getSenders().forEach((sender) => {
        sender.track?.stop();
      });
      state.peerConnection.close();
      state.peerConnection = null;
    }
    if (state.inputStream) {
      stopVoiceMeter(state.meter, ui);
      state.meter = null;
      state.inputStream.getTracks().forEach((track) => track.stop());
      state.inputStream = null;
    }
    await updateDeviceStatus(getAudioInputDeviceId?.());
    ui.replyAudio.pause();
    ui.replyAudio.removeAttribute("src");
    ui.replyAudio.srcObject = null;
    ui.replyAudio.load();
    ui.setStatus(statusText);
  }

  function handleConnectionState(pcState) {
    if (!state.isCalling) {
      return;
    }
    if (pcState === "connected") {
      clearRecoveryTimer();
      state.reconnectAttempts = 0;
      if (!state.connectionTonePlayed) {
        state.connectionTonePlayed = true;
        playTone("connected");
      }
      ui.setStatus(state.isMuted ? "Mic off" : "Listening");
      startNetworkMonitor();
      return;
    }
    if (pcState === "disconnected") {
      ui.setNetworkQuality("offline", "WebRTC connection disconnected");
      playTone("disconnected");
      scheduleRecovery();
      return;
    }
    if (pcState === "failed") {
      ui.setNetworkQuality("offline", "WebRTC connection failed");
      playTone("disconnected");
      scheduleRecovery(0);
    }
  }

  function scheduleRecovery(delayMs = CONNECTION_RECOVERY_MS) {
    if (!state.isCalling || state.recoveryTimer) {
      return;
    }
    ui.setStatus(delayMs ? "Connection unstable" : "Reconnecting");
    logger?.warn("connection recovery scheduled", {
      delayMs,
      attempts: state.reconnectAttempts,
      maxAttempts: MAX_RECONNECT_ATTEMPTS,
    });
    state.recoveryTimer = window.setTimeout(async () => {
      state.recoveryTimer = null;
      if (!state.isCalling) {
        return;
      }
      if (state.reconnectAttempts >= MAX_RECONNECT_ATTEMPTS) {
        playTone("disconnected");
        logger?.warn("connection recovery giving up");
        await endCall("Connection lost");
        onFallback?.("WebRTC failed; HTTPS fallback active.");
        return;
      }
      state.reconnectAttempts += 1;
      await endCall("Reconnecting");
      await startCall({ preserveConversation: true });
    }, delayMs);
  }

  function toggleMicrophone() {
    setMicrophoneMuted(!state.isMuted);
    ui.setStatus(state.isMuted ? "Mic off" : "Listening");
  }

  function setMicrophoneMuted(nextIsMuted) {
    state.isMuted = nextIsMuted;
    logger?.info("microphone state changed", { muted: state.isMuted });
    if (state.inputStream) {
      state.inputStream.getAudioTracks().forEach((track) => {
        track.enabled = !state.isMuted;
      });
    }
    ui.setMuted(state.isMuted);
    if (state.isMuted) {
      ui.setVoiceActive(false);
    }
    sendMicrophoneState();
  }

  function updateCallDuration() {
    if (!state.isCalling || !state.callStartedAt || state.isSpeaking) {
      return;
    }
    if (state.isMuted) {
      ui.setStatus("Mic off");
      return;
    }
    const elapsedSeconds = Math.floor((Date.now() - state.callStartedAt) / 1000);
    ui.setStatus(`Listening ${elapsedSeconds}s`);
  }

  function clearStatusTimer() {
    if (state.statusTimer) {
      window.clearInterval(state.statusTimer);
      state.statusTimer = null;
    }
  }

  function startNetworkMonitor() {
    if (state.networkStatsTimer || !state.peerConnection) {
      return;
    }
    if (state.networkQuality === "unknown") {
      ui.setNetworkQuality("checking", "Waiting for WebRTC network statistics");
    }
    sampleNetworkStats();
    state.networkStatsTimer = window.setInterval(sampleNetworkStats, NETWORK_STATS_INTERVAL_MS);
  }

  function stopNetworkMonitor() {
    if (state.networkStatsTimer) {
      window.clearInterval(state.networkStatsTimer);
      state.networkStatsTimer = null;
    }
    state.networkStatsBusy = false;
    state.networkCounters = null;
    state.networkLossSamples = [];
    state.smoothedNetworkMetrics = null;
    state.networkQuality = "unknown";
    state.pendingNetworkQuality = "unknown";
    state.pendingNetworkSamples = 0;
    state.requestedPrebufferSeconds = null;
    state.appliedPrebufferSeconds = null;
    ui.setNetworkQuality("unknown");
  }

  async function sampleNetworkStats() {
    const pc = state.peerConnection;
    if (
      state.networkStatsBusy
      || !pc
      || !state.isCalling
      || document.hidden
      || !["connected", "connecting"].includes(pc.connectionState)
    ) {
      return;
    }
    state.networkStatsBusy = true;
    try {
      const report = await pc.getStats();
      const sample = extractNetworkMetrics(report, state.networkCounters);
      state.networkCounters = sample.counters;
      sample.metrics.lossPct = updatePacketLossWindow(
        state.networkLossSamples,
        sample.packetSample,
      );
      if (!hasNetworkMetrics(sample.metrics)) {
        return;
      }
      state.smoothedNetworkMetrics = smoothNetworkMetrics(
        state.smoothedNetworkMetrics,
        sample.metrics,
      );
      considerNetworkQuality(
        classifyNetworkQuality(state.smoothedNetworkMetrics),
        state.smoothedNetworkMetrics,
      );
    } catch (error) {
      logger?.warn("network stats collection failed", errorDetails(error));
    } finally {
      state.networkStatsBusy = false;
    }
  }

  function considerNetworkQuality(nextQuality, metrics) {
    if (nextQuality === "unknown") {
      return;
    }
    const currentQuality = state.networkQuality;
    const isWorse = QUALITY_RANK[nextQuality] > QUALITY_RANK[currentQuality];
    const requiredSamples = isWorse && nextQuality === "poor" ? 2 : isWorse ? 1 : 3;
    if (requiredSamples === 1) {
      applyNetworkQuality(nextQuality, metrics);
      return;
    }
    if (nextQuality === currentQuality) {
      state.pendingNetworkQuality = "unknown";
      state.pendingNetworkSamples = 0;
      ui.setNetworkQuality(currentQuality, formatNetworkDetails(metrics, state));
      return;
    }
    if (state.pendingNetworkQuality !== nextQuality) {
      state.pendingNetworkQuality = nextQuality;
      state.pendingNetworkSamples = 1;
    } else {
      state.pendingNetworkSamples += 1;
    }
    if (state.pendingNetworkSamples >= requiredSamples) {
      applyNetworkQuality(nextQuality, metrics);
    } else {
      ui.setNetworkQuality(currentQuality, formatNetworkDetails(metrics, state));
    }
  }

  function applyNetworkQuality(quality, metrics) {
    state.networkQuality = quality;
    state.pendingNetworkQuality = "unknown";
    state.pendingNetworkSamples = 0;
    const targetSeconds = selectPrebufferSeconds(quality, state.adaptiveBuffer);
    state.requestedPrebufferSeconds = targetSeconds;
    const details = formatNetworkDetails(metrics, state);
    ui.setNetworkQuality(quality, details);
    logger?.info("network quality changed", {
      quality,
      rttMs: roundedMetric(metrics.rttMs),
      jitterMs: roundedMetric(metrics.jitterMs),
      lossPct: roundedMetric(metrics.lossPct),
      jitterBufferMs: roundedMetric(metrics.jitterBufferMs),
      route: metrics.route,
      targetPrebufferSeconds: targetSeconds,
    });
    sendAdaptiveBuffer();
  }

  function sendAdaptiveBuffer() {
    if (
      !state.adaptiveBuffer.enabled
      || state.requestedPrebufferSeconds === null
      || state.eventsChannel?.readyState !== "open"
    ) {
      return;
    }
    state.eventsChannel.send(
      JSON.stringify({
        type: "network_quality",
        quality: state.networkQuality,
        prebuffer_seconds: state.requestedPrebufferSeconds,
      }),
    );
  }

  function clearRecoveryTimer() {
    if (state.recoveryTimer) {
      window.clearTimeout(state.recoveryTimer);
      state.recoveryTimer = null;
    }
  }

  function setCallingState(nextIsCalling) {
    state.isCalling = nextIsCalling;
    ui.setCallingState(nextIsCalling, { debugMode: Boolean(isDebugMode?.()) });
  }

  function resetConversationState() {
    state.currentTurnId = null;
    state.currentTranscript = "";
    state.currentAnswer = "";
    state.turnAnswers.clear();
    ui.resetConversation();
  }

  return {
    get isCalling() {
      return state.isCalling;
    },
    endCall,
    startCall,
    reconnect,
    toggleMicrophone,
    listAudioInputs,
    switchAudioInput,
    sendDebugText,
  };

  async function reconnect() {
    if (!state.isCalling || state.isReconnecting) {
      return false;
    }
    state.isReconnecting = true;
    logger?.info("manual reconnect start");
    try {
      await endCall("Reconnecting");
      await startCall({ preserveConversation: true });
      logger?.info("manual reconnect complete", { connected: state.isCalling });
      return state.isCalling;
    } finally {
      state.isReconnecting = false;
    }
  }

  async function listAudioInputs({ requestPermission = false } = {}) {
    if (!navigator.mediaDevices?.enumerateDevices) {
      throw new Error("This browser does not support microphone selection.");
    }
    let permissionStream = null;
    if (requestPermission && !state.inputStream) {
      permissionStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    }
    try {
      const devices = await navigator.mediaDevices.enumerateDevices();
      const currentDeviceId = state.inputStream?.getAudioTracks()[0]?.getSettings?.().deviceId || "";
      return devices
        .filter((device) => device.kind === "audioinput")
        .map((device, index) => ({
          deviceId: device.deviceId,
          label: device.label || `Microphone ${index + 1}`,
          isCurrent: Boolean(currentDeviceId && device.deviceId === currentDeviceId),
        }));
    } finally {
      permissionStream?.getTracks().forEach((track) => track.stop());
    }
  }

  async function openInitialAudioInput() {
    const preferredDeviceId = getAudioInputDeviceId?.() || "";
    try {
      return await navigator.mediaDevices.getUserMedia({
        audio: audioConstraints(preferredDeviceId),
      });
    } catch (error) {
      if (!preferredDeviceId || !["NotFoundError", "OverconstrainedError"].includes(error?.name)) {
        throw error;
      }
      logger?.warn("saved audio input unavailable; using browser default", {
        deviceId: preferredDeviceId,
        error: errorDetails(error),
      });
      onAudioInputSelected?.("");
      return navigator.mediaDevices.getUserMedia({ audio: audioConstraints("") });
    }
  }

  async function switchAudioInput(deviceId) {
    if (!deviceId) {
      return false;
    }
    if (!state.isCalling || !state.inputStream) {
      onAudioInputSelected?.(deviceId);
      await updateDeviceStatus(deviceId);
      logger?.info("audio input selected for next call", { deviceId });
      return true;
    }

    let nextStream = null;
    try {
      logger?.info("audio input switch start", { deviceId });
      nextStream = await navigator.mediaDevices.getUserMedia({
        audio: audioConstraints(deviceId),
      });
      const nextTrack = nextStream.getAudioTracks()[0];
      if (!nextTrack) {
        throw new Error("The selected microphone did not provide an audio track.");
      }
      const sender = state.peerConnection?.getSenders().find((item) => item.track?.kind === "audio");
      if (!sender) {
        throw new Error("The active call has no microphone sender.");
      }
      nextTrack.enabled = !state.isMuted;
      bindInputTrackEvents(nextTrack);
      await sender.replaceTrack(nextTrack);

      const previousStream = state.inputStream;
      stopVoiceMeter(state.meter, ui);
      state.inputStream = nextStream;
      state.meter = startVoiceMeter(nextStream, ui);
      previousStream.getTracks().forEach((track) => track.stop());
      onAudioInputSelected?.(deviceId);
      await updateDeviceStatus(deviceId);
      logger?.info("audio input switch complete", {
        deviceId,
        trackLabel: nextTrack.label || "",
      });
      return true;
    } catch (error) {
      nextStream?.getTracks().forEach((track) => track.stop());
      ui.setStatus(state.isMuted ? "Mic off" : "Listening");
      ui.setDebug(error.message || "Unable to switch microphone.");
      logger?.error("audio input switch failed", errorDetails(error));
      return false;
    }
  }

  function sendDebugText(text) {
    const trimmed = String(text || "").trim();
    if (!trimmed || !state.isCalling || state.eventsChannel?.readyState !== "open") {
      logger?.warn("debug text ignored", {
        hasText: Boolean(trimmed),
        isCalling: state.isCalling,
        channelState: state.eventsChannel?.readyState || "none",
      });
      return false;
    }
    logger?.info("debug text sent", { chars: trimmed.length });
    state.eventsChannel.send(JSON.stringify({ type: "debug_text", text: trimmed }));
    return true;
  }

  function sendMicrophoneState() {
    if (state.eventsChannel?.readyState !== "open") {
      return;
    }
    state.eventsChannel.send(
      JSON.stringify({
        type: "microphone_muted",
        muted: state.isMuted,
      }),
    );
    logger?.info(state.isMuted ? "ASR pause requested" : "ASR resume requested");
  }

  async function updateDeviceStatus(preferredDeviceId = "") {
    if (!navigator.mediaDevices?.enumerateDevices) {
      ui.setDeviceStatus("Audio route", "Device details are not available in this browser.");
      return;
    }
    try {
      const devices = await navigator.mediaDevices.enumerateDevices();
      const audioInputs = devices.filter((device) => device.kind === "audioinput");
      const currentTrack = state.inputStream?.getAudioTracks()[0];
      const preferredDevice = audioInputs.find((device) => device.deviceId === preferredDeviceId);
      const currentLabel = currentTrack?.label || preferredDevice?.label || "Browser-selected microphone";
      const visibleLabel = currentLabel.toLowerCase();
      const isBluetoothLike = /bluetooth|airpods|beats|headset|headphone|耳机/.test(visibleLabel);
      const hasSelectedInput = Boolean(currentTrack || preferredDevice);
      const shortLabel = isBluetoothLike ? "Headset" : hasSelectedInput ? "iPhone mic" : "Output";
      const inputSummary = audioInputs.length
        ? `${audioInputs.length} visible input${audioInputs.length === 1 ? "" : "s"}`
        : "No named inputs exposed";
      const controlNote = "Tap this icon to choose another exposed microphone. Output follows the iOS system route.";
      ui.setDeviceStatus(
        shortLabel,
        `${inputSummary}. Current input: ${currentLabel}. Output follows iOS system route. ${controlNote}`,
      );
      logger?.debug("device status updated", {
        inputs: audioInputs.length,
        currentLabel,
        bluetoothLike: isBluetoothLike,
      });
    } catch {
      logger?.warn("device enumeration failed");
      ui.setDeviceStatus(
        "System audio",
        "Device details are blocked. Output follows iOS system route.",
      );
    }
  }

  function bindInputTrackEvents(track) {
    track.onmute = () => {
      ui.setDeviceStatus("Input muted", "The browser reported that the audio input track is muted.");
    };
    track.onunmute = () => {
      updateDeviceStatus();
    };
  }

  async function primeTones() {
    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    if (!AudioContextClass) {
      return;
    }
    if (!state.audioContext) {
      state.audioContext = new AudioContextClass();
    }
    if (state.audioContext.state === "suspended") {
      await state.audioContext.resume().catch(() => {});
    }
  }

  function playTone(kind) {
    const ctx = state.audioContext;
    if (!ctx || ctx.state === "closed") {
      return;
    }
    const now = ctx.currentTime;
    const notes = kind === "connected"
      ? [
          [660, 0, 0.08],
          [880, 0.1, 0.12],
        ]
      : [
          [420, 0, 0.09],
          [260, 0.11, 0.16],
        ];
    notes.forEach(([frequency, offset, duration]) => {
      const oscillator = ctx.createOscillator();
      const gain = ctx.createGain();
      oscillator.type = "sine";
      oscillator.frequency.setValueAtTime(frequency, now + offset);
      gain.gain.setValueAtTime(0.0001, now + offset);
      gain.gain.exponentialRampToValueAtTime(0.045, now + offset + 0.015);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + offset + duration);
      oscillator.connect(gain);
      gain.connect(ctx.destination);
      oscillator.start(now + offset);
      oscillator.stop(now + offset + duration + 0.02);
    });
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

function waitForIceGathering(pc) {
  if (pc.iceGatheringState === "complete") {
    return Promise.resolve();
  }
  return new Promise((resolve) => {
    const timeout = window.setTimeout(resolve, 2000);
    pc.addEventListener("icegatheringstatechange", () => {
      if (pc.iceGatheringState === "complete") {
        window.clearTimeout(timeout);
        resolve();
      }
    });
  });
}

function audioConstraints(deviceId = "") {
  return {
    echoCancellation: true,
    noiseSuppression: true,
    autoGainControl: true,
    channelCount: 1,
    ...(deviceId ? { deviceId: { exact: deviceId } } : {}),
  };
}

export function extractNetworkMetrics(report, previousCounters = null) {
  const values = [];
  report?.forEach?.((value) => values.push(value));
  const inbound = values.find(
    (stat) => stat.type === "inbound-rtp" && !stat.isRemote && (stat.kind === "audio" || stat.mediaType === "audio"),
  );
  const remoteInbound = values.find(
    (stat) => stat.type === "remote-inbound-rtp" && (stat.kind === "audio" || stat.mediaType === "audio"),
  );
  const transport = values.find((stat) => stat.type === "transport" && stat.selectedCandidatePairId);
  const candidatePair = (transport && report.get?.(transport.selectedCandidatePairId))
    || values.find(
      (stat) => stat.type === "candidate-pair" && stat.state === "succeeded" && (stat.selected || stat.nominated),
    );
  const localCandidate = candidatePair?.localCandidateId
    ? report.get?.(candidatePair.localCandidateId)
    : null;
  const remoteCandidate = candidatePair?.remoteCandidateId
    ? report.get?.(candidatePair.remoteCandidateId)
    : null;

  const counters = {
    packetsReceived: finiteNumber(inbound?.packetsReceived, 0),
    packetsLost: finiteNumber(inbound?.packetsLost, 0),
    jitterBufferDelay: finiteNumber(inbound?.jitterBufferDelay, 0),
    jitterBufferEmittedCount: finiteNumber(inbound?.jitterBufferEmittedCount, 0),
  };
  const hasPrevious = previousCounters !== null;
  const previous = previousCounters || counters;
  const receivedDelta = hasPrevious
    ? nonNegativeDelta(counters.packetsReceived, previous.packetsReceived)
    : 0;
  const lostDelta = hasPrevious
    ? nonNegativeDelta(counters.packetsLost, previous.packetsLost)
    : 0;
  const emittedDelta = nonNegativeDelta(
    counters.jitterBufferEmittedCount,
    previousCounters?.jitterBufferEmittedCount || 0,
  );
  const jitterBufferDelayDelta = nonNegativeDelta(
    counters.jitterBufferDelay,
    previousCounters?.jitterBufferDelay || 0,
  );
  const route = [localCandidate?.candidateType, remoteCandidate?.candidateType].includes("relay")
    ? "turn"
    : candidatePair
      ? "direct"
      : "unknown";

  return {
    counters,
    packetSample: {
      received: receivedDelta,
      lost: lostDelta,
    },
    metrics: {
      rttMs: secondsToMs(candidatePair?.currentRoundTripTime ?? remoteInbound?.roundTripTime),
      jitterMs: secondsToMs(inbound?.jitter),
      lossPct: null,
      jitterBufferMs: emittedDelta > 0 ? (jitterBufferDelayDelta / emittedDelta) * 1000 : null,
      route,
    },
  };
}

export function classifyNetworkQuality(metrics) {
  const measured = [metrics?.rttMs, metrics?.jitterMs, metrics?.lossPct]
    .filter((value) => Number.isFinite(value));
  if (!measured.length) {
    return "unknown";
  }
  const rttMs = finiteNumber(metrics.rttMs, 0);
  const jitterMs = finiteNumber(metrics.jitterMs, 0);
  const lossPct = finiteNumber(metrics.lossPct, 0);
  if (lossPct >= 10 || jitterMs >= 80 || rttMs >= 600) {
    return "poor";
  }
  if (lossPct >= 4 || jitterMs >= 30 || rttMs >= 250) {
    return "fair";
  }
  if (lossPct >= 1 || jitterMs >= 15 || rttMs >= 120) {
    return "good";
  }
  return "excellent";
}

export function updatePacketLossWindow(
  samples,
  packetSample,
  now = Date.now(),
  windowMs = NETWORK_LOSS_WINDOW_MS,
) {
  const received = Math.max(0, finiteNumber(packetSample?.received, 0));
  const lost = Math.max(0, finiteNumber(packetSample?.lost, 0));
  if (received + lost > 0) {
    samples.push({ at: now, received, lost });
  }
  while (samples.length && now - samples[0].at > windowMs) {
    samples.shift();
  }
  const totals = samples.reduce(
    (result, sample) => ({
      received: result.received + sample.received,
      lost: result.lost + sample.lost,
    }),
    { received: 0, lost: 0 },
  );
  const packetCount = totals.received + totals.lost;
  if (packetCount < NETWORK_MIN_PACKET_SAMPLE) {
    return null;
  }
  return (totals.lost / packetCount) * 100;
}

export function selectPrebufferSeconds(quality, config = defaultAdaptiveBufferConfig()) {
  const normalized = normalizeAdaptiveBufferConfig(config);
  let target = normalized.initial;
  if (quality === "excellent") {
    target = normalized.min;
  } else if (quality === "fair") {
    target = normalized.initial + ((normalized.max - normalized.initial) * 0.5);
  } else if (quality === "poor") {
    target = normalized.max;
  }
  return Number(Math.min(normalized.max, Math.max(normalized.min, target)).toFixed(2));
}

function defaultAdaptiveBufferConfig() {
  return { enabled: true, initial: 0.6, min: 0.5, max: 1.2 };
}

function normalizeAdaptiveBufferConfig(config = {}) {
  const defaults = defaultAdaptiveBufferConfig();
  const minValue = finiteNumber(config.prebuffer_min_seconds ?? config.min, defaults.min);
  const min = Math.min(2, Math.max(0.1, minValue));
  const maxValue = Math.min(
    2,
    Math.max(0.1, finiteNumber(config.prebuffer_max_seconds ?? config.max, defaults.max)),
  );
  const max = Math.max(min, maxValue);
  const initialValue = finiteNumber(config.prebuffer_seconds ?? config.initial, defaults.initial);
  return {
    enabled: config.adaptive_buffer_enabled ?? config.enabled ?? defaults.enabled,
    min,
    max,
    initial: Math.min(max, Math.max(min, initialValue)),
  };
}

function smoothNetworkMetrics(previous, current) {
  if (!previous) {
    return current;
  }
  const alpha = 0.35;
  return {
    rttMs: smoothMetric(previous.rttMs, current.rttMs, alpha),
    jitterMs: smoothMetric(previous.jitterMs, current.jitterMs, alpha),
    // Packet loss is already aggregated over a rolling window.
    lossPct: Number.isFinite(current.lossPct) ? current.lossPct : previous.lossPct,
    jitterBufferMs: smoothMetric(previous.jitterBufferMs, current.jitterBufferMs, alpha),
    route: current.route === "unknown" ? previous.route : current.route,
  };
}

function smoothMetric(previous, current, alpha) {
  if (!Number.isFinite(current)) {
    return previous;
  }
  if (!Number.isFinite(previous)) {
    return current;
  }
  return (current * alpha) + (previous * (1 - alpha));
}

function hasNetworkMetrics(metrics) {
  return [metrics.rttMs, metrics.jitterMs, metrics.lossPct].some((value) => Number.isFinite(value));
}

function formatNetworkDetails(metrics, state) {
  const parts = [];
  if (Number.isFinite(metrics.rttMs)) {
    parts.push(`RTT ${Math.round(metrics.rttMs)} ms`);
  }
  if (Number.isFinite(metrics.jitterMs)) {
    parts.push(`jitter ${Math.round(metrics.jitterMs)} ms`);
  }
  if (Number.isFinite(metrics.lossPct)) {
    parts.push(`loss ${metrics.lossPct.toFixed(1)}%`);
  }
  if (Number.isFinite(metrics.jitterBufferMs)) {
    parts.push(`playout ${Math.round(metrics.jitterBufferMs)} ms`);
  }
  if (metrics.route && metrics.route !== "unknown") {
    parts.push(metrics.route === "turn" ? "TURN relay" : "direct path");
  }
  const bufferSeconds = state.appliedPrebufferSeconds
    ?? state.requestedPrebufferSeconds
    ?? state.adaptiveBuffer.initial;
  parts.push(`source buffer ${Number(bufferSeconds).toFixed(2)} s`);
  return parts.join(" · ");
}

function finiteNumber(value, fallback) {
  if (value === null || value === undefined || value === "") {
    return fallback;
  }
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

function nonNegativeDelta(current, previous) {
  return Math.max(0, finiteNumber(current, 0) - finiteNumber(previous, 0));
}

function secondsToMs(value) {
  const seconds = Number(value);
  return Number.isFinite(seconds) ? seconds * 1000 : null;
}

function roundedMetric(value) {
  return Number.isFinite(value) ? Number(value.toFixed(1)) : null;
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
