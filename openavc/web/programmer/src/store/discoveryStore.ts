/**
 * Discovery store — manages device discovery scan state.
 */
import { create } from "zustand";
import type { DiscoveredDevice } from "../api/restClient";

interface DiscoveryState {
  /** Discovered devices keyed by IP */
  devices: Record<string, DiscoveredDevice>;
  /** Scan status */
  /** "partial" = the scan covered less of the network than was asked for —
   *  it hit a time limit, errored partway, or narrowed a phase's work to fit
   *  its budget. The devices it did find are matched; the warnings say what
   *  was left out. */
  status: "idle" | "running" | "complete" | "cancelled" | "partial";
  /** Current scan phase name */
  phase: string;
  /** Progress 0-1 */
  progress: number;
  /** Phase message */
  message: string;
  /** Scan ID */
  scanId: string;
  /** Dynamic port labels from baseline + driver hints + community catalog */
  portLabels: Record<string, string>;
  /** Environment problems that kept scan phases from working */
  warnings: string[];

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
  /** Set environment warnings from API/WS */
  setWarnings: (warnings: string[]) => void;
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
  warnings: [],

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

  setWarnings: (warnings) => set({ warnings }),

  clear: () =>
    set({
      devices: {},
      status: "idle",
      phase: "",
      progress: 0,
      message: "",
      scanId: "",
      portLabels: {},
      warnings: [],
    }),
}));
