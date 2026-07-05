import assert from "node:assert/strict";

import {
  classifyNetworkQuality,
  selectPrebufferSeconds,
  summarizeIceCandidates,
  updatePacketLossWindow,
} from "../server/app/static/rtc.js";

assert.equal(classifyNetworkQuality({}), "unknown");
assert.equal(classifyNetworkQuality({ rttMs: 40, jitterMs: 4, lossPct: 0 }), "excellent");
assert.equal(classifyNetworkQuality({ rttMs: 120, jitterMs: 4, lossPct: 0 }), "good");
assert.equal(classifyNetworkQuality({ rttMs: 250, jitterMs: 4, lossPct: 0 }), "fair");
assert.equal(classifyNetworkQuality({ rttMs: 600, jitterMs: 4, lossPct: 0 }), "poor");
assert.equal(classifyNetworkQuality({ rttMs: 40, jitterMs: 80, lossPct: 0 }), "poor");
assert.equal(classifyNetworkQuality({ rttMs: 40, jitterMs: 4, lossPct: 10 }), "poor");

const lossSamples = [];
assert.equal(updatePacketLossWindow(lossSamples, { received: 20, lost: 2 }, 1000), null);
assert.equal(updatePacketLossWindow(lossSamples, { received: 30, lost: 3 }, 2000), 9.090909090909092);
assert.equal(updatePacketLossWindow(lossSamples, { received: 100, lost: 0 }, 15001), 0);

const bufferConfig = { initial: 0.8, min: 0.5, max: 1.2, enabled: true };
assert.equal(selectPrebufferSeconds("excellent", bufferConfig), 0.5);
assert.equal(selectPrebufferSeconds("good", bufferConfig), 0.8);
assert.equal(selectPrebufferSeconds("fair", bufferConfig), 1);
assert.equal(selectPrebufferSeconds("poor", bufferConfig), 1.2);

const candidates = summarizeIceCandidates([
  "a=candidate:1 1 udp 1 192.0.2.1 1234 typ host",
  "a=candidate:2 1 udp 1 198.51.100.1 2345 typ srflx",
  "a=candidate:3 1 udp 1 203.0.113.1 3456 typ relay",
].join("\r\n"));
assert.deepEqual(candidates, { host: 1, srflx: 1, relay: 1, prflx: 0, total: 3 });

console.log("Frontend network tests passed");
