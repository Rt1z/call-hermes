export function createAuthClient(config, logger = null) {
  let token = localStorage.getItem("hermes.token") || "";

  function clearToken() {
    token = "";
    localStorage.removeItem("hermes.token");
  }

  async function ensureToken() {
    if (token) {
      return token;
    }
    logger?.info("auth/session start", { bridgeUrl: config.bridgeUrl });
    let response;
    try {
      response = await fetch(`${config.bridgeUrl}/auth/session`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          shared_secret: config.sharedSecret,
          device_name: navigator.userAgent,
        }),
      });
    } catch (error) {
      logger?.error("auth/session network failed", errorDetails(error));
      throw error;
    }
    if (!response.ok) {
      logger?.error("auth/session failed", { status: response.status, error: await formatError(response.clone()) });
      throw new Error("Authentication failed");
    }
    const session = await response.json();
    token = session.token;
    localStorage.setItem("hermes.token", token);
    logger?.info("auth/session ok", { expiresAt: session.expires_at });
    return token;
  }

  async function authorizedFetch(url, options = {}, retry = true) {
    const currentToken = await ensureToken();
    const headers = new Headers(options.headers || {});
    headers.set("Authorization", `Bearer ${currentToken}`);
    logger?.debug("fetch start", { url, method: options.method || "GET" });
    let response;
    try {
      response = await fetch(url, { ...options, headers });
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
        tts_voice: options.ttsVoice,
        tts_speech_rate: options.ttsSpeechRate,
        vad_silence_ms: options.vadSilenceMs,
      }),
    });
    if (!response.ok) {
      throw new Error(await formatError(response));
    }
    const payload = await response.json();
    logger?.info("rtc/offer ok", { type: payload.type, iceServers: payload.ice_servers?.length || 0 });
    return payload;
  }

  async function sendPwaTurn(audioBlob) {
    const formData = new FormData();
    const extension = audioBlob.type.includes("mp4") ? "m4a" : "webm";
    formData.append("audio", audioBlob, `recording.${extension}`);
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

  return { clearToken, ensureToken, fetchRtcConfig, sendOffer, sendPwaTurn };
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
