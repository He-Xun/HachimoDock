// From pc/: npm ci, npx playwright install chromium, then npm run dev:web.
// In another terminal: node scripts/test-card-hover-browser.mjs
// PET_TEST_URL selects a local Vite origin; PET_TEST_BROWSER overrides Chromium.
// The community iframe and desktop IPC use local fixtures.
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const base = process.env.PET_TEST_URL || "http://127.0.0.1:4173";
assert.equal(new URL(base).hostname, "127.0.0.1");
const browser = await chromium.launch({ executablePath: process.env.PET_TEST_BROWSER });
try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 1000 } });
  page.setDefaultTimeout(6500);
  const community = new URL("../../server/community/web/", import.meta.url);
  await page.route("**/*", async route => {
    const url = new URL(route.request().url());
    if (url.origin !== base) return route.abort();
    if (url.pathname === "/__hover-gallery") {
      return route.fulfill({ contentType: "text/html", body: '<!doctype html><meta charset="utf-8"><div class="app-shell"><div id="root"></div></div>' });
    }
    if (url.pathname.startsWith("/community/")) {
      const file = url.pathname.slice(11) || "index.html";
      const types = { "index.html": "text/html", "app.js": "text/javascript", "feature-view.mjs": "text/javascript", "style.css": "text/css" };
      if (!Object.hasOwn(types, file)) return route.abort();
      return route.fulfill({ contentType: types[file], body: await readFile(new URL(file, community)) });
    }
    return route.continue();
  });

  async function desktopTheme() {
    await page.addStyleTag({ path: fileURLToPath(new URL("../src/companion-theme.css", import.meta.url)) });
    await page.addStyleTag({ content: "*, *::before, *::after { transition-duration: 0s !important; }" });
  }
  const paint = target => target.evaluate(node => [null, "::before", "::after"].map(pseudo => {
    const s = getComputedStyle(node, pseudo);
    return [s.backgroundColor, s.backgroundImage, s.color, s.borderColor, s.boxShadow, s.opacity];
  }));
  async function stable(hit, observed = [hit]) {
    await hit.scrollIntoViewIfNeeded();
    await page.mouse.move(0, 0);
    const before = await Promise.all(observed.map(paint));
    await hit.hover();
    assert.equal(await hit.evaluate(node => node.matches(":hover")), true, hit.toString());
    assert.deepEqual(await Promise.all(observed.map(paint)), before, hit.toString());
  }
  async function actionChanges(button) {
    await button.scrollIntoViewIfNeeded();
    await page.mouse.move(0, 0);
    const before = await paint(button);
    await button.hover();
    assert.notDeepEqual(await paint(button), before, button.toString());
  }

  await page.goto(base + "/tests/component-center-review.html");
  await page.locator("#root").evaluate(root => root.classList.add("app-shell"));
  await desktopTheme();
  const card = page.locator(".candidate-card").filter({ has: page.getByRole("button", { name: "查看 回归测试组件 详情", exact: true }) });
  const select = card.locator(".candidate-card__select");
  for (const hit of [card.locator(".candidate-card__preview"), card.locator(".candidate-card__name")]) {
    await stable(hit, [card, select, hit]);
  }
  await card.evaluate(node => node.classList.add("candidate-card--enabled"));
  await stable(select, [card, select]);
  await page.keyboard.press("Tab");
  await select.focus();
  assert(await select.evaluate(n => n.matches(":focus-visible") && getComputedStyle(n).outlineStyle !== "none"));
  await actionChanges(card.locator("footer button:enabled").first());
  await stable(page.locator(".candidate-card--create"));

  await page.evaluate(() => {
    const native = window.__TAURI_INTERNALS__.invoke;
    window.__TAURI_INTERNALS__.invoke = (cmd, args) =>
      cmd === "community_request" && args.input.action === "rankings"
        ? Promise.resolve({ items: [{ id: "review-community", name: "榜单回归", rankValue: 2 }] })
        : native(cmd, args);
  });
  await page.getByRole("tab", { name: "社区广场", exact: true }).click();
  const frame = page.frameLocator('iframe[title="哈基米社区网页"]');
  const open = frame.locator(".card-open").first();
  await stable(open.locator(".cover"), [open, open.locator(".card-copy")]);
  await stable(open.locator(".card-copy"), [open, open.locator(".card-copy")]);
  await stable(frame.locator(".rank").first());
  await actionChanges(frame.locator(".bookmark").first());

  await page.goto(base + "/__hover-gallery");
  await page.evaluate(async () => {
    const refresh = (await import("/@react-refresh")).default;
    refresh.injectIntoGlobalHook(window);
    window.$RefreshReg$ = () => {};
    window.$RefreshSig$ = () => type => type;
    window.__vite_plugin_react_preamble_installed__ = true;
    window.__TAURI_INTERNALS__ = { invoke: async cmd => {
      if (cmd === "plugin:fs|read_dir") return [];
      if (cmd === "plugin:path|resolve_directory") return "/fixture";
      if (cmd === "plugin:fs|read_text_file") return new TextEncoder().encode("{}");
      if (cmd === "load_voice_chat_settings") return {};
      return null;
    }};
    await import("/src/styles.css");
    (await import("/scripts/fixtures/persona-gallery.jsx")).mount();
    window.openGallery();
  });
  await desktopTheme();
  await page.getByRole("button", { name: "关闭引导" }).click();
  const appearance = page.locator(".appearance-card--clickable").first();
  for (const hit of [appearance.locator(".appearance-card-preview"), appearance.locator(".appearance-card-main")]) {
    await stable(hit, [appearance, hit]);
  }
  await appearance.evaluate(node => node.classList.add("is-active", "appearance-card--active"));
  await stable(appearance.locator(".appearance-card-main"), [appearance, appearance.locator(".appearance-card-main")]);
  await page.getByRole("button", { name: "新建自定义形象" }).click();
  const dialog = page.getByRole("dialog");
  await stable(dialog.locator(".creation-source-card").first());
  await actionChanges(dialog.locator(".icon-btn").first());
  console.log("PASS gallery and component covers, card bodies, active states, keyboard focus, nested actions and embedded community rankings");
} finally {
  await browser.close();
}
