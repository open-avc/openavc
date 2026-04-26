import { create } from "zustand";

interface ConnectionStore {
  connected: boolean;
  liveState: Record<string, unknown>;
  stateVersion: number;

  setConnected: (v: boolean) => void;
  applyStateUpdate: (changes: Record<string, unknown>) => void;
  setFullState: (state: Record<string, unknown>) => void;
  removeKeysWithPrefix: (prefix: string) => void;
}

export const useConnectionStore = create<ConnectionStore>((set) => ({
  connected: false,
  liveState: {},
  stateVersion: 0,

  setConnected: (connected) => set({ connected }),

  applyStateUpdate: (changes) =>
    set((s) => ({
      liveState: { ...s.liveState, ...changes },
      stateVersion: s.stateVersion + 1,
    })),

  setFullState: (liveState) => set((s) => ({ liveState, stateVersion: s.stateVersion + 1 })),

  removeKeysWithPrefix: (prefix) =>
    set((s) => {
      const p = prefix.endsWith(".") ? prefix : prefix + ".";
      const next: Record<string, unknown> = {};
      for (const [key, value] of Object.entries(s.liveState)) {
        if (!key.startsWith(p) && key !== prefix) {
          next[key] = value;
        }
      }
      return { liveState: next };
    }),
}));
