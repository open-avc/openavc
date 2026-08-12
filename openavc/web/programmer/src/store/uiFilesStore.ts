/**
 * A counter that goes up every time a file lands in the project's ui/ folder.
 *
 * Custom controls are files, not project data, so saving one changes nothing
 * the project push would carry and the design canvas would happily keep
 * showing the copy the browser already has. The canvas rides this number on
 * each control's URL, so a save redraws it.
 */
import { create } from "zustand";

interface UiFilesState {
  version: number;
  /** Call after any write into ui/ (editor save, upload, zip import, delete). */
  bump: () => void;
}

export const useUiFilesStore = create<UiFilesState>((set) => ({
  version: 0,
  bump: () => set((s) => ({ version: s.version + 1 })),
}));
