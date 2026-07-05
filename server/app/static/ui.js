export function createUi() {
  const ui = {
    statusEl: document.querySelector("#status"),
    networkQualityEl: document.querySelector("#networkQuality"),
    deviceStatusEl: document.querySelector("#deviceStatusText"),
    conversationEl: document.querySelector("#conversationHistory"),
    debugEl: document.querySelector("#debugText"),
    qualityMetricsEl: document.querySelector("#qualityMetrics"),
    recordButton: document.querySelector("#recordButton"),
    micButton: document.querySelector("#replayButton"),
    debugForm: document.querySelector("#debugForm"),
    debugInput: document.querySelector("#debugInput"),
    debugSendButton: document.querySelector("#debugSendButton"),
    debugEndButton: document.querySelector("#debugEndButton"),
    replyAudio: document.querySelector("#replyAudio"),
    reconnectButton: document.querySelector("#reconnectButton"),
    settingsButton: document.querySelector("#settingsButton"),
    settingsDialog: document.querySelector("#settingsDialog"),
    audioDeviceDialog: document.querySelector("#audioDeviceDialog"),
    conversationDialog: document.querySelector("#conversationDialog"),
    resumeConversationButton: document.querySelector("#resumeConversationButton"),
    newConversationButton: document.querySelector("#newConversationButton"),
    conversationHistoryButton: document.querySelector("#conversationHistoryButton"),
    conversationHistoryDialog: document.querySelector("#conversationHistoryDialog"),
    closeConversationHistoryButton: document.querySelector("#closeConversationHistoryButton"),
    conversationSearchInput: document.querySelector("#conversationSearchInput"),
    conversationList: document.querySelector("#conversationList"),
    loadMoreConversationsButton: document.querySelector("#loadMoreConversationsButton"),
    activeSessionsButton: document.querySelector("#activeSessionsButton"),
    activeSessionsDialog: document.querySelector("#activeSessionsDialog"),
    closeActiveSessionsButton: document.querySelector("#closeActiveSessionsButton"),
    activeSessionList: document.querySelector("#activeSessionList"),
    authorizedDeviceList: document.querySelector("#authorizedDeviceList"),
    accountButton: document.querySelector("#accountButton"),
    accountDialog: document.querySelector("#accountDialog"),
    accountForm: document.querySelector("#accountForm"),
    currentPasswordInput: document.querySelector("#currentPasswordInput"),
    newPasswordInput: document.querySelector("#newPasswordInput"),
    logoutButton: document.querySelector("#logoutButton"),
    closeAccountButton: document.querySelector("#closeAccountButton"),
    installButton: document.querySelector("#installButton"),
    platformCapability: document.querySelector("#platformCapability"),
    audioDeviceList: document.querySelector("#audioDeviceList"),
    audioDeviceHelp: document.querySelector("#audioDeviceHelp"),
    settingsFocusTarget: document.querySelector("#settingsFocusTarget"),
    bridgeUrlInput: document.querySelector("#bridgeUrlInput"),
    usernameInput: document.querySelector("#usernameInput"),
    secretInput: document.querySelector("#secretInput"),
    ttsVoiceSelect: document.querySelector("#ttsVoiceSelect"),
    ttsVoiceDescription: document.querySelector("#ttsVoiceDescription"),
    assistantPresetSelect: document.querySelector("#assistantPresetSelect"),
    hermesModelInput: document.querySelector("#hermesModelInput"),
    languageSelect: document.querySelector("#languageSelect"),
    systemPromptInput: document.querySelector("#systemPromptInput"),
    maxTokensInput: document.querySelector("#maxTokensInput"),
    historyTurnsInput: document.querySelector("#historyTurnsInput"),
    speechRateInput: document.querySelector("#speechRateInput"),
    speechRateValue: document.querySelector("#speechRateValue"),
    vadSilenceInput: document.querySelector("#vadSilenceInput"),
    vadSilenceValue: document.querySelector("#vadSilenceValue"),
    debugModeInput: document.querySelector("#debugModeInput"),
    saveSettingsButton: document.querySelector("#saveSettingsButton"),
  };

  ui.micButton.disabled = true;
  ui.reconnectButton.disabled = true;
  ui.debugSendButton.disabled = true;
  ui.micButton.setAttribute("aria-label", "Mute microphone");
  const turns = new Map();

  return {
    ...ui,
    setStatus(text) {
      ui.statusEl.textContent = text;
      document.body.dataset.status = statusKind(text);
    },
    setNetworkQuality(quality = "unknown", details = "") {
      const labels = {
        checking: "Checking",
        excellent: "Excellent",
        good: "Good",
        fair: "Fair",
        poor: "Poor",
        offline: "Offline",
      };
      const normalized = labels[quality] ? quality : "unknown";
      const label = ui.networkQualityEl.querySelector(".network-quality-label");
      ui.networkQualityEl.dataset.quality = normalized;
      ui.networkQualityEl.hidden = normalized === "unknown";
      ui.networkQualityEl.title = details;
      ui.networkQualityEl.setAttribute(
        "aria-label",
        details ? `Network ${labels[normalized] || "unknown"}. ${details}` : `Network ${labels[normalized] || "unknown"}`,
      );
      if (label) {
        label.textContent = labels[normalized] || "Checking";
      }
    },
    setDebug(text) {
      ui.debugEl.textContent = text || "No debug information.";
    },
    setQualityMetrics(metrics, history = []) {
      const rows = [
        ["Hermes first token", metrics.asr_final__to__hermes_first_token_ms],
        ["TTS first audio", metrics.hermes_first_token__to__tts_first_audio_ms],
        ["Total response", metrics.asr_final__to__speaking_end_ms],
      ].filter(([, value]) => Number.isFinite(Number(value)));
      ui.qualityMetricsEl.replaceChildren(...rows.map(([label, value]) => {
        const row = document.createElement("div");
        row.innerHTML = `<span>${escapeHtml(label)}</span><strong>${Math.round(Number(value))} ms</strong>`;
        return row;
      }));
      const totals = history
        .map((item) => Number(item.asr_final__to__speaking_end_ms))
        .filter(Number.isFinite);
      if (totals.length) {
        const trend = document.createElement("div");
        trend.className = "quality-trend";
        const average = totals.reduce((sum, value) => sum + value, 0) / totals.length;
        trend.innerHTML = `<span>${Math.min(totals.length, 50)}-turn average</span><strong>${Math.round(average)} ms</strong>`;
        ui.qualityMetricsEl.append(trend);
      }
      if (!rows.length) ui.qualityMetricsEl.textContent = "No completed turns yet.";
    },
    setDeviceStatus(text, details = "") {
      const label = ui.deviceStatusEl.querySelector(".device-label");
      if (label) {
        label.textContent = text;
      }
      ui.deviceStatusEl.dataset.device = deviceKind(text, details);
      ui.deviceStatusEl.setAttribute("aria-label", details || text);
      ui.deviceStatusEl.title = details || text;
    },
    setAudioDevices(devices, selectedDeviceId = "") {
      ui.audioDeviceList.replaceChildren();
      devices.forEach((device, index) => {
        const button = document.createElement("button");
        const selected = device.deviceId === selectedDeviceId
          || (!selectedDeviceId && device.isCurrent)
          || (!selectedDeviceId && index === 0);
        button.type = "button";
        button.className = "device-option";
        button.dataset.deviceId = device.deviceId;
        button.setAttribute("role", "option");
        button.setAttribute("aria-selected", String(selected));
        button.innerHTML = `<span class="device-option-icon" aria-hidden="true"></span><span>${escapeHtml(device.label)}</span><span class="device-check" aria-hidden="true">✓</span>`;
        ui.audioDeviceList.append(button);
      });
    },
    setActiveSessions(sessions) {
      ui.activeSessionList.replaceChildren();
      if (!sessions.length) {
        const empty = document.createElement("p");
        empty.className = "conversation-empty";
        empty.textContent = "No active sessions.";
        ui.activeSessionList.append(empty);
        return;
      }
      sessions.forEach((session) => {
        const row = document.createElement("article");
        row.className = "active-session-item";
        const age = formatDuration(Math.max(0, Date.now() / 1000 - Number(session.started_at || 0)));
        row.innerHTML = `<div><strong>${escapeHtml(shortDeviceName(session.device_name))}${session.current ? " (This device)" : ""}</strong><small>${escapeHtml(session.state || "unknown")} · ${age}</small></div><button type="button" data-session-id="${escapeHtml(session.session_id)}">Disconnect</button>`;
        ui.activeSessionList.append(row);
      });
    },
    setAuthorizedDevices(devices) {
      ui.authorizedDeviceList.replaceChildren();
      devices.forEach((device) => {
        const row = document.createElement("article");
        row.className = "active-session-item";
        const state = device.revoked_at ? "Revoked" : device.current ? "This device" : "Authorized";
        row.innerHTML = `<div><strong>${escapeHtml(shortDeviceName(device.name))}</strong><small>${state} · ${escapeHtml(formatConversationDate(device.last_seen_at))}</small></div><button type="button" data-device-id="${escapeHtml(device.id)}" ${device.revoked_at ? "disabled" : ""}>${device.current ? "Log out" : "Revoke"}</button>`;
        ui.authorizedDeviceList.append(row);
      });
      if (!devices.length) ui.authorizedDeviceList.textContent = "No authorized devices.";
    },
    setCallingState(isCalling, options = {}) {
      document.body.classList.toggle("calling", isCalling);
      document.body.classList.toggle("debug-mode", Boolean(options.debugMode));
      ui.recordButton.disabled = false;
      ui.reconnectButton.disabled = !isCalling;
      ui.recordButton.setAttribute("aria-label", isCalling ? "End call" : "Start call");
      ui.micButton.disabled = !isCalling || Boolean(options.debugMode);
      ui.debugSendButton.disabled = !isCalling || !Boolean(options.debugMode);
      if (!isCalling) {
        document.body.classList.remove("muted");
        document.body.classList.remove("debug-mode");
        document.body.classList.remove("voice-active");
        ui.micButton.setAttribute("aria-label", "Mute microphone");
        ui.debugInput.value = "";
      }
    },
    setMuted(isMuted) {
      document.body.classList.toggle("muted", isMuted);
      ui.micButton.setAttribute("aria-label", isMuted ? "Unmute microphone" : "Mute microphone");
    },
    setFallbackMode(isFallback) {
      document.body.classList.toggle("fallback-mode", isFallback);
      document.body.classList.toggle("calling", isFallback);
      ui.recordButton.disabled = false;
      ui.reconnectButton.disabled = true;
      ui.recordButton.setAttribute("aria-label", isFallback ? "Exit fallback mode" : "Start call");
      ui.micButton.disabled = !isFallback;
      ui.micButton.setAttribute("aria-label", "Record fallback turn");
      if (!isFallback) {
        document.body.classList.remove("fallback-recording");
        document.body.classList.remove("voice-active");
      }
    },
    setFallbackRecording(isRecording) {
      document.body.classList.toggle("fallback-recording", isRecording);
      ui.micButton.setAttribute("aria-label", isRecording ? "Stop and send" : "Record fallback turn");
    },
    setVoiceActive(isActive) {
      document.body.classList.toggle("voice-active", isActive);
    },
    resetConversation() {
      turns.clear();
      ui.conversationEl.replaceChildren();
      ui.debugEl.textContent = "No debug information.";
    },
    restoreConversation(messages = []) {
      const pairs = [];
      for (const message of messages) {
        if (message?.role === "user") {
          pairs.push({ user: message.content, assistant: "" });
        } else if (message?.role === "assistant" && pairs.length) {
          pairs[pairs.length - 1].assistant = message.content;
        }
      }
      pairs.forEach((pair, index) => {
        const turnId = `history-${index}`;
        this.setTurnUser(turnId, pair.user);
        if (pair.assistant) {
          this.setTurnAnswer(turnId, pair.assistant);
          this.setTurnComplete(turnId);
        }
      });
    },
    setConversationList(conversations = [], currentConversationId = "", append = false) {
      if (!conversations.length) {
        if (!append) ui.conversationList.innerHTML = '<p class="conversation-empty">No conversations found.</p>';
        return;
      }
      const rows = conversations.map((conversation) => {
        const row = document.createElement("div");
        row.className = "conversation-item";
        row.dataset.current = String(conversation.conversation_id === currentConversationId);
        row.innerHTML = `
          <button class="conversation-open" type="button" data-action="open">
            <span>${escapeHtml(conversation.title)}</span>
            <small>${Number(conversation.turn_count) || 0} turns · ${escapeHtml(formatConversationDate(conversation.updated_at))}</small>
          </button>
          <button class="conversation-action ${conversation.favorite ? "favorite" : ""}" type="button" data-action="favorite" aria-label="Favorite conversation" title="Favorite">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3l2.8 5.7 6.2.9-4.5 4.4 1.1 6.2-5.6-3-5.6 3 1.1-6.2L3 9.6l6.2-.9L12 3z"></path></svg>
          </button>
          <button class="conversation-action" type="button" data-action="rename" aria-label="Rename conversation" title="Rename">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 20h4l11-11-4-4L4 16v4zM13.5 6.5l4 4"></path></svg>
          </button>
          <button class="conversation-action" type="button" data-action="export" aria-label="Export conversation" title="Export">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3v12m0 0l-4-4m4 4l4-4M5 19h14"></path></svg>
          </button>
          <button class="conversation-action danger" type="button" data-action="delete" aria-label="Delete conversation" title="Delete">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M9 7V4h6v3m-9 0l1 14h10l1-14M10 11v6m4-6v6"></path></svg>
          </button>`;
        row.querySelectorAll("button[data-action]").forEach((button) => {
          button.dataset.id = conversation.conversation_id;
          button.dataset.title = conversation.title;
          button.dataset.favorite = String(Boolean(conversation.favorite));
        });
        return row;
      });
      if (append) ui.conversationList.append(...rows);
      else ui.conversationList.replaceChildren(...rows);
    },
    setTurnUser(turnId, text, options = {}) {
      const turn = ensureTurn(turnId);
      updateConversation(() => {
        turn.userText.textContent = text || "…";
        turn.root.classList.toggle("partial", Boolean(options.partial));
      });
    },
    setTurnThinking(turnId) {
      const turn = ensureTurn(turnId);
      updateConversation(() => {
        turn.assistantPanel.hidden = false;
        if (!turn.assistantText.textContent) {
          turn.assistantText.textContent = "…";
          turn.assistantText.dataset.placeholder = "true";
        }
        turn.root.classList.add("thinking");
      });
    },
    setTurnAnswer(turnId, text) {
      const turn = ensureTurn(turnId);
      updateConversation(() => {
        turn.assistantPanel.hidden = false;
        turn.assistantText.textContent = text || "…";
        delete turn.assistantText.dataset.placeholder;
        turn.root.classList.remove("thinking", "interrupted");
        turn.root.classList.add("streaming");
      });
    },
    setTurnInterrupted(turnId) {
      const turn = turns.get(turnId);
      if (!turn) {
        return;
      }
      turn.root.classList.remove("thinking", "streaming");
      turn.root.classList.add("interrupted");
      if (turn.assistantText.dataset.placeholder === "true") {
        turn.assistantText.textContent = "";
        delete turn.assistantText.dataset.placeholder;
        turn.assistantPanel.hidden = true;
      }
    },
    setTurnComplete(turnId) {
      const turn = turns.get(turnId);
      if (!turn) {
        return;
      }
      turn.root.classList.remove("partial", "thinking", "streaming");
    },
    setSpeechRateValue(value) {
      ui.speechRateValue.textContent = `${Number(value).toFixed(2)}x`;
    },
    setVadSilenceValue(value) {
      ui.vadSilenceValue.textContent = `${value}ms`;
    },
  };

  function ensureTurn(turnId) {
    const id = String(turnId || `local-${Date.now()}-${turns.size + 1}`);
    const existing = turns.get(id);
    if (existing) {
      return existing;
    }

    const root = document.createElement("article");
    root.className = "conversation-turn";
    root.dataset.turnId = id;

    const userPanel = createMessagePanel("user-panel", "You");
    const assistantPanel = createMessagePanel("assistant-panel", "Hermes");
    assistantPanel.panel.hidden = true;
    root.append(userPanel.panel, assistantPanel.panel);
    ui.conversationEl.append(root);

    const turn = {
      root,
      userText: userPanel.text,
      assistantPanel: assistantPanel.panel,
      assistantText: assistantPanel.text,
    };
    turns.set(id, turn);
    scrollConversationToBottom();
    return turn;
  }

  function createMessagePanel(className, labelText) {
    const panel = document.createElement("div");
    panel.className = `transcript-panel ${className}`;
    panel.setAttribute("role", "group");
    panel.setAttribute("aria-label", `${labelText} message`);
    const message = document.createElement("p");
    panel.append(message);
    return { panel, text: message };
  }

  function updateConversation(update) {
    const distanceFromBottom = ui.conversationEl.scrollHeight
      - ui.conversationEl.scrollTop
      - ui.conversationEl.clientHeight;
    update();
    if (distanceFromBottom < 96) {
      scrollConversationToBottom();
    }
  }

  function scrollConversationToBottom() {
    requestAnimationFrame(() => {
      ui.conversationEl.scrollTop = ui.conversationEl.scrollHeight;
    });
  }
}

function escapeHtml(value) {
  const element = document.createElement("span");
  element.textContent = String(value);
  return element.innerHTML;
}

function formatConversationDate(value) {
  if (!value) {
    return "Unknown date";
  }
  const date = new Date(String(value).replace(" ", "T") + "Z");
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString();
}

function formatDuration(seconds) {
  const total = Math.floor(seconds);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  if (hours) {
    return `${hours}h ${minutes}m`;
  }
  return `${minutes}m`;
}

function shortDeviceName(value) {
  const name = String(value || "Unknown device");
  if (/iphone|ipad/i.test(name)) return "iPhone / iPad";
  if (/android/i.test(name)) return "Android";
  if (/firefox/i.test(name)) return "Firefox";
  if (/edg\//i.test(name)) return "Microsoft Edge";
  if (/chrome|chromium/i.test(name)) return "Chrome";
  if (/safari/i.test(name)) return "Safari";
  return name.slice(0, 60);
}

function deviceKind(text, details = "") {
  const normalized = `${text} ${details}`.toLowerCase();
  if (normalized.includes("muted")) {
    return "muted";
  }
  if (normalized.includes("bluetooth") || normalized.includes("airpods") || normalized.includes("headset") || normalized.includes("headphone") || normalized.includes("耳机")) {
    return "headset";
  }
  if (normalized.includes("output")) {
    return "output";
  }
  return "mic";
}

function statusKind(text) {
  const normalized = String(text).toLowerCase();
  if (normalized.includes("lost") || normalized.includes("failed") || normalized.includes("error")) {
    return "error";
  }
  if (normalized.includes("unstable") || normalized.includes("reconnecting") || normalized.includes("connecting")) {
    return "busy";
  }
  if (normalized.includes("speaking") || normalized.includes("playing")) {
    return "speaking";
  }
  if (normalized.includes("off") || normalized.includes("muted")) {
    return "muted";
  }
  if (normalized.includes("listening")) {
    return "listening";
  }
  return "idle";
}
