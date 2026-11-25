// Run against npm run dev:web. The dashboard, session owners and panel effects are real.
import assert from "node:assert/strict";
import { chromium } from "playwright";

const base = process.env.PET_TEST_URL || "http://127.0.0.1:4173";
assert.equal(new URL(base).hostname, "127.0.0.1");
const browser = await chromium.launch({ executablePath: process.env.PET_TEST_BROWSER });
try {
  for (const runtime of ["esp-p4", "linux"]) {
    const page = await browser.newPage({ userAgent: "Mozilla/5.0 (Macintosh; Intel Mac OS X)" });
    const errors = [];
    page.on("pageerror", error => errors.push(error.message));
    await page.route("**/*", route => {
      const url = new URL(route.request().url());
      if (url.origin !== base) return route.abort();
      if (url.pathname === "/__folded") return route.fulfill({ contentType: "text/html", body: '<div id="root"></div>' });
      return route.continue();
    });
    await page.goto(base + "/__folded");
    await page.clock.install();
    await page.evaluate(async runtime => {
      Object.defineProperty(navigator, "platform", { value: "MacIntel" });
      Object.defineProperty(navigator, "userAgentData", { value: { platform: "macOS" } });
      const refresh = (await import("/@react-refresh")).default;
      refresh.injectIntoGlobalHook(window);
      window.$RefreshReg$ = () => {};
      window.$RefreshSig$ = () => type => type;
      window.__vite_plugin_react_preamble_installed__ = true;
      const { API_CONFIGURATION_UPDATED_EVENT } = await import("/src/lib/api-configuration.js");
      const board = "p4-123456789abc";
      const callbacks = new Map(), listeners = new Map();
      let id = 0;
      const chat = { active: true, state: "listening" };
      const calls = [];
      window.fixture = {
        calls,
        asrConfigured: false,
        signal(event, payload) {
          for (const listener of listeners.values()) {
            if (listener.event === event) callbacks.get(listener.handler)({ payload });
          }
        },
        updateChat() {
          Object.assign(chat, { state: "speaking", reply: "Reply received while folded" });
          this.signal("realtime-chat-state", chat);
        },
        refreshSettings() {
          window.dispatchEvent(new Event("focus"));
          window.dispatchEvent(new Event(API_CONFIGURATION_UPDATED_EVENT));
        },
      };
      localStorage.setItem("pet-manager.device-guide-seen", "1");
      window.__TAURI_EVENT_PLUGIN_INTERNALS__ = { unregisterListener: (_, id) => listeners.delete(id) };
      window.__TAURI_INTERNALS__ = {
        transformCallback(fn) { callbacks.set(++id, fn); return id; },
        unregisterCallback(id) { callbacks.delete(id); },
        async invoke(command, args) {
          calls.push(command === "local_bridge_request" ? args.route.split("?")[0] : command);
          switch (command) {
            case "plugin:event|listen": listeners.set(++id, args); return id;
            case "usb_get_status": return { connected: true, runtime, boardDeviceId: board, bootId: "1111111111111111", connectionId: "1" };
            case "load_device_asr_settings": return { configured: fixture.asrConfigured };
            case "load_device_bindings": return [{ boardDeviceId: board }];
            case "list_appearances": return [];
            case "detect_local_agents": return { agents: [] };
            case "realtime_chat_status": return { ...chat };
            case "local_bridge_request": return { ok: true, agents: [], events: [], sessions: [], cursor: 0 };
            case "ensure_voice_runtime":
            case "ensure_bridge_runtime": return { running: true };
            default: return {};
          }
        },
      };
      const { mount } = await import("/scripts/tray-benchmark-fixture.jsx");
      mount("usb-dashboard", board);
    }, runtime);

    const toggle = name => page.locator("button[data-card-surface][aria-expanded]").filter({ hasText: name });
    const voice = toggle("语音输入"), chat = toggle("实时对话");
    const click = locator => locator.evaluate(el => el.click());
    const calls = () => page.evaluate(() => fixture.calls.slice());
    const reset = () => page.evaluate(() => { fixture.calls.length = 0; });
    const runtimeCommand = runtime === "linux" ? "ensure_voice_runtime" : "ensure_device_voice_runtime";
    await page.waitForFunction(cmd => fixture.calls.includes(cmd), runtimeCommand);
    await click(page.locator(".voice-panel__advanced--diagnostics summary"));
    await page.locator("#voice-mock-inject-input").fill("draft before folding");
    await click(voice);
    await click(chat);
    assert.equal(await voice.getAttribute("aria-expanded"), "false");
    assert.equal(await chat.getAttribute("aria-expanded"), "false");
    assert.equal(await page.locator("button[data-card-surface][aria-expanded=false]").count(), 2);
    await reset();
    await page.evaluate(() => {
      fixture.asrConfigured = true;
      fixture.updateChat();
      fixture.signal("voice-transcript", {
        phase: "error", text: "PTT while folded", utteranceId: "fold-test", revision: 1,
        ok: false, agentId: "codex", composerError: "macOS 辅助功能",
      });
      fixture.refreshSettings();
    });
    await page.clock.runFor(11000);
    const folded = await calls();
    for (const cmd of ["load_device_asr_settings", "load_voice_chat_settings", "check_codex_accessibility_permission",
      "realtime_chat_start", "realtime_chat_stop", "audio_bridge_signal", "/agent/status", "ensure_device_voice_runtime"]) {
      assert.ok(!folded.includes(cmd), runtime + " folded: " + cmd);
    }
    assert.ok(folded.includes("/agent/session-events"));
    assert.ok(folded.includes("realtime_chat_status"));
    if (runtime === "linux") assert.ok(folded.includes("ensure_voice_runtime"));

    await reset();
    await click(voice);
    await page.waitForFunction(() => fixture.calls.includes("load_device_asr_settings") && fixture.calls.includes("/agent/status"));
    await page.getByText("语音识别已配置", { exact: true }).waitFor();
    assert.ok((await calls()).includes("check_codex_accessibility_permission"));
    if (runtime === "esp-p4") assert.ok((await calls()).includes(runtimeCommand));
    assert.ok(!(await calls()).includes("load_voice_chat_settings"));
    assert.equal(await page.locator(".voice-panel__advanced--diagnostics").evaluate(el => el.open), true);
    assert.equal(await page.locator("#voice-mock-inject-input").inputValue(), "draft before folding");
    await page.getByText("PTT while folded", { exact: true }).waitFor();
    await reset();
    await click(chat);
    await page.waitForFunction(() => fixture.calls.includes("load_voice_chat_settings") && fixture.calls.includes("load_device_asr_settings"));
    await page.getByRole("button", { name: "结束聊天" }).waitFor();
    await page.getByText("Reply received while folded", { exact: false }).last().waitFor();
    assert.deepEqual(errors, []);
    await page.close();
    console.log("PASS folded panels, session updates and reopen refresh (" + runtime + ")");
  }
} finally {
  await browser.close();
}
