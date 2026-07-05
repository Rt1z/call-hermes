export function createAuthClient(config, logger = null) {
  let token = "";
  let tokenPromise = null;
  let hasAuthenticated = false;
  localStorage.removeItem("hermes.token");

  function clearToken() {
    token = "";
  }

  async function ensureToken() {
    if (token) {
      return token;
    }
    if (tokenPromise) {
      return tokenPromise;
    }
    tokenPromise = acquireToken();
    try {
      return await tokenPromise;
    } finally {
      tokenPromise = null;
    }
  }

  async function acquireToken() {
    logger?.info("auth refresh start", { bridgeUrl: config.bridgeUrl });
    let response;
    try {
      response = await fetch(`${config.bridgeUrl}/auth/refresh`, {
        method: "POST",
        credentials: "include",
      });
      if (!response.ok && !hasAuthenticated && config.sharedSecret) {
        response = await fetch(`${config.bridgeUrl}/auth/login`, {
          method: "POST",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            username: config.username || "admin",
            password: config.sharedSecret,
            device_name: navigator.userAgent,
            device_id: localStorage.getItem("hermes.deviceId") || null,
          }),
        });
      }
    } catch (error) {
      logger?.error("authentication network failed", errorDetails(error));
      throw error;
    }
    if (!response.ok) {
      logger?.error("authentication failed", { status: response.status, error: await formatError(response.clone()) });
      throw new Error("Authentication failed");
    }
    const session = await response.json();
    token = session.token;
    hasAuthenticated = true;
    if (session.device_id) {
      localStorage.setItem("hermes.deviceId", session.device_id);
    }
    logger?.info("authentication ok", { expiresAt: session.expires_at });
    return token;
  }

  async function authorizedFetch(url, options = {}, retry = true) {
    const currentToken = await ensureToken();
    const headers = new Headers(options.headers || {});
    headers.set("Authorization", `Bearer ${currentToken}`);
    logger?.debug("fetch start", { url, method: options.method || "GET" });
    let response;
    try {
      response = await fetch(url, { ...options, headers, credentials: "include" });
    } catch (error) {
      logger?.error("fetch network failed", { ...errorDetails(error), url, method: options.method || "GET" });
      throw error;
    }
    logger?.debug("fetch complete", { url, method: options.method || "GET", status: response.status });
    if (response.status === 401 && retry) {
      logger?.warn("fetch unauthorized; refreshing token", { url });
      clearToken();
      return authorizedFetch(url, options, false);
    }
    return response;
  }

  async function fetchRtcConfig() {
    const response = await authorizedFetch(`${config.bridgeUrl}/rtc/config`);
    if (!response.ok) {
      throw new Error(await formatError(response));
    }
    const payload = await response.json();
    logger?.info("rtc/config ok", { iceServers: payload.ice_servers?.length || 0 });
    return payload;
  }

  async function sendOffer(description, options = {}) {
    const response = await authorizedFetch(`${config.bridgeUrl}/rtc/offer`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        type: description.type,
        sdp: description.sdp,
        preserve_conversation: options.preserveConversation !== false,
        conversation_id: options.conversationId,
        tts_voice: options.ttsVoice,
        tts_speech_rate: options.ttsSpeechRate,
        vad_silence_ms: options.vadSilenceMs,
        hermes_model: options.hermesModel,
        system_prompt: options.systemPrompt,
        max_tokens: options.maxTokens,
        history_max_turns: options.historyMaxTurns,
        language: options.language,
      }),
    });
    if (!response.ok) {
      throw new Error(await formatError(response));
    }
    const payload = await response.json();
    logger?.info("rtc/offer ok", { type: payload.type, iceServers: payload.ice_servers?.length || 0 });
    return payload;
  }

  async function sendPwaTurn(audioBlob, options = {}) {
    const formData = new FormData();
    const extension = audioBlob.type.includes("mp4") ? "m4a" : "webm";
    formData.append("audio", audioBlob, `recording.${extension}`);
    if (options.conversationId) {
      formData.append("conversation_id", options.conversationId);
    }
    if (options.ttsVoice) {
      formData.append("tts_voice", options.ttsVoice);
    }
    if (options.ttsSpeechRate) {
      formData.append("tts_speech_rate", String(options.ttsSpeechRate));
    }
    for (const [key, value] of [
      ["hermes_model", options.hermesModel],
      ["system_prompt", options.systemPrompt],
      ["max_tokens", options.maxTokens],
      ["history_max_turns", options.historyMaxTurns],
      ["language", options.language],
    ]) {
      if (value !== undefined && value !== null && value !== "") formData.append(key, String(value));
    }
    const response = await authorizedFetch(`${config.bridgeUrl}/pwa/turn`, {
      method: "POST",
      body: formData,
    });
    if (!response.ok) {
      throw new Error(await formatError(response));
    }
    const payload = await response.json();
    logger?.info("pwa/turn ok", { turnId: payload.turn_id, transcriptLength: payload.transcript?.length || 0 });
    return payload;
  }

  async function closeSession() {
    if (!token) {
      return;
    }
    try {
      const response = await fetch(`${config.bridgeUrl}/rtc/session`, {
        method: "DELETE",
        headers: { Authorization: `Bearer ${token}` },
        keepalive: true,
        credentials: "include",
      });
      logger?.info("rtc/session closed", { status: response.status });
    } catch (error) {
      logger?.warn("rtc/session close failed", errorDetails(error));
    }
  }

  async function fetchConversation(conversationId) {
    const response = await authorizedFetch(
      `${config.bridgeUrl}/conversations/${encodeURIComponent(conversationId)}`,
    );
    if (!response.ok) {
      throw new Error(await formatError(response));
    }
    return response.json();
  }

  async function listConversations(query = "", offset = 0, limit = 25) {
    const params = new URLSearchParams({ query, offset: String(offset), limit: String(limit) });
    const response = await authorizedFetch(`${config.bridgeUrl}/conversations?${params}`);
    if (!response.ok) {
      throw new Error(await formatError(response));
    }
    return response.json();
  }

  async function deleteConversation(conversationId) {
    const response = await authorizedFetch(
      `${config.bridgeUrl}/conversations/${encodeURIComponent(conversationId)}`,
      { method: "DELETE" },
    );
    if (!response.ok) {
      throw new Error(await formatError(response));
    }
  }

  async function updateConversation(conversationId, changes) {
    const response = await authorizedFetch(
      `${config.bridgeUrl}/conversations/${encodeURIComponent(conversationId)}`,
      { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(changes) },
    );
    if (!response.ok) throw new Error(await formatError(response));
    return response.json();
  }

  async function listRtcSessions() {
    const response = await authorizedFetch(`${config.bridgeUrl}/rtc/sessions`);
    if (!response.ok) {
      throw new Error(await formatError(response));
    }
    return response.json();
  }

  async function terminateRtcSession(sessionId) {
    const response = await authorizedFetch(
      `${config.bridgeUrl}/rtc/sessions/${encodeURIComponent(sessionId)}`,
      { method: "DELETE" },
    );
    if (!response.ok) {
      throw new Error(await formatError(response));
    }
  }

  async function listDevices() {
    const response = await authorizedFetch(`${config.bridgeUrl}/auth/devices`);
    if (!response.ok) throw new Error(await formatError(response));
    return response.json();
  }

  async function revokeDevice(deviceId) {
    const response = await authorizedFetch(
      `${config.bridgeUrl}/auth/devices/${encodeURIComponent(deviceId)}`,
      { method: "DELETE" },
    );
    if (!response.ok) throw new Error(await formatError(response));
    return response.json();
  }

  async function logout() {
    try {
      await ensureToken();
      await authorizedFetch(`${config.bridgeUrl}/auth/logout`, { method: "POST" }, false);
    } catch (error) {
      logger?.warn("logout request failed", errorDetails(error));
    } finally {
      clearToken();
    }
  }

  async function changePassword(currentPassword, newPassword) {
    const response = await authorizedFetch(`${config.bridgeUrl}/auth/password`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
    });
    if (!response.ok) throw new Error(await formatError(response));
  }

  return {
    clearToken,
    ensureToken,
    fetchRtcConfig,
    sendOffer,
    sendPwaTurn,
    closeSession,
    fetchConversation,
    listConversations,
    deleteConversation,
    updateConversation,
    listRtcSessions,
    terminateRtcSession,
    listDevices,
    revokeDevice,
    logout,
    changePassword,
  };
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

export async function formatError(response) {
  try {
    const payload = await response.json();
    const detail = payload.detail || {};
    if (detail.message) {
      return detail.message;
    }
    if (typeof detail === "string") {
      return detail;
    }
  } catch {
    return `Request failed (${response.status})`;
  }
  return `Request failed (${response.status})`;
}
