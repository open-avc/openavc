// Pure rollback/selection logic behind the UI Builder undo/redo (no React,
// no store imports) so the node-harness regression suite can exercise it
// directly. Mirrors the driverBuilderStore.helpers.ts split.
import type {
  UIPage,
  UISettings,
  MasterElement,
  PageGroup,
  MacroConfig,
  VariableConfig,
  ProjectConfig,
} from "../api/types";

export interface UndoScope {
  // ui.* scopes
  pages?: UIPage[];
  settings?: UISettings;
  master_elements?: MasterElement[];
  page_groups?: PageGroup[];
  // project.* scopes (for cross-project edits like element rename
  // that must rewrite references in macros/variables)
  macros?: MacroConfig[];
  variables?: VariableConfig[];
}

export interface UndoEntry {
  description: string;
  snapshot: UndoScope;
}

export interface BuilderSelection {
  selectedPageId: string | null;
  selectedElementIds: string[];
  selectedElementId: string | null;
  selectedMasterElementId: string | null;
}

// Build (a) the inverse snapshot to push onto the redo/undo stack and
// (b) the project patch to apply, given the snapshot the user is rolling
// back to. UI scopes overlay onto project.ui; project scopes overlay
// directly onto the project. Only scopes present in the original snapshot
// are touched — that's the point of the scoped API.
export function computeRollbackPatch(
  snapshot: UndoScope,
  project: ProjectConfig,
): {
  redoSnapshot: UndoScope;
  projectPatch: Partial<ProjectConfig>;
} {
  const redoSnapshot: UndoScope = {};
  const uiPatch: Partial<ProjectConfig["ui"]> = {};
  const projectPatch: Partial<ProjectConfig> = {};
  let touchesUi = false;

  if ("pages" in snapshot) {
    redoSnapshot.pages = project.ui.pages;
    uiPatch.pages = snapshot.pages;
    touchesUi = true;
  }
  if ("settings" in snapshot) {
    redoSnapshot.settings = project.ui.settings;
    uiPatch.settings = snapshot.settings;
    touchesUi = true;
  }
  if ("master_elements" in snapshot) {
    redoSnapshot.master_elements = project.ui.master_elements ?? [];
    uiPatch.master_elements = snapshot.master_elements;
    touchesUi = true;
  }
  if ("page_groups" in snapshot) {
    redoSnapshot.page_groups = project.ui.page_groups ?? [];
    uiPatch.page_groups = snapshot.page_groups;
    touchesUi = true;
  }
  if ("macros" in snapshot) {
    redoSnapshot.macros = project.macros;
    projectPatch.macros = snapshot.macros;
  }
  if ("variables" in snapshot) {
    redoSnapshot.variables = project.variables;
    projectPatch.variables = snapshot.variables;
  }

  if (touchesUi) {
    projectPatch.ui = { ...project.ui, ...uiPatch };
  }

  return { redoSnapshot, projectPatch };
}

export function repairSelection(
  scope: UndoScope,
  current: {
    selectedPageId: string | null;
    selectedElementIds: string[];
    selectedMasterElementId: string | null;
  },
  fallback: { pages: UIPage[]; masters: MasterElement[] },
): BuilderSelection {
  const pages = scope.pages ?? fallback.pages;
  const masters = scope.master_elements ?? fallback.masters;

  let { selectedPageId, selectedElementIds, selectedMasterElementId } = current;

  if (selectedPageId) {
    const page = pages.find((p) => p.id === selectedPageId);
    if (!page) {
      selectedPageId = pages[0]?.id ?? null;
      selectedElementIds = [];
    } else {
      selectedElementIds = selectedElementIds.filter((eid) =>
        page.elements.some((e) => e.id === eid),
      );
    }
  }

  if (selectedMasterElementId && !masters.some((m) => m.id === selectedMasterElementId)) {
    selectedMasterElementId = null;
  }

  return {
    selectedPageId,
    selectedElementIds,
    selectedElementId: selectedElementIds[0] || null,
    selectedMasterElementId,
  };
}

// Selection to apply after rolling back to `scope`. Pages/elements/masters
// that exist in the applied snapshot but not in the current project are
// being re-created by this rollback (undo of a delete, redo of an add or
// paste) — select them so the canvas and Properties panel follow the
// change, matching how a forward add selects the new element. When nothing
// re-appears, fall back to repairing the existing selection against the
// rolled-back state.
export function selectionAfterRollback(
  scope: UndoScope,
  current: {
    selectedPageId: string | null;
    selectedElementIds: string[];
    selectedMasterElementId: string | null;
  },
  project: ProjectConfig,
): BuilderSelection {
  const repaired = repairSelection(scope, current, {
    pages: project.ui.pages,
    masters: project.ui.master_elements ?? [],
  });

  if (scope.pages) {
    const currentPages = new Map(
      project.ui.pages.map((p) => [p.id, new Set(p.elements.map((e) => e.id))]),
    );
    // A whole page re-appears (undo of a page delete): select the page
    // itself, not every element on it.
    const newPage = scope.pages.find((p) => !currentPages.has(p.id));
    if (newPage) {
      return {
        selectedPageId: newPage.id,
        selectedElementIds: [],
        selectedElementId: null,
        selectedMasterElementId: null,
      };
    }
    for (const page of scope.pages) {
      const existing = currentPages.get(page.id);
      const appeared = page.elements.filter((e) => !existing?.has(e.id)).map((e) => e.id);
      if (appeared.length > 0) {
        return {
          selectedPageId: page.id,
          selectedElementIds: appeared,
          selectedElementId: appeared[0],
          selectedMasterElementId: null,
        };
      }
    }
  }

  if (scope.master_elements) {
    const currentMasters = new Set((project.ui.master_elements ?? []).map((m) => m.id));
    const appearedMaster = scope.master_elements.find((m) => !currentMasters.has(m.id));
    if (appearedMaster) {
      return {
        selectedPageId: repaired.selectedPageId,
        selectedElementIds: [],
        selectedElementId: null,
        selectedMasterElementId: appearedMaster.id,
      };
    }
  }

  return repaired;
}
