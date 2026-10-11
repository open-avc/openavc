// Who holds a paused device, in the words the device page and its status dot
// use. `device.<id>.paused_for` names the holder: "audit" for a Device Audit,
// "live_test" for the Driver Builder's Live Test.

export function pausedTitle(pausedFor: unknown): string {
  if (pausedFor === "audit") return "Paused for a Device Audit";
  if (pausedFor === "live_test") return "Paused for Live Test";
  return "Paused";
}

export function pausedBannerText(pausedFor: unknown): string {
  if (pausedFor === "audit") {
    return "Paused for a Device Audit. It reconnects when the audit finishes.";
  }
  if (pausedFor === "live_test") {
    return "Paused for Live Test in the Driver Builder. It reconnects when you leave the Test tab.";
  }
  return "Paused. Press Resume to reconnect it.";
}
