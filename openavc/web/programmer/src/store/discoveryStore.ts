/**
 * Discovery store — manages device discovery scan state.
 */
import { create } from "zustand";
import type { DiscoveredDevice } from "../api/restClient";

/** How a scan ended, from a ``discovery.complete`` event's ``status``: a scan
 *  someone stopped (in this Programmer or another) reads ``cancelled``, one
 *  that covered less than was asked ``partial``, anything else ``complete``. */
export function finishedScanStatus(status: unknown): "complete" | "partial" | "cancelled" {
  return status === "partial" || status === "cancelled" ? status : "complete";
}

/** Whether a scan with this status has ended, finished or stopped. */
export function scanHasEnded(status: string): boolean {
  return status === "complete" || status === "partial" || status === "cancelled";
}

/** A scan that began, from its ``discovery.started`` message. */
export interface StartedScan {
  scanId: string;
  /** The time limit it works to, in seconds */
  budgetSeconds: number;
}

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
  /** The time limit the running scan works to, in seconds (0 when not known) */
  budgetSeconds: number;
  /** Dynamic port labels from baseline + driver hints + community catalog */
  portLabels: Record<string, string>;
  /** The drivers that declare each port (port -> driver ids) */
  portDrivers: Record<string, string[]>;
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
  /** Set the running scan's time limit */
  setBudgetSeconds: (seconds: number) => void;
  /** A scan began: started here, in another Programmer, or by the assistant */
  scanStarted: (scan: StartedScan) => void;
  /** Set port labels, and the drivers behind each port, from API */
  setPortLabels: (labels: Record<string, string>, portDrivers?: Record<string, string[]>) => void;
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
  budgetSeconds: 0,
  portLabels: {},
  portDrivers: {},
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

  setBudgetSeconds: (budgetSeconds) => set({ budgetSeconds }),

  // The last scan's progress and warnings go with it.
  scanStarted: ({ scanId, budgetSeconds }) =>
    set({ status: "running", scanId, budgetSeconds, phase: "", progress: 0, message: "", warnings: [] }),

  setPortLabels: (labels, portDrivers) =>
    set({ portLabels: labels, portDrivers: portDrivers ?? {} }),

  setWarnings: (warnings) => set({ warnings }),

  clear: () =>
    set({
      devices: {},
      status: "idle",
      phase: "",
      progress: 0,
      message: "",
      scanId: "",
      budgetSeconds: 0,
      portLabels: {},
      portDrivers: {},
      warnings: [],
    }),
}));

type DiscoveryStore = ReturnType<typeof useDiscoveryStore.getState>;
type DiscoveryMessage = Record<string, unknown>;

/** A message field that should be text: "" when it is not. */
function text(value: unknown): string {
  return typeof value === "string" ? value : "";
}

/** A message field that should be a number: 0 when it is not. */
function count(value: unknown): number {
  return typeof value === "number" ? value : 0;
}

function applyWarnings(store: DiscoveryStore, warnings: unknown): void {
  if (Array.isArray(warnings)) store.setWarnings(warnings as string[]);
}

function applyStart(store: DiscoveryStore, msg: DiscoveryMessage): void {
  store.scanStarted({ scanId: text(msg.scan_id), budgetSeconds: count(msg.budget_seconds) });
}

function applyDeviceUpdate(store: DiscoveryStore, msg: DiscoveryMessage): void {
  if (!msg.device) return;
  store.upsertDevice(msg.device as DiscoveredDevice);
  if (typeof msg.progress === "number") store.setPhase(text(msg.phase), msg.progress, "");
}

function applyPhase(store: DiscoveryStore, msg: DiscoveryMessage): void {
  store.setPhase(text(msg.phase), count(msg.progress), text(msg.message));
  applyWarnings(store, msg.warnings);
}

// "partial" when it ran out of time or errored (the results are real but the
// sweep didn't finish); "cancelled" when someone pressed Stop.
function applyEnd(store: DiscoveryStore, msg: DiscoveryMessage): void {
  store.setStatus(finishedScanStatus(msg.status));
  applyWarnings(store, msg.warnings);
}

const DISCOVERY_MESSAGES = new Map<unknown, (store: DiscoveryStore, msg: DiscoveryMessage) => void>([
  ["discovery.started", applyStart],
  ["discovery.update", applyDeviceUpdate],
  ["discovery.phase", applyPhase],
  ["discovery.complete", applyEnd],
]);

/** Apply one ``discovery.*`` WebSocket message: a scan began (whoever
 *  started it), moved on a phase, found a device, or ended. Any other type is
 *  left alone. */
export function applyDiscoveryMessage(msg: DiscoveryMessage): void {
  const apply = DISCOVERY_MESSAGES.get(msg.type);
  if (apply) apply(useDiscoveryStore.getState(), msg);
}
