import assert from "node:assert/strict";

const storage = new Map();
globalThis.localStorage = {
  getItem: (key) => storage.get(key) || null,
  setItem: (key, value) => storage.set(key, String(value)),
  removeItem: (key) => storage.delete(key),
};
Object.defineProperty(globalThis, "navigator", { value: { userAgent: "Node test browser" } });

const { createAuthClient } = await import("../server/app/static/auth.js");
const config = { bridgeUrl: "https://example.test", username: "admin", sharedSecret: "password" };

let refreshCalls = 0;
globalThis.fetch = async (url) => {
  if (url.endsWith("/auth/refresh")) {
    refreshCalls += 1;
    await new Promise((resolve) => setTimeout(resolve, 10));
    return Response.json({ token: "access-1", expires_at: "later", device_id: "device-1" });
  }
  throw new Error(`Unexpected URL ${url}`);
};
const concurrentClient = createAuthClient(config);
assert.deepEqual(await Promise.all([concurrentClient.ensureToken(), concurrentClient.ensureToken()]), ["access-1", "access-1"]);
assert.equal(refreshCalls, 1);

let loginCalls = 0;
let refreshSequence = 0;
globalThis.fetch = async (url) => {
  if (url.endsWith("/auth/refresh")) {
    refreshSequence += 1;
    return refreshSequence === 1
      ? Response.json({ token: "access-2", expires_at: "later", device_id: "device-2" })
      : Response.json({ detail: "revoked" }, { status: 401 });
  }
  if (url.endsWith("/auth/login")) {
    loginCalls += 1;
    return Response.json({ token: "unexpected" });
  }
  if (url.endsWith("/rtc/config")) {
    return Response.json({ detail: "expired" }, { status: 401 });
  }
  throw new Error(`Unexpected URL ${url}`);
};
const revokedClient = createAuthClient(config);
await revokedClient.ensureToken();
await assert.rejects(revokedClient.fetchRtcConfig(), /Authentication failed/);
assert.equal(loginCalls, 0);

console.log("Frontend authentication tests passed");
