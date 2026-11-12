import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { UIPage } from "../../api/types";
import { ShortcutsPanel } from "../../components/shared/ShortcutsPanel";
import { useUIBuilderStore } from "../../store/uiBuilderStore";
import { BUILDER_SHORTCUTS, handleBuilderKey, type BuilderKeys } from "./builderShortcuts";

// The shortcuts reference (Ctrl+/) lists the UI Builder's keys from the rows
// beside them, so a key the Builder answers without a row, or a row for a key
// it no longer answers, fails here.

vi.mock("../../store/saveState", () => ({ saveNow: vi.fn() }));

afterEach(cleanup);

/** A Builder with something selected and something to paste, so every key
 *  that acts on a selection has one to act on. */
function builder(): BuilderKeys {
  const page = {
    id: "main", name: "Main",
    elements: [{ id: "a", type: "button", placement: { x: 10, y: 10, w: 10, h: 10 } }],
  } as unknown as UIPage;
  return {
    previewMode: false, page, activeLayoutId: null, masters: [], selectedIds: ["a"],
    selectedMasterId: null, locked: new Set(), hasClipboard: true, togglePalette: vi.fn(),
    edits: {
      nudge: vi.fn(), editMaster: vi.fn(), deleteMaster: vi.fn(), remove: vi.fn(),
      copy: vi.fn(), paste: vi.fn(), duplicate: vi.fn(),
    },
  };
}

interface Press { key: string; ctrl?: boolean; shift?: boolean; alt?: boolean }

/** The name a row gives a press: "Ctrl+Shift+Z", "Shift+Arrow keys". */
function named(p: Press): string {
  const base = p.key.startsWith("Arrow") ? "Arrow keys" : p.key.length === 1 ? p.key.toUpperCase() : p.key;
  return [p.ctrl && "Ctrl", p.alt && "Alt", p.shift && "Shift", base].filter(Boolean).join("+");
}

/** Whether the Builder acts on a press, read from the key event it spends. */
function answers(p: Press): boolean {
  const target = document.createElement("div");
  document.body.append(target);
  const event = new KeyboardEvent("keydown", {
    key: p.key, ctrlKey: !!p.ctrl, shiftKey: !!p.shift, altKey: !!p.alt, bubbles: true, cancelable: true,
  });
  const k = builder();
  target.addEventListener("keydown", (e) => handleBuilderKey(e, k));
  target.dispatchEvent(event);
  target.remove();
  return event.defaultPrevented;
}

// Every letter as a chord, with and without Shift (which a browser reports
// as the capital); the keys that are not letters on their own, and the arrows
// with each modifier that changes a move.
const LETTERS = "abcdefghijklmnopqrstuvwxyz".split("");
const ARROWS = ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"];
const PRESSES: Press[] = [
  ...LETTERS.flatMap((key) => [{ key, ctrl: true }, { key: key.toUpperCase(), ctrl: true, shift: true }]),
  ...["Escape", "Delete", "Backspace", "Enter", "Tab", "Home", "End", "PageUp", "PageDown"].map((key) => ({ key })),
  ...ARROWS.flatMap((key) => [{ key }, { key, shift: true }, { key, alt: true }]),
];

/** Each key a row names; "Ctrl+Y / Ctrl+Shift+Z" names two. */
const rowKeys = () => new Set(BUILDER_SHORTCUTS.flatMap((row) => row.keys.split(" / ")));

// Pressed on the canvas, not a key the keyboard handler hears.
const CANVAS_GESTURES = new Set(["Shift+Click"]);

describe("the UI Builder's rows in the shortcuts reference", () => {
  it("name every key the Builder answers", () => {
    const answered = [...new Set(PRESSES.filter(answers).map(named))];
    expect(answered.length).toBeGreaterThan(10);
    expect(answered.filter((name) => !rowKeys().has(name))).toEqual([]);
  });

  it("name only keys the Builder answers", () => {
    const answered = new Set(PRESSES.filter(answers).map(named));
    const listed = [...rowKeys()].filter((name) => !CANVAS_GESTURES.has(name));
    expect(listed.filter((name) => !answered.has(name))).toEqual([]);
  });

  it("are what the reference shows under UI Builder, and undo is not listed as global", () => {
    render(<ShortcutsPanel onClose={() => {}} />);
    const panel = screen.getByRole("dialog");
    const sections = within(panel).getAllByText(/^(Global|UI Builder|Script Editor)$/);
    expect(sections.map((s) => s.textContent)).toEqual(["Global", "UI Builder", "Script Editor"]);
    const builderRows = sections[1].parentElement!.querySelectorAll("kbd");
    expect([...builderRows].map((k) => k.textContent)).toEqual(BUILDER_SHORTCUTS.map((row) => row.keys));
    const globalRows = sections[0].parentElement!.querySelectorAll("kbd");
    expect([...globalRows].map((k) => k.textContent)).toEqual(["Ctrl+/"]);
  });
});

/** The keys a browser reports with Caps Lock on: a letter as its capital,
 *  with Shift not held. */
function capsLockPress(key: string, k: BuilderKeys, shift = false): KeyboardEvent {
  const event = new KeyboardEvent("keydown", {
    key, ctrlKey: true, shiftKey: shift, bubbles: true, cancelable: true,
  });
  const target = document.createElement("div");
  document.body.append(target);
  target.addEventListener("keydown", (e) => handleBuilderKey(e, k));
  target.dispatchEvent(event);
  target.remove();
  return event;
}

describe("the UI Builder's toggles with Caps Lock on", () => {
  afterEach(() => useUIBuilderStore.getState().setPreviewMode(false));

  it("Ctrl+P switches preview on", () => {
    const event = capsLockPress("P", builder());
    expect(event.defaultPrevented).toBe(true);
    expect(useUIBuilderStore.getState().previewMode).toBe(true);
  });

  it("Ctrl+E shows or hides the palette", () => {
    const k = builder();
    const event = capsLockPress("E", k);
    expect(event.defaultPrevented).toBe(true);
    expect(k.togglePalette).toHaveBeenCalledTimes(1);
  });

  it("Ctrl+Shift+P and Ctrl+Shift+E are not toggles, with Caps Lock on or off", () => {
    for (const key of ["P", "E", "p", "e"]) {
      const k = builder();
      const event = capsLockPress(key, k, true);
      expect([key, event.defaultPrevented]).toEqual([key, false]);
      expect(k.togglePalette).not.toHaveBeenCalled();
      expect(useUIBuilderStore.getState().previewMode).toBe(false);
    }
  });
});
