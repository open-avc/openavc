/**
 * Panel devices store: the tablets, phones and browsers that have asked to
 * open the panel, and the Panel access mode they are judged by.
 *
 * Fetched from GET /api/panel/devices on every WebSocket (re)connect, then
 * kept current by the `panel.devices.changed` push, which carries the same
 * record the list does. A missed push is therefore harmless: the next connect
 * refetches. Select one primitive or one stored array at a time; a selector
 * that builds a new object returns a new reference every render and crashes
 * React 19.
 */
import { create } from "zustand";
import type { PanelAccessMode, PanelDevice } from "../api/restClient";
import * as api from "../api/restClient";
import { parseApiError } from "../api/errors";
import { showError } from "./toastStore";

export type PanelDevicesChangeReason =
  | "pending"
  | "approved"
  | "denied"
  | "revoked"
  | "renamed"
  | "expired"
  | "access_changed";

interface PanelDevicesStore {
  access: PanelAccessMode;
  /** True on a system that was updated with its panels connected and is
   *  still set to Anyone on the network; the notice shows once until it is
   *  dismissed or the mode is switched. */
  upgradeNotice: boolean;
  /** Waiting for approval, oldest request first. */
  pending: PanelDevice[];
  /** Approved, by name. */
  approved: PanelDevice[];
  /** Denied; each record expires a day after the denial. */
  denied: PanelDevice[];
  /** True once a list has been fetched, so an empty card can say so honestly. */
  loaded: boolean;
  error: string | null;

  load: () => Promise<void>;
  /** Apply one `panel.devices.changed` push. */
  applyChange: (message: Record<string, unknown>) => void;
  /** Each action resolves true when the server accepted it; a refusal is
   *  shown as a toast and resolves false, so a dialog can stay open. */
  approve: (id: string, name: string) => Promise<boolean>;
  deny: (id: string) => Promise<boolean>;
  revoke: (id: string) => Promise<boolean>;
  rename: (id: string, name: string) => Promise<boolean>;
  /** Clear the one-time notice; the server remembers, so it stays gone. */
  dismissUpgradeNotice: () => Promise<void>;
}

function accessMode(value: unknown): PanelAccessMode {
  // The server's own rule: anything other than the exact word "open" is
  // approved, so a garbled value never draws the card as if the panel were
  // open to everyone.
  return value === "open" ? "open" : "approved";
}

function isDevice(value: unknown): value is PanelDevice {
  return (
    typeof value === "object" && value !== null
    && typeof (value as PanelDevice).id === "string"
    && typeof (value as PanelDevice).status === "string"
  );
}

type Lists = Pick<PanelDevicesStore, "pending" | "approved" | "denied">;

function without(lists: Lists, id: string): Lists {
  return {
    pending: lists.pending.filter((d) => d.id !== id),
    approved: lists.approved.filter((d) => d.id !== id),
    denied: lists.denied.filter((d) => d.id !== id),
  };
}

/** The record placed in the list its status names, in the server's order:
 *  pending by first sight, approved by name, denied by the time of denial. */
function placed(lists: Lists, device: PanelDevice): Lists {
  const next = without(lists, device.id);
  if (device.status === "pending") {
    next.pending = [...next.pending, device].sort((a, b) => a.first_seen.localeCompare(b.first_seen));
  } else if (device.status === "approved") {
    next.approved = [...next.approved, device].sort(
      (a, b) => a.name.toLowerCase().localeCompare(b.name.toLowerCase()) || a.id.localeCompare(b.id),
    );
  } else if (device.status === "denied") {
    next.denied = [...next.denied, device].sort(
      (a, b) => (a.denied_at ?? "").localeCompare(b.denied_at ?? ""),
    );
  }
  return next;
}

export const usePanelDevicesStore = create<PanelDevicesStore>((set, get) => ({
  access: "approved",
  upgradeNotice: false,
  pending: [],
  approved: [],
  denied: [],
  loaded: false,
  error: null,

  load: async () => {
    try {
      const list = await api.listPanelDevices();
      set({
        access: accessMode(list.access),
        upgradeNotice: list.upgrade_notice === true,
        pending: list.pending ?? [],
        approved: list.approved ?? [],
        denied: list.denied ?? [],
        loaded: true,
        error: null,
      });
    } catch (e) {
      // Not a toast: this runs on every reconnect, and the card says nothing
      // rather than claiming an empty list it never fetched.
      set({ error: parseApiError(e) });
    }
  },

  applyChange: (message) => {
    const reason = message.reason as PanelDevicesChangeReason | undefined;
    const device = message.device;
    if (reason === "access_changed") {
      set({ access: accessMode(message.access), upgradeNotice: message.upgrade_notice === true });
      return;
    }
    if (!isDevice(device)) {
      // A push this store cannot read: refetch rather than guess.
      void get().load();
      return;
    }
    const lists: Lists = { pending: get().pending, approved: get().approved, denied: get().denied };
    if (reason === "revoked" || reason === "expired") {
      set(without(lists, device.id));
      return;
    }
    if (reason === "pending" || reason === "approved" || reason === "denied" || reason === "renamed") {
      set(placed(lists, device));
      return;
    }
    void get().load();
  },

  approve: async (id, name) => {
    try {
      const device = await api.approvePanelDevice(id, name);
      const s = get();
      set(placed({ pending: s.pending, approved: s.approved, denied: s.denied }, device));
      return true;
    } catch (e) {
      showError("Couldn't approve the panel: " + parseApiError(e));
      return false;
    }
  },

  deny: async (id) => {
    try {
      const device = await api.denyPanelDevice(id);
      const s = get();
      set(placed({ pending: s.pending, approved: s.approved, denied: s.denied }, device));
      return true;
    } catch (e) {
      showError("Couldn't deny the panel: " + parseApiError(e));
      return false;
    }
  },

  revoke: async (id) => {
    try {
      await api.revokePanelDevice(id);
      const s = get();
      set(without({ pending: s.pending, approved: s.approved, denied: s.denied }, id));
      return true;
    } catch (e) {
      showError("Couldn't revoke the panel: " + parseApiError(e));
      return false;
    }
  },

  rename: async (id, name) => {
    try {
      const device = await api.renamePanelDevice(id, name);
      const s = get();
      set(placed({ pending: s.pending, approved: s.approved, denied: s.denied }, device));
      return true;
    } catch (e) {
      showError("Couldn't rename the panel: " + parseApiError(e));
      return false;
    }
  },

  dismissUpgradeNotice: async () => {
    set({ upgradeNotice: false });
    try {
      await api.dismissPanelAccessNotice();
    } catch (e) {
      // It comes back on the next connect if the server never heard.
      showError("Couldn't dismiss the notice: " + parseApiError(e));
    }
  },
}));
