// Run against the Vite app so the real shell, sidebar and retained pages participate.
import assert from "node:assert/strict";
import { chromium } from "playwright";

const url = process.env.PET_TEST_URL || "http://127.0.0.1:4173";
assert.equal(new URL(url).hostname, "127.0.0.1");
const browser = await chromium.launch({ executablePath: process.env.PET_TEST_BROWSER });
try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 720 } });
  await page.route("**/*", route => new URL(route.request().url()).origin === new URL(url).origin
    ? route.continue() : route.abort());
  await page.goto(url);
  const openSettings = page.getByTitle("模型配置", { exact: true });
  const content = page.locator(".app-content");
  const overflow = () => content.evaluate(el => getComputedStyle(el).overflowY);
  await openSettings.waitFor();
  const otherPageOverflow = await overflow();
  await openSettings.click();

  const nav = page.getByRole("navigation", { name: "跳至模型配置项目" });
  const current = label => nav.getByRole("button", { name: label }).and(nav.locator('[aria-current="true"]')).waitFor();
  const pinned = async () => {
    const box = await nav.boundingBox();
    assert.ok(Math.abs(box.y) <= 1, `navigation top: ${box.y}`);
    assert.equal(await nav.evaluate(el => el.contains(document.elementFromPoint(
      el.getBoundingClientRect().left + 8, el.getBoundingClientRect().top + 8))), true);
  };
  const jump = async (label, id) => {
    await nav.getByRole("button", { name: label }).click();
    await current(label);
    assert.equal(await page.evaluate(() => document.activeElement.id), id);
    await pinned();
    const top = (await page.locator("#" + id).boundingBox()).y;
    const height = (await nav.boundingBox()).height;
    assert.ok(top >= height && top < page.viewportSize().height, `section heading at ${top}, navigation height ${height}`);
  };
  await current("语音识别与合成");
  await jump("语音识别与合成", "model-settings-speech");
  const speech = page.locator("#model-settings-speech");
  assert.ok((await speech.boundingBox()).height < 720, "speech fits inside one viewport");
  await page.mouse.move(700, 450);
  await page.mouse.wheel(0, Math.ceil((await speech.boundingBox()).height + 60));
  await current("对话大模型");
  await pinned();
  assert.ok(await page.evaluate(() => window.scrollY > 0));
  assert.equal(await content.evaluate(el => el.scrollTop), 0);

  await jump("形象生成", "model-settings-avatar");
  const desktopHeight = (await nav.boundingBox()).height;
  await page.setViewportSize({ width: 375, height: 720 });
  await pinned();
  assert.ok((await nav.boundingBox()).height > desktopHeight, "narrow navigation wraps");
  await jump("对话大模型", "model-settings-dialogue");
  await page.mouse.move(200, 450);
  await page.mouse.wheel(0, 10000);
  await current("云服务网络");
  await pinned();
  assert.ok(await page.evaluate(() => Math.abs(window.scrollY + innerHeight - document.documentElement.scrollHeight) <= 1));
  await page.mouse.wheel(0, -100);
  await current("形象生成");
  await jump("云服务网络", "model-settings-network");

  await page.setViewportSize({ width: 1280, height: 900 });
  await current("云服务网络");
  await pinned();
  await page.setViewportSize({ width: 1280, height: 1450 });
  await jump("形象生成", "model-settings-avatar");
  await jump("云服务网络", "model-settings-network");
  await page.getByTitle("其它设置", { exact: true }).click();
  await nav.waitFor({ state: "hidden" });
  await page.getByRole("heading", { name: "其它设置", exact: true }).waitFor();
  assert.equal(await overflow(), otherPageOverflow);
  await openSettings.click();
  await current("语音识别与合成");
  console.log("PASS model navigation: document wheel scrolling, all four jumps, focus, short sections, bottom, resize and page switching");
} finally {
  await browser.close();
}
