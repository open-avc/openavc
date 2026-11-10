/**
 * The UI Builder's keyboard: which key does what on the page, and when the
 * page leaves a key alone.
 *
 * `handleBuilderKey` asks, in this order, and the first answer wins: a dialog
 * is open (the key is the dialog's); Ctrl+P and Ctrl+E (preview and the
 * palette, in preview too); preview (nothing else); a key typed in a field
 * with no Ctrl or Cmd (the field's); Escape; the arrow keys; Delete and
 * Backspace; then the Ctrl chords, which a field keeps for itself.
 *
 * The page hands the keyboard the Builder as it was last drawn, with the edits
 * a key can make and the undo run (`BuilderKeys`); which edit each key makes,
 * which box an arrow moves and how far, are here. Undo, redo, Escape and
 * Ctrl+P are the Builder store's own actions, and Ctrl+S is saving now
 * (saveState.ts). `useBuilderShortcuts` registers again on every render, so a
 * key always acts on the page as it was last drawn.
 */
import { useEffect } from "react";
import type { MasterElement, SnapConfig, UIPage } from "../../api/types";
import { isModalOpen } from "../../components/shared/Modal";
import {
  layoutOrientation,
  masterPlacement,
  pageSnap,
  withMasterPlacement,
} from "../../components/ui-builder/uiBuilderHelpers";
import { saveNow } from "../../store/saveState";
import { useUIBuilderStore } from "../../store/uiBuilderStore";
import type { Offset } from "./elementEdits";
import type { EditRun } from "./useEditRun";
import type { ElementEdits } from "./useElementEdits";

/** Arrow-key travel, in percent, when snapping is off or bypassed. Small
 *  enough to place a control by eye, large enough to see it move. */
export const FINE_NUDGE = 0.25;

/** The page as the keyboard needs it, as last drawn, and what a key acts on. */
export interface BuilderKeys {
  previewMode: boolean;
  page: UIPage | null;
  activeLayoutId: string | null;
  masters: MasterElement[];
  selectedIds: string[];
  selectedMasterId: string | null;
  locked: Set<string>;
  hasClipboard: boolean;
  /** Ctrl+E: whether the palette shows is the page's. */
  togglePalette: () => void;
  /** Delete, copy and duplicate are handed the selection; a nudge, of
   *  controls or of a master, joins the run in progress. */
  edits: Pick<ElementEdits, "nudge" | "editMaster" | "deleteMaster" | "remove" | "copy" | "paste" | "duplicate">;
  run: Pick<EditRun, "endRun">;
}

const store = () => useUIBuilderStore.getState();

/** Undo and redo end the run first: an undo that keeps the selection would
 *  otherwise leave the next edit inside the entry just undone. */
function undo(k: BuilderKeys) {
  k.run.endRun();
  store().undo();
}

function redo(k: BuilderKeys) {
  k.run.endRun();
  store().redo();
}

export function handleBuilderKey(e: KeyboardEvent, k: BuilderKeys): void {
  // A dialog is up: the canvas is behind a backdrop, so these keys are meant
  // for the dialog. Before this guard, Escape on a dialog also deselected the
  // element underneath, and Delete reached the canvas and removed it.
  if (isModalOpen()) return;
  if (toggleKey(e, k) || k.previewMode) return;
  if (typingInField(e)) {
    // A plain key (Delete, an arrow) is the field's. A Ctrl chord on the
    // selection is still the page's; undo, copy and the rest are the field's.
    if (commandKey(e)) selectionKey(e, k);
    return;
  }
  if (!selectionKey(e, k) && commandKey(e)) chordKey(e, k);
}

/** Escape, the arrows, Delete and Backspace. True when the key is spent. */
function selectionKey(e: KeyboardEvent, k: BuilderKeys): boolean {
  if (e.key === "Escape") {
    e.preventDefault();
    store().selectElement(null);
    return true;
  }
  if (DIRECTIONS.has(e.key) && k.page) return arrowKey(e, k, k.page);
  return (e.key === "Delete" || e.key === "Backspace") && deleteKey(e, k);
}

export function useBuilderShortcuts(k: BuilderKeys): void {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => handleBuilderKey(e, k);
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  });
}

/** Which way each arrow moves a box, as (x, y) signs. */
const DIRECTIONS = new Map<string, [number, number]>([
  ["ArrowLeft", [-1, 0]],
  ["ArrowRight", [1, 0]],
  ["ArrowUp", [0, -1]],
  ["ArrowDown", [0, 1]],
]);

function commandKey(e: KeyboardEvent): boolean {
  return e.ctrlKey || e.metaKey;
}

function typingInField(e: KeyboardEvent): boolean {
  const tag = (e.target as HTMLElement).tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT";
}

/** Ctrl+P (preview) and Ctrl+E (the palette), which work in preview too. */
const TOGGLES = new Map<string, (k: BuilderKeys) => void>([
  ["p", (k) => store().setPreviewMode(!k.previewMode)],
  ["e", (k) => k.togglePalette()],
]);

function toggleKey(e: KeyboardEvent, k: BuilderKeys): boolean {
  const toggle = commandKey(e) ? TOGGLES.get(e.key) : undefined;
  if (!toggle) return false;
  e.preventDefault();
  toggle(k);
  return true;
}

/**
 * How far one press moves a selection, in percent: one snap step, ten with
 * shift; holding Alt suspends snapping and moves a hair, the same bypass a
 * drag gets.
 */
export function nudgeOffset(
  key: string,
  snap: SnapConfig,
  mods: { alt: boolean; shift: boolean },
): Offset {
  const [sx, sy] = DIRECTIONS.get(key) ?? [0, 0];
  const fine = mods.alt || !snap.enabled;
  const mult = mods.shift ? 10 : 1;
  return {
    dx: sx * (fine ? FINE_NUDGE : snap.x) * mult,
    dy: sy * (fine ? FINE_NUDGE : snap.y) * mult,
  };
}

/** An arrow key moves the selected master, else the selected controls.
 *  False when nothing is selected, so the key goes on down the list. */
function arrowKey(e: KeyboardEvent, k: BuilderKeys, page: UIPage): boolean {
  const offset = nudgeOffset(e.key, pageSnap(page), { alt: e.altKey, shift: e.shiftKey });
  if (k.selectedMasterId && k.masters.length > 0) {
    nudgeMaster(e, k, page, offset);
    return true;
  }
  if (k.selectedIds.length === 0) return false;
  // Nothing clamps: free positioning warns rather than prevents, and the
  // out-of-bounds badge is what says so.
  if (k.selectedIds.some((id) => k.locked.has(id))) return true;
  e.preventDefault();
  const movable = k.selectedIds.filter((id) => page.elements.some((el) => el.id === id));
  if (movable.length > 0) {
    k.edits.nudge(movable, offset, `Nudge ${e.key.replace("Arrow", "").toLowerCase()}`);
  }
  return true;
}

/**
 * The arrangement on screen decides which of a master's boxes moves, the same
 * rule the canvas drag and the Layout fields follow: this used to write the
 * landscape box whatever was being authored, so nudging a master on a portrait
 * arrangement moved it on every OTHER page and left it where it was on this one.
 */
function nudgeMaster(e: KeyboardEvent, k: BuilderKeys, page: UIPage, { dx, dy }: Offset) {
  const id = k.selectedMasterId!;
  if (k.locked.has(id)) return;
  const master = k.masters.find((m) => m.id === id);
  if (!master) return;
  e.preventDefault();
  const orientation = layoutOrientation(page, k.activeLayoutId);
  const current = masterPlacement(master, orientation);
  if (!current) return;
  const moved = withMasterPlacement(master, orientation, { ...current, x: current.x + dx, y: current.y + dy });
  k.edits.editMaster(id, { placements: moved.placements });
}

/** Delete and Backspace: the selected master, else the selected controls. A
 *  locked one is protected from a stray key. True when the key is spent. */
function deleteKey(e: KeyboardEvent, k: BuilderKeys): boolean {
  if (k.selectedMasterId) {
    if (k.locked.has(k.selectedMasterId)) return true;
    e.preventDefault();
    k.edits.deleteMaster(k.selectedMasterId);
    return true;
  }
  if (k.selectedIds.length === 0 || !k.page) return false;
  if (k.selectedIds.some((id) => k.locked.has(id))) return true;
  e.preventDefault();
  k.edits.remove(k.selectedIds);
  return true;
}

/** The Ctrl (or Cmd) chords: undo, redo, save, copy, paste, duplicate. */
function chordKey(e: KeyboardEvent, k: BuilderKeys): void {
  const action = chordAction(e, k);
  if (!action) return;
  e.preventDefault();
  action();
}

function chordAction(e: KeyboardEvent, k: BuilderKeys): (() => void) | null {
  const hasSelection = k.selectedIds.length > 0 && !!k.page;
  switch (e.key) {
    case "z":
      return e.shiftKey ? () => redo(k) : () => undo(k);
    case "y":
      return () => redo(k);
    case "s":
      return e.shiftKey ? null : saveNow;
    case "c":
      return hasSelection ? () => k.edits.copy(k.selectedIds) : null;
    case "v":
      return k.hasClipboard && k.page ? k.edits.paste : null;
    case "d":
      return hasSelection ? () => k.edits.duplicate(k.selectedIds) : null;
    default:
      return null;
  }
}
