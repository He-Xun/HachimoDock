export function wifiDeviceControlReady(wifi) {
  return wifi?.panelControl === true && wifi.controlTrusted === true
    && typeof wifi.boardDeviceId === "string" && wifi.boardDeviceId.length > 0
    && /^[0-9a-f]{16}$/i.test(wifi.bootId || "");
}

export function wifiDeviceControl(invoke, wifi, operation, args = {}) {
  if (!wifiDeviceControlReady(wifi)) return Promise.reject(new Error("当前设备未开放这项 Wi-Fi 控制。"));
  return invoke("lan_panel_control", { ...args, boardDeviceId: wifi.boardDeviceId, expectedBootId: wifi.bootId, operation });
}
