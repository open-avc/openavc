/**
 * Discovery store — manages device discovery scan state.
 */
import { create } from "zustand";
import type { DiscoveredDevice } from "../api/restClient";

interface DiscoveryState {
  /** Discovered devices keyed by IP */
  devices: Record<string, DiscoveredDevice>;
  /** Scan status */
  status: "idle" | "running" | "complete" | "cancelled";
  /** Current scan phase name */
  phase: string;
  /** Progress 0-1 */
  progress: number;
  /** Phase message */
  message: string;
  /** Scan ID */
  scanId: string;
  /** Dynamic port labels from AV_PORTS + community drivers */
  portLabels: Record<string, string>;

  /** Update or add a device from a WS event */
  upsertDevice: (device: DiscoveredDevice) => void;
  /** Set full device list from REST response */
  setDevices: (devices: DiscoveredDevice[]) => void;
  /** Update scan progress */
  setPhase: (phase: string, progress: number, message: string) => void;
  /** Update scan status */
  setStatus: (status: DiscoveryState["status"]) => void;
  /** Set scan ID */
  setScanId: (id: string) => void;
  /** Set port labels from API */
  setPortLabels: (labels: Record<string, string>) => void;
  /** Clear all results */
  clear: () => void;
}

export const useDiscoveryStore = create<DiscoveryState>((set) => ({
  devices: {},
  status: "idle",
  phase: "",
  progress: 0,
  message: "",
  scanId: "",
  portLabels: {},

  upsertDevice: (device) =>
    set((s) => ({
      devices: { ...s.devices, [device.ip]: device },
    })),

  setDevices: (devices) => {
    const map: Record<string, DiscoveredDevice> = {};
    for (const d of devices) map[d.ip] = d;
    set({ devices: map });
  },

  setPhase: (phase, progress, message) => set({ phase, progress, message }),

  setStatus: (status) => set({ status }),

  setScanId: (scanId) => set({ scanId }),

  setPortLabels: (labels) => set({ portLabels: labels }),

  clear: () =>
    set({
      devices: {},
      status: "idle",
      phase: "",
      progress: 0,
      message: "",
      scanId: "",
      portLabels: {},
    }),
}));
