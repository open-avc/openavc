import { create } from "zustand";

export interface LogEntry {
  timestamp: number;
  level: string;
  source: string;
  category: string;
  message: string;
}

export interface MacroProgress {
  macroId: string | null;
  stepIndex: number | null;
  totalSteps: number | null;
  status: "idle" | "running" | "completed" | "error";
}

export interface StepError {
  stepIndex: number;
  action: string;
  device: string;
  group: string;
  command: string;
  error: string;
  description: string;
}

export interface ConditionalResult {
  conditionResult: boolean;
  branch: "then" | "else";
  conditionKey: string;
  conditionOperator: string;
  actualValue: unknown;
}

export interface GroupCommandResult {
  group: string;
  command: string;
  deviceResults: Array<{
    device_id: string;
    name: string;
    success: boolean;
    error?: string;
  }>;
}

export interface MacroLastRun {
  macroId: string;
  startedAt: number;
  completedAt: number;
  duration: number;
  status: "completed" | "error";
  stepErrors: StepError[];
  conditionalResults: ConditionalResult[];
  groupResults: GroupCommandResult[];
  error?: string;
}

export interface TriggerPending {
  reason: "debounce" | "delay" | "queued";
  waitSeconds?: number;
  queuePosition?: number;
  timestamp: number;
}

interface LogStore {
  logEntries: LogEntry[];
  logPaused: boolean;
  logSubscribed: boolean;

  macroProgress: MacroProgress;
  // Step-level data accumulated during a macro run
  stepErrors: StepError[];
  conditionalResults: ConditionalResult[];
  groupResults: GroupCommandResult[];
  macroStartedAt: number;
  lastRun: MacroLastRun | null;

  // Trigger pending states (trigger_id -> pending info)
  triggerPending: Record<string, TriggerPending>;

  addLogEntry: (entry: LogEntry) => void;
  addLogBatch: (entries: LogEntry[]) => void;
  setLogPaused: (v: boolean) => void;
  clearLogEntries: () => void;
  setLogSubscribed: (v: boolean) => void;

  setMacroProgress: (p: Partial<MacroProgress>) => void;
  resetMacroProgress: () => void;
  addStepError: (e: StepError) => void;
  addConditionalResult: (r: ConditionalResult) => void;
  addGroupResult: (r: GroupCommandResult) => void;
  startMacroRun: (macroId: string) => void;
  finishMacroRun: (status: "completed" | "error", error?: string) => void;
  setTriggerPending: (triggerId: string, pending: TriggerPending | null) => void;
}

const INITIAL_MACRO: MacroProgress = {
  macroId: null,
  stepIndex: null,
  totalSteps: null,
  status: "idle",
};

export const useLogStore = create<LogStore>((set, get) => ({
  logEntries: [],
  logPaused: false,
  logSubscribed: false,

  macroProgress: { ...INITIAL_MACRO },
  stepErrors: [],
  conditionalResults: [],
  groupResults: [],
  macroStartedAt: 0,
  lastRun: null,
  triggerPending: {},

  addLogEntry: (entry) =>
    set((s) => {
      if (s.logPaused) return s;
      const next = [...s.logEntries, entry];
      return { logEntries: next.length > 500 ? next.slice(-500) : next };
    }),

  addLogBatch: (entries) =>
    set((s) => {
      if (s.logPaused) return s;
      const next = [...s.logEntries, ...entries];
      return { logEntries: next.length > 500 ? next.slice(-500) : next };
    }),

  setLogPaused: (logPaused) => set({ logPaused }),

  clearLogEntries: () => set({ logEntries: [] }),

  setLogSubscribed: (logSubscribed) => set({ logSubscribed }),

  setMacroProgress: (p) =>
    set((s) => ({
      macroProgress: { ...s.macroProgress, ...p },
    })),

  resetMacroProgress: () => set({
    macroProgress: { ...INITIAL_MACRO },
    stepErrors: [],
    conditionalResults: [],
    groupResults: [],
  }),

  addStepError: (e) => set((s) => ({ stepErrors: [...s.stepErrors, e] })),

  addConditionalResult: (r) => set((s) => ({ conditionalResults: [...s.conditionalResults, r] })),

  addGroupResult: (r) => set((s) => ({ groupResults: [...s.groupResults, r] })),

  startMacroRun: (macroId) => set({
    stepErrors: [],
    conditionalResults: [],
    groupResults: [],
    macroStartedAt: Date.now(),
    macroProgress: { macroId, stepIndex: null, totalSteps: null, status: "running" },
  }),

  finishMacroRun: (status, error) => {
    const s = get();
    const lastRun: MacroLastRun = {
      macroId: s.macroProgress.macroId ?? "",
      startedAt: s.macroStartedAt,
      completedAt: Date.now(),
      duration: s.macroStartedAt ? Date.now() - s.macroStartedAt : 0,
      status,
      stepErrors: [...s.stepErrors],
      conditionalResults: [...s.conditionalResults],
      groupResults: [...s.groupResults],
      error,
    };
    set({ lastRun });
  },

  setTriggerPending: (triggerId, pending) =>
    set((s) => {
      const next = { ...s.triggerPending };
      if (pending) {
        next[triggerId] = pending;
      } else {
        delete next[triggerId];
      }
      return { triggerPending: next };
    }),
}));
