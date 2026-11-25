import { buildP4ConversationQueue, filterDismissedP4Sessions, p4SessionDisplayTitle } from "./p4-session-service.js";
import { deviceSessionTerminalRemainingMs, deviceSessionTransitionRevision, isActiveDeviceSession } from "./session-display.js";

export function workbuddyFollowSnapshot(rows, dismissed, now = Date.now()) {
  const visible = buildP4ConversationQueue(filterDismissedP4Sessions(rows, dismissed), [], now,
    { authoritativeSnapshot: true, restoreRecentTerminal: true });
  const sessions = visible.map(row => ({
    id: row.id, title: p4SessionDisplayTitle(row), content: row.displayContent || "", state: row.state,
    transitionRevision: deviceSessionTransitionRevision(row), terminalRemainingMs: deviceSessionTerminalRemainingMs(row, now),
  }));
  return { agentId: "workbuddy", sessionId: "auto", title: sessions[0]?.title || "", count: sessions.length,
    index: sessions.length ? 1 : 0, displayEnabled: sessions.length > 0, sessions,
    activeSessionIds: visible.filter(isActiveDeviceSession).map(row => row.id) };
}

export function usbNavigationOrigin(row, usb, active) {
  const scope = row?._nativeUsbScope;
  if (!usb?.connected || !scope?.connectionId || scope.connectionId !== usb.connectionId
      || scope.boardDeviceId !== usb.boardDeviceId || scope.bootId !== usb.bootId
      || row.boardDeviceId !== scope.boardDeviceId || row.bootId !== scope.bootId) return null;
  return row.agentId || active || null;
}

export function updateFollowQueue(current, operation, id, intent) {
  const enabled = new Set(current.enabledAgents);
  let active = current.activeAgentId;
  if (intent) {
    if (intent.origin !== active || ![-1, 1].includes(intent.direction) || !enabled.has(active)) return current;
    const order = [...enabled];
    id = order[(order.indexOf(active) + intent.direction + order.length) % order.length];
  }
  if (!id) return current;
  switch (operation) {
    case "select":
      if (!enabled.has(id) || active === id) return current;
      active = id;
      break;
    case "enable":
      if (enabled.has(id)) return current;
      if (enabled.size >= 3) throw new Error("最多跟随 3 个 Agent");
      enabled.add(id);
      break;
    case "remove":
      if (!enabled.delete(id)) return current;
      if (!enabled.size) throw new Error("至少保留一个 Agent");
      if (active === id) active = enabled.values().next().value;
      break;
    default:
      return current;
  }
  return { enabledAgents: enabled, activeAgentId: active };
}

export async function ensureFollowQueueRuntime(operation, probe, ensure) {
  // Focus is routed by the device binding. The bridge owns in-flight sessions
  // for the whole queue and only needs new monitors when membership changes.
  if (operation === "select") {
    const status = await probe().catch(() => null);
    if (status?.ok) return;
  }
  const runtime = await ensure(operation !== "select");
  if (!runtime?.running) throw new Error("跟随配置已保存，本地桥接服务尚未就绪。");
}
