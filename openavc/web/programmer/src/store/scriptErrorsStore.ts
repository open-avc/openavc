/**
 * A counter that goes up each time the server sends `script.error`: a script
 * failed while running, and the server has already recorded it.
 *
 * Only a count, never the failure itself: the server's record
 * (`GET /api/scripts/errors`) is what the Scripts view shows, and a message
 * can name a version of a script that an edit has since replaced, which the
 * record leaves off its row. The count is what tells the view to ask again
 * (`useScriptErrorsRefresh`).
 */
import { create } from "zustand";

interface ScriptErrorsState {
  heard: number;
  /** Call on each `script.error` message. */
  bump: () => void;
}

export const useScriptErrorsStore = create<ScriptErrorsState>((set) => ({
  heard: 0,
  bump: () => set((s) => ({ heard: s.heard + 1 })),
}));
