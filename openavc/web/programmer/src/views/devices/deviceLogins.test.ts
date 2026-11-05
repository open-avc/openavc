import { beforeEach, describe, expect, it, vi } from "vitest";

/**
 * Entering a device's login: the save goes through the device's own settings
 * save, built from the server's copy of the device, and the store is re-synced
 * from the server afterwards, so this tab never keeps the value typed. Plus the
 * driver's default and the sentence a restore, an open or an import shows.
 */

const MARKER = "<saved on this system>";

const mocks = vi.hoisted(() => ({
  getProject: vi.fn(),
  updateDevice: vi.fn(async () => ({ status: "updated", device_id: "proj1" })),
  syncDeviceFromServer: vi.fn(async () => {}),
}));

vi.mock("../../api/restClient", () => ({
  getProject: mocks.getProject,
  updateDevice: mocks.updateDevice,
}));
vi.mock("../../store/projectStore", () => ({
  syncDeviceFromServer: mocks.syncDeviceFromServer,
}));

import {
  driverLoginDefaults,
  holdsTypedLogin,
  keepSavedLogins,
  needsLoginSentence,
  SAVED_LOGIN,
  saveDeviceLogins,
  withoutSavedLogins,
} from "./deviceLogins";

describe("saving a device's login", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.getProject.mockResolvedValue({
      devices: [{ id: "proj1", driver: "acme_widget", name: "Projector", config: { mode: "eco", api_key: MARKER } }],
      connections: { proj1: { host: "192.0.2.10", password: MARKER } },
    });
  });

  it("sends the server's copy of the device's settings with the login typed in", async () => {
    await saveDeviceLogins("proj1", { password: "Hunter2-Acme" });
    expect(mocks.updateDevice).toHaveBeenCalledWith("proj1", {
      config: { mode: "eco", api_key: MARKER, password: "Hunter2-Acme" },
    });
  });

  it("re-syncs this tab from the server, never from what was typed", async () => {
    await saveDeviceLogins("proj1", { api_key: "k-123456" });
    expect(mocks.syncDeviceFromServer).toHaveBeenCalledWith("proj1");
    expect(JSON.stringify(mocks.syncDeviceFromServer.mock.calls)).not.toContain("k-123456");
  });

  it("refuses a device the project no longer has, and sends nothing", async () => {
    await expect(saveDeviceLogins("gone1", { password: "x" })).rejects.toThrow(/no longer has/);
    expect(mocks.updateDevice).not.toHaveBeenCalled();
  });
});

describe("the driver's default", () => {
  it("is what its default_config gives, for the settings that wait", () => {
    const info = { default_config: { port: 23, password: "acme-factory", community: "" } };
    expect(driverLoginDefaults(info, ["password", "community", "api_key"])).toEqual({
      password: "acme-factory",
    });
  });

  it("is nothing for a driver that ships none", () => {
    expect(driverLoginDefaults({}, ["password"])).toEqual({});
    expect(driverLoginDefaults(undefined, ["password"])).toEqual({});
  });
});

describe("what a restore says about the devices that wait", () => {
  it("says nothing when none do", () => {
    expect(needsLoginSentence([])).toBe("");
    expect(needsLoginSentence(undefined)).toBe("");
  });

  it("names one", () => {
    expect(needsLoginSentence([{ id: "proj1", name: "Projector" }])).toBe(
      "1 device needs its login entered: Projector. Open it in Devices to enter it.",
    );
  });

  it("names several, and counts the rest past five", () => {
    expect(needsLoginSentence([
      { id: "a", name: "Projector" }, { id: "b", name: "WattBox" },
    ])).toBe(
      "2 devices need their login entered: Projector, WattBox. Open each one in Devices to enter it.",
    );
    const seven = ["A", "B", "C", "D", "E", "F", "G"].map((n) => ({ id: n.toLowerCase(), name: n }));
    expect(needsLoginSentence(seven)).toBe(
      "7 devices need their login entered: A, B, C, D, E and 2 more. Open each one in Devices to enter it.",
    );
  });
});

describe("the marker in a form", () => {
  it("is the server's marker", () => {
    expect(SAVED_LOGIN).toBe(MARKER);
  });

  it("goes back for a saved login left blank after Change, and only for one", () => {
    const stored = { host: "192.0.2.10", password: MARKER, api_key: MARKER, community: "" };
    const form = { host: "192.0.2.10", password: "", api_key: "New-Key-1", community: "" };
    expect(keepSavedLogins(form, stored)).toEqual({
      host: "192.0.2.10",
      password: MARKER,
      api_key: "New-Key-1",
      community: "",
    });
  });

  it("goes back when the field was left out of the form altogether", () => {
    expect(keepSavedLogins({ host: "192.0.2.10" }, { password: MARKER })).toEqual({
      host: "192.0.2.10",
      password: MARKER,
    });
  });

  it("is left out of a copy, whose logins are saved under the device it came from", () => {
    expect(withoutSavedLogins({ host: "192.0.2.10", password: MARKER, mode: "eco" })).toEqual({
      host: "192.0.2.10",
      mode: "eco",
    });
  });
});

describe("whether a config holds a login somebody typed", () => {
  const schema = { api_key: { type: "string", secret: true }, mode: { type: "string" } };

  it("is true for a secret with a value, the password by its name alone", () => {
    expect(holdsTypedLogin({ api_key: "Key-1" }, schema)).toBe(true);
    expect(holdsTypedLogin({ password: "Hunter2-Acme" }, {})).toBe(true);
  });

  it("is false for the marker, an empty box and a setting that is not a secret", () => {
    expect(holdsTypedLogin({ api_key: MARKER, password: MARKER }, schema)).toBe(false);
    expect(holdsTypedLogin({ api_key: "", mode: "eco" }, schema)).toBe(false);
  });
});
