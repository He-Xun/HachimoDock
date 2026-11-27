#!/usr/bin/env node
// Clawd — Kiro CLI hook (stdin JSON with hook_event_name, camelCase).
// Prints nothing on stdout: for agentSpawn/userPromptSubmit stdout is injected
// into the model context, and for preToolUse exit 0 means "allow".
// Always exits 0 so a missing desktop app never blocks a tool call.

const { postStateToRunningServer } = require("./server-config");

// Kiro hook event (lower-cased) → { state, event } for the Clawd state machine
const HOOK_MAP = {
  agentspawn:       { state: "idle",      event: "SessionStart" },
  userpromptsubmit: { state: "working",   event: "UserPromptSubmit" },
  pretooluse:       { state: "working",   event: "PreToolUse" },
  posttooluse:      { state: "working",   event: "PostToolUse" },
  stop:             { state: "attention", event: "Stop" },
};

let _ran = false;

function finishOnce(payload) {
  if (_ran) return;
  _ran = true;

  const hookName = String((payload && payload.hook_event_name) || "").toLowerCase();
  const mapped = HOOK_MAP[hookName];
  if (!mapped) process.exit(0);

  // Without a title the device falls back to the raw agent id ("kiro-cli").
  const body = {
    state: mapped.state,
    event: mapped.event,
    session_id: (payload && payload.session_id) || "default",
    session_title: "Kiro",
    session_title_explicit: false,
    display_title: "Kiro",
    agent_id: "kiro-cli",
  };
  if (mapped.state === "working" && payload && payload.tool_name) {
    body.tool_name = String(payload.tool_name).slice(0, 100);
  }
  if (payload && payload.cwd) body.cwd = payload.cwd;
  body.source_pid = process.ppid;

  postStateToRunningServer(JSON.stringify(body), { timeoutMs: 100 }, () => process.exit(0));
}

const chunks = [];
process.stdin.on("data", (c) => chunks.push(c));
process.stdin.on("end", () => {
  let payload = {};
  try {
    const raw = Buffer.concat(chunks).toString();
    if (raw.trim()) payload = JSON.parse(raw);
  } catch {
    payload = {};
  }
  finishOnce(payload);
});
setTimeout(() => finishOnce({}), 400);
