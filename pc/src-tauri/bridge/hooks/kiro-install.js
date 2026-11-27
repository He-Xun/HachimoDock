#!/usr/bin/env node
"use strict";

// Kiro CLI hook installer.
// 1. Writes a launcher script (~/.kiro/hooks/hachimodock-kiro.cmd on Windows,
//    hachimodock-kiro.js elsewhere) that pipes Kiro's hook stdin into kiro-hook.js.
//    The launcher path has no spaces, so Kiro Crew's hook settings accept it.
// 2. Registers that launcher in user-owned ~/.kiro/agents/*.json files.
//    Kiro Crew's generated specs (kirocrew*.json) are rebuilt on every gateway
//    start, so they are skipped; add the launcher in Kiro Crew's Hooks settings.
// Skips silently when ~/.kiro does not exist. Idempotent.

const fs = require("fs");
const os = require("os");
const path = require("path");
const { resolveNodeBin } = require("./server-config");

const MARKER = "hachimodock-kiro";
const KIRO_HOOK_EVENTS = ["agentSpawn", "userPromptSubmit", "preToolUse", "postToolUse", "stop"];
const TOOL_EVENTS = new Set(["preToolUse", "postToolUse"]);

function resolveKiroDir(options = {}) {
  const env = options.env || process.env;
  const home = options.home || env.HOME || env.USERPROFILE || os.homedir();
  return path.join(path.resolve(home), ".kiro");
}

function writeAtomic(filePath, text) {
  const dir = path.dirname(filePath);
  const tmpPath = path.join(dir, `.${path.basename(filePath)}.${process.pid}.${Date.now()}.tmp`);
  fs.mkdirSync(dir, { recursive: true });
  try {
    fs.writeFileSync(tmpPath, text, "utf-8");
    fs.renameSync(tmpPath, filePath);
  } catch (err) {
    try { fs.unlinkSync(tmpPath); } catch {}
    throw err;
  }
}

function launcherContent(nodeBin, hookScript, isWindows) {
  if (isWindows) {
    return `@echo off\r\n"${nodeBin}" "${hookScript}"\r\nexit /b 0\r\n`;
  }
  return `#!/bin/sh\nexec "${nodeBin}" "${hookScript}"\n`;
}

function registerKiroHooks(options = {}) {
  const kiroDir = options.kiroDir || resolveKiroDir(options);
  if (!options.force && !fs.existsSync(kiroDir)) {
    return { added: 0, updated: 0, skipped: 1, reason: "kiro-not-found" };
  }
  const isWindows = options.isWindows !== undefined ? options.isWindows : process.platform === "win32";
  // Prefer the node that runs this installer (the bridge's bundled runtime):
  // Kiro's hook environment may not have the user's node on PATH.
  const resolvedNode = options.nodeBin !== undefined ? options.nodeBin : resolveNodeBin();
  const nodeBin = resolvedNode && path.isAbsolute(resolvedNode) ? resolvedNode : process.execPath;
  const hookScript = path.resolve(__dirname, "kiro-hook.js").replace("app.asar" + path.sep, "app.asar.unpacked" + path.sep);
  // ".sh" would be auto-imported by Kiro Crew as a preToolUse hook, so the
  // POSIX launcher uses a neutral name and is referenced explicitly.
  const launcherPath = path.join(kiroDir, "hooks", isWindows ? `${MARKER}.cmd` : `${MARKER}-launcher`);

  let added = 0;
  let updated = 0;
  let skipped = 0;

  const launcher = launcherContent(nodeBin, hookScript, isWindows);
  let previousLauncher = null;
  try { previousLauncher = fs.readFileSync(launcherPath, "utf-8"); } catch {}
  if (previousLauncher !== launcher) {
    writeAtomic(launcherPath, launcher);
    if (!isWindows) fs.chmodSync(launcherPath, 0o755);
    if (previousLauncher) updated++; else added++;
  } else {
    skipped++;
  }

  const command = `"${launcherPath}"`;
  const agentsDir = path.join(kiroDir, "agents");
  let agentFiles = [];
  try {
    agentFiles = fs.readdirSync(agentsDir)
      .filter((name) => name.endsWith(".json") && !name.startsWith("kirocrew"));
  } catch {}

  for (const name of agentFiles) {
    const filePath = path.join(agentsDir, name);
    let spec;
    try {
      spec = JSON.parse(fs.readFileSync(filePath, "utf-8"));
    } catch {
      continue;
    }
    if (!spec || typeof spec !== "object" || Array.isArray(spec)) continue;
    if (!spec.hooks || typeof spec.hooks !== "object") spec.hooks = {};
    let changed = false;
    for (const event of KIRO_HOOK_EVENTS) {
      if (!Array.isArray(spec.hooks[event])) spec.hooks[event] = [];
      const entries = spec.hooks[event];
      const existing = entries.find((entry) => entry && typeof entry.command === "string"
        && (entry.command.includes(MARKER) || entry.command.includes("kiro-hook.js")));
      const desired = TOOL_EVENTS.has(event) ? { matcher: "*", command } : { command };
      if (existing) {
        if (existing.command !== command) {
          existing.command = command;
          changed = true;
        }
        continue;
      }
      entries.push(desired);
      changed = true;
    }
    if (changed) {
      writeAtomic(filePath, JSON.stringify(spec, null, 2));
      updated++;
    } else {
      skipped++;
    }
  }

  return { added, updated, skipped, launcherPath, agentFiles };
}

module.exports = { registerKiroHooks, KIRO_HOOK_EVENTS };

if (require.main === module) {
  try {
    console.log(JSON.stringify(registerKiroHooks({}), null, 2));
  } catch (err) {
    console.error(err.message);
    process.exit(1);
  }
}
