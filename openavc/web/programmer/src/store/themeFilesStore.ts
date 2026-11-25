/**
 * A counter that goes up every time one of the project's saved themes changes
 * on the server: saved, imported or deleted in another tab, or swapped with
 * the files of a project that replaced this one (`themes.changed`).
 *
 * A theme is a file, not project data, so a change to one moves nothing the
 * project push carries. The UI Builder reads its theme list and the theme it
 * draws again on this number (builderTheme.ts).
 */
import { create } from "zustand";

interface ThemeFilesState {
  version: number;
  bump: () => void;
}

export const useThemeFilesStore = create<ThemeFilesState>((set) => ({
  version: 0,
  bump: () => set((s) => ({ version: s.version + 1 })),
}));
