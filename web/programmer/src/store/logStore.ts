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

interface LogStore {
  logEntries: LogEntry[];
  logPaused: boolean;
  logSubscribed: boolean;

  macroProgress: MacroProgress;

  addLogEntry: (entry: LogEntry) => void;
  addLogBatch: (entries: LogEntry[]) => void;
  setLogPaused: (v: boolean) => void;
  clearLogEntries: () => void;
  setLogSubscribed: (v: boolean) => void;

  setMacroProgress: (p: Partial<MacroProgress>) => void;
  resetMacroProgress: () => void;
}

const INITIAL_MACRO: MacroProgress = {
  macroId: null,
  stepIndex: null,
  totalSteps: null,
  status: "idle",
};

export const useLogStore = create<LogStore>((set) => ({
  logEntries: [],
  logPaused: false,
  logSubscribed: false,

  macroProgress: { ...INITIAL_MACRO },

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

  resetMacroProgress: () => set({ macroProgress: { ...INITIAL_MACRO } }),
}));
