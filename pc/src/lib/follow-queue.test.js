import test from "node:test";
import assert from "node:assert/strict";
import { updateFollowQueue } from "./follow-queue.js";

test("adding preserves focus, the three slots cycle in insertion order and stale directions are ignored", () => {
  const initial = { enabledAgents: new Set(["workbuddy"]), activeAgentId: "workbuddy" };
  const two = updateFollowQueue(initial, "enable", "cursor-agent");
  const three = updateFollowQueue(two, "enable", "codex");
  assert.equal(three.activeAgentId, "workbuddy");
  assert.deepEqual([...three.enabledAgents], ["workbuddy", "cursor-agent", "codex"]);
  assert.throws(() => updateFollowQueue(three, "enable", "mimocode"), /最多/);
  const cycle = (state, origin, direction) => updateFollowQueue(state, "select", "", {origin, direction});
  const prev = cycle(three, "workbuddy", -1);
  assert.equal(prev.activeAgentId, "codex");
  assert.equal(cycle(prev, "workbuddy", -1), prev);
  assert.equal(cycle(prev, "codex", 1).activeAgentId, "workbuddy");
  assert.equal(updateFollowQueue(prev, "select", "mimocode"), prev);
  assert.equal(updateFollowQueue(prev, "remove", "codex").activeAgentId, "workbuddy");
  assert.throws(() => updateFollowQueue(initial, "remove", "workbuddy"), /至少/);
  assert.equal(cycle(initial, "workbuddy", 1), initial);
});

test("recovering a failed focus probe is non-forced and membership changes reload monitors", async () => {
  const { ensureFollowQueueRuntime } = await import("./follow-queue.js");
  const calls = [];
  const ensure = async force => { calls.push(force); return { running: true }; };
  await ensureFollowQueueRuntime("select", async () => { throw new Error("offline"); }, ensure);
  for (const operation of ["enable", "remove"]) {
    await ensureFollowQueueRuntime(operation, () => assert.fail("unexpected probe"), ensure);
  }
  assert.deepEqual(calls, [false, true, true]);
});

test("USB navigation with no session label uses only the current native connection", async () => {
  const { usbNavigationOrigin } = await import("./follow-queue.js");
  const scope = { boardDeviceId: "board", bootId: "boot", connectionId: "9" };
  const usb = { ...scope, connected: true };
  const row = { ...scope, _nativeUsbScope: scope };
  assert.equal(usbNavigationOrigin(row, usb, "workbuddy"), "workbuddy");
  assert.equal(usbNavigationOrigin({ ...row, agentId: "codex" }, usb, "workbuddy"), "codex");
  for (const field of Object.keys(scope)) {
    assert.equal(usbNavigationOrigin(row, { ...usb, [field]: "old" }, "workbuddy"), null);
  }
  assert.equal(usbNavigationOrigin({ ...row, _nativeUsbScope: null }, usb, "workbuddy"), null);
});

test("the first WorkBuddy switch payload carries the task or the unexpired reply", async () => {
  const { workbuddyFollowSnapshot } = await import("./follow-queue.js");
  const { p4SessionActivitySignature } = await import("./p4-session-service.js");
  const row = { id: "s", name: "音频连接", displayContent: "北京攻略", state: "working", statusUpdatedAt: 1000 };
  const active = workbuddyFollowSnapshot([row], {}, 2000);
  assert.deepEqual(active.activeSessionIds, ["s"]);
  assert.equal(active.sessions[0].content, "北京攻略");
  assert.equal(active.sessions[0].title, "音频连接");
  assert.equal(workbuddyFollowSnapshot([row], { s: p4SessionActivitySignature(row) }, 2000).count, 0);
  const done = { ...row, state: "done", displayContent: "已完成" };
  assert.equal(workbuddyFollowSnapshot([done], {}, 60000).sessions[0].terminalRemainingMs, 1000);
  assert.equal(workbuddyFollowSnapshot([done], {}, 61000).count, 0);
});
