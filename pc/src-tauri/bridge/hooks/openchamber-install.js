"use strict";

// Install the Pet Manager plugin into OpenCode's global plugin directory so
// OpenChamber (a desktop/web UI that drives OpenCode) reports its sessions.
// Skips silently when OpenChamber is not installed.

const fs = require("fs");
const os = require("os");
const path = require("path");

const MANAGED_PLUGIN_FILE_NAME = "pet-manager-openchamber.js";

function resolveHome(options = {}) {
  const env = options.env || process.env;
  return path.resolve(options.home || env.HOME || env.USERPROFILE || os.homedir());
}

function resolveOpenChamberDir(options = {}) {
  return path.join(resolveHome(options), ".config", "openchamber");
}

function resolveOpenCodeConfigDir(options = {}) {
  const env = options.env || process.env;
  if (env.OPENCODE_CONFIG_DIR) return path.resolve(env.OPENCODE_CONFIG_DIR);
  if (env.XDG_CONFIG_HOME) return path.join(path.resolve(env.XDG_CONFIG_HOME), "opencode");
  return path.join(resolveHome(options), ".config", "opencode");
}

function syncOpenChamberPlugin(options = {}) {
  const openChamberDir = options.openChamberDir || resolveOpenChamberDir(options);
  if (!options.force && !fs.existsSync(openChamberDir)) {
    return { added: 0, updated: 0, removed: 0, skipped: 1, reason: "openchamber-not-found" };
  }
  const sourcePath = options.sourcePath
    || path.join(__dirname, "openchamber-pet-manager-plugin.js");
  const pluginDir = path.join(options.configDir || resolveOpenCodeConfigDir(options), "plugins");
  const targetPath = path.join(pluginDir, MANAGED_PLUGIN_FILE_NAME);
  const source = Buffer.from(fs.readFileSync(sourcePath, "utf8").replace(
    JSON.stringify("__HACHIMO_CAPABILITY_MODULE__"),
    JSON.stringify(require.resolve("../packages/agent-session-bus/src/local-capability")),
  ));
  let previous = null;
  try {
    previous = fs.readFileSync(targetPath);
  } catch {}
  if (previous && Buffer.compare(previous, source) === 0) {
    return { added: 0, updated: 0, removed: 0, skipped: 1, targetPath };
  }

  fs.mkdirSync(pluginDir, { recursive: true });
  const tempPath = path.join(pluginDir, `.${MANAGED_PLUGIN_FILE_NAME}.${process.pid}.${Date.now()}.tmp`);
  fs.writeFileSync(tempPath, source);
  try {
    fs.renameSync(tempPath, targetPath);
  } catch (error) {
    try { fs.unlinkSync(targetPath); } catch {}
    try {
      fs.renameSync(tempPath, targetPath);
    } catch {
      try { fs.unlinkSync(tempPath); } catch {}
      throw error;
    }
  }
  return { added: previous ? 0 : 1, updated: previous ? 1 : 0, removed: 0, skipped: 0, targetPath };
}

module.exports = {
  MANAGED_PLUGIN_FILE_NAME,
  resolveOpenCodeConfigDir,
  syncOpenChamberPlugin,
};

if (require.main === module) {
  try {
    console.log(JSON.stringify(syncOpenChamberPlugin({})));
  } catch (err) {
    console.error(err.message);
    process.exit(1);
  }
}
