import assert from "node:assert/strict";

import {
  classifyNetworkQuality,
  applyMicrophoneTrack,
  selectPrebufferSeconds,
  summarizeIceCandidates,
  updatePacketLossWindow,
} from "../server/app/static/rtc.js";
import { handleBridgeEvent } from "../server/app/static/events.js";

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

const microphoneTrack = { enabled: true };
const replacements = [];
const sender = { replaceTrack: async (track) => replacements.push(track) };
await applyMicrophoneTrack(sender, microphoneTrack, true);
assert.equal(microphoneTrack.enabled, false);
assert.deepEqual(replacements, [null]);
await applyMicrophoneTrack(sender, microphoneTrack, false);
assert.equal(microphoneTrack.enabled, true);
assert.deepEqual(replacements, [null, microphoneTrack]);

const renderedAnswers = [];
const discardedTurns = [];
const eventState = {
  currentTurnId: null,
  currentTranscript: "pending",
  pendingTurn: true,
  isMuted: true,
  turnAnswers: new Map(),
};
const eventUi = {
  setTurnAnswer: (_turnId, text) => renderedAnswers.push(text),
  discardTurn: (turnId) => discardedTurns.push(turnId),
  setStatus: () => {},
  setDebug: () => {},
};
handleBridgeEvent(JSON.stringify({ type: "answer_delta", turn_id: "turn-1", text: "\n\nAnswer" }), {
  state: eventState,
  ui: eventUi,
});
assert.deepEqual(renderedAnswers, ["Answer"]);
handleBridgeEvent(JSON.stringify({ type: "transcript_discarded", turn_id: "turn-1" }), {
  state: eventState,
  ui: eventUi,
});
assert.deepEqual(discardedTurns, ["turn-1"]);
assert.equal(eventState.pendingTurn, false);

console.log("Frontend network tests passed");
