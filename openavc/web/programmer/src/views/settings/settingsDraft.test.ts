import { describe, it, expect } from "vitest";
import { changeNeedsRestart } from "./settingsDraft";

describe("changeNeedsRestart", () => {
  it.each([
    ["network", "http_port", true],
    ["network", "port80_redirect", true],
    ["tls", "enabled", true],
    ["tls", "redirect_http", true],
    ["network", "accept_connections_from", false],
    ["network", "reach_devices_through", false],
    ["logging", "level", false],
    ["kiosk", "enabled", false],
    ["panels", "access", false],
  ])("%s.%s: %s", (section, key, expected) => {
    expect(changeNeedsRestart(section, key)).toBe(expected);
  });
});
