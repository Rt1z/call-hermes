export function handleBridgeEvent(raw, context) {
  let event;
  try {
    event = JSON.parse(raw);
  } catch {
    return;
  }

  const { state, ui } = context;
  const logger = context.logger || null;
  logger?.debug("bridge event", { type: event.type, state: event.state || "", source: event.source || "" });

  if (event.type === "listening") {
    state.isSpeaking = false;
    ui.setStatus(state.isMuted ? "Mic off" : "Listening");
    return;
  }
  if (event.type === "partial_transcript") {
    const turnId = resolveTurnId(event, state);
    ui.setTurnUser(turnId, event.text || "", { partial: true });
    ui.setStatus(state.isMuted ? "Mic off" : "Listening");
    return;
  }
  if (event.type === "final_transcript") {
    const turnId = resolveTurnId(event, state);
    state.currentTranscript = event.text || "";
    state.currentAnswer = "";
    state.turnAnswers.set(turnId, "");
    ui.setTurnUser(turnId, state.currentTranscript);
    ui.setTurnThinking(turnId);
    ui.setStatus("Thinking");
    return;
  }
  if (event.type === "thinking") {
    const turnId = resolveTurnId(event, state);
    state.currentTranscript = event.text || state.currentTranscript;
    ui.setTurnUser(turnId, state.currentTranscript);
    ui.setTurnThinking(turnId);
    ui.setStatus("Thinking");
    return;
  }
  if (event.type === "answer_delta") {
    const turnId = resolveTurnId(event, state);
    const answer = `${state.turnAnswers.get(turnId) || ""}${event.text || ""}`;
    state.turnAnswers.set(turnId, answer);
    state.currentAnswer = answer;
    ui.setTurnAnswer(turnId, answer);
    return;
  }
  if (event.type === "speaking") {
    const turnId = event.turn_id || state.currentTurnId;
    state.isSpeaking = event.state === "start";
    if (event.state === "interrupted") {
      ui.setTurnInterrupted(turnId);
      ui.setStatus("Interrupted");
    } else {
      if (event.state === "end") {
        ui.setTurnComplete(turnId);
      }
      ui.setStatus(state.isSpeaking ? "Speaking" : state.isMuted ? "Mic off" : "Listening");
    }
    return;
  }
  if (event.type === "microphone") {
    state.isMuted = Boolean(event.muted);
    ui.setMuted(state.isMuted);
    ui.setStatus(state.isMuted ? "Mic off" : "Listening");
    return;
  }
  if (event.type === "asr_state") {
    ui.setDebug(event.state === "stopped" ? "ASR paused" : "ASR active");
    return;
  }
  if (event.type === "network_buffer") {
    const seconds = Number(event.prebuffer_seconds);
    if (Number.isFinite(seconds)) {
      state.appliedPrebufferSeconds = seconds;
    }
    logger?.info("adaptive audio buffer applied", {
      quality: event.quality || "unknown",
      prebufferSeconds: state.appliedPrebufferSeconds,
    });
    return;
  }
  if (event.type === "vad_state") {
    if (event.state === "speech") {
      ui.setDebug("VAD speech detected; ASR active");
      ui.setVoiceActive(true);
    } else if (event.state === "silence") {
      ui.setDebug("VAD silence; ASR paused");
      ui.setVoiceActive(false);
    } else if (event.state === "muted") {
      ui.setDebug("VAD muted; ASR paused");
      ui.setVoiceActive(false);
    }
    return;
  }
  if (event.type === "error") {
    logger?.error("bridge error event", {
      source: event.source || "",
      message: event.message || "",
    });
    ui.setStatus(event.message || "Bridge error");
    ui.setDebug(event.source ? `Error ${event.source}` : "Error");
  }
}

function resolveTurnId(event, state) {
  if (event.turn_id) {
    state.currentTurnId = String(event.turn_id);
    return state.currentTurnId;
  }
  if (!state.currentTurnId) {
    state.currentTurnId = `legacy-${Date.now()}`;
  }
  return state.currentTurnId;
}
