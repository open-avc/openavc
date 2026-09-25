import { describe, it, expect, vi, beforeEach } from "vitest";

// The panel devices store: what the notice and the Dashboard's Panels card
// read. Fed by one fetch on connect and by the `panel.devices.changed` push,
// which carries the same record the fetch does; each reason has to leave the
// three lists the way the server's own list would look.

vi.mock("../api/restClient", () => ({
  listPanelDevices: vi.fn(),
  approvePanelDevice: vi.fn(),
  denyPanelDevice: vi.fn(),
  revokePanelDevice: vi.fn(),
  renamePanelDevice: vi.fn(),
}));
vi.mock("./toastStore", () => ({ showError: vi.fn() }));

import * as api from "../api/restClient";
import type { PanelDevice } from "../api/restClient";
import { showError } from "./toastStore";
import { usePanelDevicesStore } from "./panelDevicesStore";

const ipad: PanelDevice = {
  id: "pd_ipad", status: "pending", name: "", platform: "iPad", address: "10.1.1.50",
  first_seen: "2026-09-25T14:02:11Z", last_seen: "2026-09-25T14:02:11Z", code: "482-915",
};
const phone: PanelDevice = {
  id: "pd_phone", status: "pending", name: "", platform: "Android phone", address: "10.1.1.51",
  first_seen: "2026-09-25T14:01:00Z", last_seen: "2026-09-25T14:01:00Z", code: "107-220",
};
const wall: PanelDevice = {
  id: "pd_wall", status: "approved", name: "Room 101 wall", platform: "Android tablet",
  address: "10.1.1.60", first_seen: "2026-09-20T09:00:00Z", last_seen: "2026-09-25T13:00:00Z",
  approved_at: "2026-09-20T09:01:00Z", approved_by: "admin",
};

function reset() {
  usePanelDevicesStore.setState({
    access: "approved", pending: [], approved: [], denied: [], loaded: false, error: null,
  });
}

describe("panelDevicesStore.load", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    reset();
  });

  it("takes the three lists and the mode from the server", async () => {
    vi.mocked(api.listPanelDevices).mockResolvedValue({
      access: "open", pending: [ipad], approved: [wall], denied: [],
    });
    await usePanelDevicesStore.getState().load();
    const s = usePanelDevicesStore.getState();
    expect(s.access).toBe("open");
    expect(s.pending.map((d) => d.id)).toEqual(["pd_ipad"]);
    expect(s.approved.map((d) => d.id)).toEqual(["pd_wall"]);
    expect(s.loaded).toBe(true);
    expect(s.error).toBeNull();
  });

  it("reads any mode but the exact word open as approved, like the server", async () => {
    vi.mocked(api.listPanelDevices).mockResolvedValue({
      access: "Open" as never, pending: [], approved: [], denied: [],
    });
    await usePanelDevicesStore.getState().load();
    expect(usePanelDevicesStore.getState().access).toBe("approved");
  });

  it("keeps what it had and shows no toast when the fetch fails", async () => {
    usePanelDevicesStore.setState({ pending: [ipad], loaded: true });
    vi.mocked(api.listPanelDevices).mockRejectedValue(new Error("API 503"));
    await usePanelDevicesStore.getState().load();
    const s = usePanelDevicesStore.getState();
    expect(s.pending.map((d) => d.id)).toEqual(["pd_ipad"]);
    expect(s.error).toBeTruthy();
    expect(showError).not.toHaveBeenCalled();
  });
});

describe("panelDevicesStore.applyChange", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    reset();
  });

  it("adds a pending device, oldest request first", () => {
    usePanelDevicesStore.getState().applyChange({ type: "panel.devices.changed", reason: "pending", device: ipad });
    usePanelDevicesStore.getState().applyChange({ type: "panel.devices.changed", reason: "pending", device: phone });
    expect(usePanelDevicesStore.getState().pending.map((d) => d.id)).toEqual(["pd_phone", "pd_ipad"]);
  });

  it("moves an approved device out of the waiting list and into the approved one, by name", () => {
    usePanelDevicesStore.setState({ pending: [ipad], approved: [wall] });
    usePanelDevicesStore.getState().applyChange({
      reason: "approved",
      device: { ...ipad, status: "approved", name: "Lobby", code: undefined },
    });
    const s = usePanelDevicesStore.getState();
    expect(s.pending).toEqual([]);
    expect(s.approved.map((d) => d.name)).toEqual(["Lobby", "Room 101 wall"]);
  });

  it("moves a denied device to the denied list", () => {
    usePanelDevicesStore.setState({ pending: [ipad] });
    usePanelDevicesStore.getState().applyChange({
      reason: "denied", device: { ...ipad, status: "denied", denied_at: "2026-09-25T14:05:00Z" },
    });
    const s = usePanelDevicesStore.getState();
    expect(s.pending).toEqual([]);
    expect(s.denied.map((d) => d.id)).toEqual(["pd_ipad"]);
  });

  it("drops a revoked or expired device from every list", () => {
    usePanelDevicesStore.setState({ pending: [ipad], approved: [wall] });
    usePanelDevicesStore.getState().applyChange({ reason: "revoked", device: wall });
    usePanelDevicesStore.getState().applyChange({ reason: "expired", device: ipad });
    const s = usePanelDevicesStore.getState();
    expect(s.pending).toEqual([]);
    expect(s.approved).toEqual([]);
  });

  it("replaces a renamed device in place", () => {
    usePanelDevicesStore.setState({ approved: [wall] });
    usePanelDevicesStore.getState().applyChange({ reason: "renamed", device: { ...wall, name: "Lobby wall" } });
    expect(usePanelDevicesStore.getState().approved.map((d) => d.name)).toEqual(["Lobby wall"]);
  });

  it("takes the new mode from an access change, which carries no device", () => {
    usePanelDevicesStore.getState().applyChange({ reason: "access_changed", access: "open" });
    expect(usePanelDevicesStore.getState().access).toBe("open");
    usePanelDevicesStore.getState().applyChange({ reason: "access_changed", access: "approved" });
    expect(usePanelDevicesStore.getState().access).toBe("approved");
  });

  it("refetches rather than guessing when a push carries no record it can read", () => {
    vi.mocked(api.listPanelDevices).mockResolvedValue({
      access: "approved", pending: [], approved: [], denied: [],
    });
    usePanelDevicesStore.getState().applyChange({ reason: "pending" });
    expect(api.listPanelDevices).toHaveBeenCalledTimes(1);
  });
});

describe("panelDevicesStore actions", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    reset();
  });

  it("approve sends the name and places the record the server returns", async () => {
    usePanelDevicesStore.setState({ pending: [ipad] });
    vi.mocked(api.approvePanelDevice).mockResolvedValue({ ...ipad, status: "approved", name: "Lobby" });
    const ok = await usePanelDevicesStore.getState().approve("pd_ipad", "Lobby");
    expect(ok).toBe(true);
    expect(api.approvePanelDevice).toHaveBeenCalledWith("pd_ipad", "Lobby");
    const s = usePanelDevicesStore.getState();
    expect(s.pending).toEqual([]);
    expect(s.approved.map((d) => d.name)).toEqual(["Lobby"]);
  });

  it("a refused action is a toast and resolves false", async () => {
    usePanelDevicesStore.setState({ pending: [ipad] });
    vi.mocked(api.approvePanelDevice).mockRejectedValue(new Error("API 404: No panel with that id."));
    const ok = await usePanelDevicesStore.getState().approve("pd_ipad", "Lobby");
    expect(ok).toBe(false);
    expect(showError).toHaveBeenCalledTimes(1);
    expect(usePanelDevicesStore.getState().pending.map((d) => d.id)).toEqual(["pd_ipad"]);
  });

  it("deny moves the device to the denied list", async () => {
    usePanelDevicesStore.setState({ pending: [ipad] });
    vi.mocked(api.denyPanelDevice).mockResolvedValue({ ...ipad, status: "denied", denied_at: "2026-09-25T14:05:00Z" });
    await usePanelDevicesStore.getState().deny("pd_ipad");
    expect(api.denyPanelDevice).toHaveBeenCalledWith("pd_ipad");
    expect(usePanelDevicesStore.getState().denied.map((d) => d.id)).toEqual(["pd_ipad"]);
  });

  it("revoke removes the device", async () => {
    usePanelDevicesStore.setState({ approved: [wall] });
    vi.mocked(api.revokePanelDevice).mockResolvedValue({ status: "revoked", panel_id: "pd_wall" });
    await usePanelDevicesStore.getState().revoke("pd_wall");
    expect(api.revokePanelDevice).toHaveBeenCalledWith("pd_wall");
    expect(usePanelDevicesStore.getState().approved).toEqual([]);
  });

  it("rename replaces the record", async () => {
    usePanelDevicesStore.setState({ approved: [wall] });
    vi.mocked(api.renamePanelDevice).mockResolvedValue({ ...wall, name: "Lobby wall" });
    await usePanelDevicesStore.getState().rename("pd_wall", "Lobby wall");
    expect(api.renamePanelDevice).toHaveBeenCalledWith("pd_wall", "Lobby wall");
    expect(usePanelDevicesStore.getState().approved.map((d) => d.name)).toEqual(["Lobby wall"]);
  });
});
