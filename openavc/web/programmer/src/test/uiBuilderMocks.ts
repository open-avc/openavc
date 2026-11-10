// The UI Builder page's stubbed children and mocked modules: each stub
// records the props it was last drawn with, so a test can call the page's
// callbacks the way the real child would. Kept apart from the harness, which
// imports the real stores: a mock factory that loaded the stores would wait on
// the very module it is building.
//
// Each test file mocks with
//   vi.mock("<path>", async () => (await import("../test/uiBuilderMocks")).stubs.<Name>)
import { createElement as h, Fragment, useEffect, type ReactNode } from "react";
import { vi } from "vitest";

/* eslint-disable @typescript-eslint/no-explicit-any */

/** The props each stubbed child was last drawn with, by component name. */
export const seen: Record<string, any> = {};

/** How many times each stubbed child was mounted and unmounted: a child that
 *  keeps state of its own (the canvas's panel frame, the Theme Studio's
 *  working copy) loses it on a remount, which its props alone cannot show. */
export const mounts: Record<string, { mounted: number; unmounted: number }> = {};

function useMountCount(name: string) {
  useEffect(() => {
    const m = (mounts[name] ??= { mounted: 0, unmounted: 0 });
    m.mounted++;
    return () => {
      m.unmounted++;
    };
  }, [name]);
}

function recorder(name: string) {
  return (props: any) => {
    seen[name] = props;
    useMountCount(name);
    return h("div", { "data-testid": `stub-${name}` });
  };
}

export const stubs = {
  ElementPalette: { ElementPalette: recorder("ElementPalette") },
  OutlinePanel: { OutlinePanel: recorder("OutlinePanel") },
  Canvas: { Canvas: recorder("Canvas") },
  CanvasToolbar: {
    CanvasToolbar: (props: any) => {
      seen.CanvasToolbar = props;
      useMountCount("CanvasToolbar");
      return h("div", { "data-testid": "stub-CanvasToolbar" }, props.trailing ?? null);
    },
  },
  PropertiesPanel: { PropertiesPanel: recorder("PropertiesPanel") },
  ContextMenu: { ContextMenu: recorder("ContextMenu") },
  ThemeStudio: { ThemeStudio: recorder("ThemeStudio") },
  StylesheetEditor: { StylesheetEditor: recorder("StylesheetEditor") },
  panels: {
    PanelGroup: ({ children }: { children: ReactNode }) => h("div", { "data-testid": "panel-group" }, children),
    Panel: ({ children, defaultSize }: { children: ReactNode; defaultSize?: number }) =>
      h("div", { "data-testid": "panel", "data-size": defaultSize }, children),
    PanelResizeHandle: () => h("div", { "data-testid": "resize-handle" }),
  },
};

/** @dnd-kit/core with DndContext and DragOverlay replaced, the rest real. */
export async function dndKit(importOriginal: () => Promise<any>) {
  const real = await importOriginal();
  return {
    ...real,
    DndContext: (props: any) => {
      seen.DndContext = props;
      return h(Fragment, null, props.children);
    },
    DragOverlay: ({ children }: { children: ReactNode }) =>
      h("div", { "data-testid": "drag-overlay" }, children),
  };
}

export const toast = {
  showError: vi.fn(),
  showSuccess: vi.fn(),
  showInfo: vi.fn(),
  showWarning: vi.fn(),
};

export async function toastModule(importOriginal: () => Promise<any>) {
  return { ...(await importOriginal()), ...toast };
}

/** The ws listener the page registered, if any. */
export const ws = {
  listeners: [] as Array<(msg: Record<string, unknown>) => void>,
  unsubscribed: 0,
};

export const wsModule = {
  onMessage: (fn: (msg: Record<string, unknown>) => void) => {
    ws.listeners.push(fn);
    return () => {
      ws.listeners = ws.listeners.filter((l) => l !== fn);
      ws.unsubscribed++;
    };
  },
};

export const api = {
  listThemes: vi.fn(),
  getTunnelPrefix: vi.fn(() => ""),
};

export async function restModule(importOriginal: () => Promise<any>) {
  return { ...(await importOriginal()), ...api };
}

export const commands = { commandIssues: vi.fn() };

