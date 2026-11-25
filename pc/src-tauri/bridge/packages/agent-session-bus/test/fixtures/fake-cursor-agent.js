#!/usr/bin/env node
"use strict";

const fs = require("fs");
const args = process.argv.slice(2);

if (process.env.FAKE_CURSOR_ARGS_FILE) {
  fs.writeFileSync(process.env.FAKE_CURSOR_ARGS_FILE, JSON.stringify(args));
}

if (args.length === 1 && (args[0] === "--version" || args[0] === "-v")) {
  process.stdout.write(`${process.env.FAKE_CURSOR_VERSION || "2026.05.05-test"}\n`);
  process.exit(0);
}

if (args[0] === "create-chat") {
  process.stdout.write("chat-created-1\n");
  process.exit(0);
}

if (args.includes("-p")) {
  if (process.env.FAKE_CURSOR_CWD) fs.writeFileSync(process.env.FAKE_CURSOR_CWD, process.cwd());
  const prompt = fs.readFileSync(0, "utf8");
  if (!prompt.trim()) process.exit(2);
  if (process.env.FAKE_CURSOR_INPUT) fs.writeFileSync(process.env.FAKE_CURSOR_INPUT, prompt);
  const reply = process.env.FAKE_CURSOR_REPLY || "好的";
  let sid = "cursor-sid";
  const resumeIdx = args.indexOf("--resume");
  if (resumeIdx >= 0 && args[resumeIdx + 1]) sid = args[resumeIdx + 1];
  const emit = (obj) => process.stdout.write(JSON.stringify(obj) + "\n");
  emit({ type: "system", subtype: "init", session_id: sid });
  if (process.env.FAKE_CURSOR_FAIL === "1") {
    emit({ type: "error", error: { type: "rate_limit", message: "fake fail" } });
    process.exit(1);
  }
  emit({ type: "assistant", message: { content: [{ type: "text", text: reply }] } });
  emit({ type: "result", session_id: sid, stop_reason: "end_turn", usage: { input_tokens: 2, output_tokens: reply.length } });
  process.exit(0);
}

process.stderr.write(`fake-cursor-agent: unsupported args: ${JSON.stringify(args)}\n`);
process.exit(2);
