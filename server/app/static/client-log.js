const MAX_VISIBLE_LOGS = 8;

export function createClientLogger({ ui, getBridgeUrl }) {
  const visibleLogs = [];

  function log(level, message, details = {}) {
    const entry = {
      level,
      message: String(message || ""),
      details: sanitize(details),
      url: window.location.href,
      user_agent: navigator.userAgent,
      ts: new Date().toISOString(),
    };
    visibleLogs.unshift(entry);
    visibleLogs.splice(MAX_VISIBLE_LOGS);
    ui.setDebug(formatVisibleLogs(visibleLogs));
    console[level === "error" ? "error" : level === "warn" ? "warn" : "log"](
      `[client] ${entry.message}`,
      entry.details,
    );
    if (level !== "debug") {
      send(entry);
    }
  }

  function send(entry) {
    const bridgeUrl = getBridgeUrl?.() || window.location.origin;
    const endpoint = `${String(bridgeUrl).replace(/\/$/, "")}/client/log`;
    const body = JSON.stringify(entry);
    if (navigator.sendBeacon) {
      try {
        const blob = new Blob([body], { type: "application/json" });
        if (navigator.sendBeacon(endpoint, blob)) {
          return;
        }
      } catch {
        // Fall through to fetch.
      }
    }
    fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body,
      keepalive: true,
    }).catch(() => {});
  }

  return {
    debug(message, details) {
      log("debug", message, details);
    },
    info(message, details) {
      log("info", message, details);
    },
    warn(message, details) {
      log("warn", message, details);
    },
    error(message, details) {
      log("error", message, details);
    },
  };
}

function formatVisibleLogs(logs) {
  return logs
    .map((entry) => {
      const time = entry.ts.slice(11, 19);
      const detailText = compactDetails(entry.details);
      return `${time} ${entry.level.toUpperCase()} ${entry.message}${detailText ? ` ${detailText}` : ""}`;
    })
    .join("\n");
}

function compactDetails(details) {
  const pairs = Object.entries(details || {})
    .filter(([, value]) => value !== undefined && value !== null && value !== "")
    .slice(0, 5)
    .map(([key, value]) => `${key}=${String(value).slice(0, 80)}`);
  return pairs.length ? `[${pairs.join(" ")}]` : "";
}

function sanitize(value) {
  if (!value || typeof value !== "object") {
    return {};
  }
  const output = {};
  Object.entries(value).forEach(([key, item]) => {
    const normalizedKey = key.toLowerCase();
    if (normalizedKey.includes("secret") || normalizedKey.includes("token") || normalizedKey.includes("authorization")) {
      return;
    }
    output[key] = typeof item === "string" ? item.slice(0, 500) : item;
  });
  return output;
}
