// The UI Builder page with its children stubbed out: each stub records the
// props it was last drawn with, so a test can call the page's callbacks the
// way the real child would and read what the page did with them. The project
// store and the Builder store are the real ones (undo reads the project store
// itself), with the save calls replaced by spies.
//
// The stubs and mocked modules are in uiBuilderMocks.ts.
import { createElement as h, type ReactNode } from "react";
import { vi } from "vitest";
import { act, render } from "@testing-library/react";
import type { MasterElement, ProjectConfig, UIElement, UIPage } from "../api/types";
import { useProjectStore } from "../store/projectStore";
import { useUIBuilderStore } from "../store/uiBuilderStore";
import { useNavigationStore } from "../store/navigationStore";
import { usePluginStore } from "../store/pluginStore";
import { api, commands, mounts, seen, toast, ws } from "./uiBuilderMocks";

export { api, commands, mounts, seen, toast, ws };

/* eslint-disable @typescript-eslint/no-explicit-any */

// --- The invented project ---

export const THEMES = [
  { id: "dark-default", name: "Dark", version: "1", author: "", description: "", preview_colors: [], variables: { panel_bg: "#101010" }, source: "builtin" },
  { id: "paper", name: "Paper", version: "1", author: "", description: "", preview_colors: [], variables: { panel_bg: "#fafafa" }, source: "builtin" },
  { id: "light-ish", name: "No bg", version: "1", author: "", description: "", preview_colors: [], variables: {}, source: "builtin" },
  { id: "minimal", name: "Minimal", version: "1", author: "", description: "", preview_colors: [], variables: {}, source: "builtin" },
];

export function el(id: string, type: string, over: Partial<UIElement> = {}): UIElement {
  return { id, type, label: id, style: {}, ...over } as unknown as UIElement;
}

export function page(id: string, elements: UIElement[], placements: Record<string, { x: number; y: number; w: number; h: number }>, over: Partial<UIPage> = {}): UIPage {
  return {
    id,
    name: id[0].toUpperCase() + id.slice(1),
    snap: { enabled: true, x: 5, y: 4 },
    elements,
    layouts: [
      { id: `${id}_land`, orientation: "landscape", primary: true, placements, hidden: [] },
      { id: `${id}_port`, orientation: "portrait", primary: false, inherits: `${id}_land`, placements: {}, hidden: [] },
    ],
    ...over,
  } as UIPage;
}

export function master(id: string, over: Partial<MasterElement> = {}): MasterElement {
  return {
    ...el(id, "label"),
    pages: "*",
    hidden: false,
    placements: { landscape: { x: 10, y: 10, w: 20, h: 10 }, portrait: { x: 50, y: 50, w: 30, h: 8 } },
    ...over,
  } as MasterElement;
}

/** Two pages; Main holds a button, a label and a group with one child. */
export function makeProject(over: { pages?: UIPage[]; masters?: MasterElement[]; scripts?: unknown[]; settings?: Record<string, unknown> } = {}): ProjectConfig {
  const pages = over.pages ?? [
    page(
      "main",
      [el("btn_1", "button"), el("lbl_1", "label", { style: { bg_color: "#111", text_color: "#eee", font_size: 2 } as never }), el("grp_1", "group"), el("in_1", "button", { parent: "grp_1" } as never)],
      {
        btn_1: { x: 10, y: 10, w: 20, h: 10 },
        lbl_1: { x: 40, y: 10, w: 20, h: 10 },
        grp_1: { x: 50, y: 50, w: 40, h: 40 },
        in_1: { x: 10, y: 10, w: 50, h: 50 },
      },
    ),
    page("other", [el("btn_9", "button")], { btn_9: { x: 0, y: 0, w: 10, h: 10 } }),
  ];
  return {
    devices: [],
    macros: [{ id: "m1", name: "M1", steps: [{ action: "state.set", key: "ui.btn_1.label", value: "x" }] }],
    variables: [{ id: "v1", source_key: "ui.btn_1.value" }],
    scripts: over.scripts ?? [],
    ui: {
      settings: {
        theme: "dark",
        theme_id: "dark-default",
        theme_overrides: { accent: "#123456" },
        accent_color: "#ff0000",
        font_family: "monospace",
        lock_code: "",
        idle_timeout_seconds: 0,
        idle_page: "main",
        page_transition: "none",
        page_transition_duration: 200,
        ...(over.settings ?? {}),
      },
      pages,
      master_elements: over.masters ?? [master("m_1")],
      custom_css: ".x{}",
    },
  } as unknown as ProjectConfig;
}

const initialBuilder = useUIBuilderStore.getState();
const initialPlugins = usePluginStore.getState();

export const saves = {
  save: vi.fn(async () => {}),
  debouncedSave: vi.fn(),
  flushSave: vi.fn(),
};

/** Fresh stores around `project`, with nothing selected yet. */
export function seed(project: ProjectConfig | null = makeProject(), builder: Record<string, unknown> = {}) {
  useUIBuilderStore.setState({ ...initialBuilder, undoStack: [], redoStack: [], clipboard: null, contextMenu: null, ...builder }, true);
  usePluginStore.setState(initialPlugins, true);
  useProjectStore.setState({
    project,
    dirty: false,
    saving: false,
    error: null,
    conflictDetected: false,
    ...saves,
  } as never);
  useNavigationStore.setState({ pendingFocus: null } as never);
}

export function resetMocks() {
  for (const k of Object.keys(seen)) delete seen[k];
  for (const k of Object.keys(mounts)) delete mounts[k];
  ws.listeners = [];
  ws.unsubscribed = 0;
  for (const f of [...Object.values(toast), ...Object.values(saves), api.listThemes, commands.commandIssues]) f.mockReset();
  api.getTunnelPrefix.mockImplementation(() => "");
  saves.save.mockImplementation(async () => {});
  api.listThemes.mockResolvedValue(THEMES);
  commands.commandIssues.mockResolvedValue(new Map());
}

/** The theme the page fetches by id; set per test before rendering. */
export const themeFetch = {
  byId: {} as Record<string, unknown>,
  calls: [] as string[],
  fail: false,
};

export function installFetch() {
  themeFetch.byId = {};
  themeFetch.calls = [];
  themeFetch.fail = false;
  globalThis.fetch = vi.fn(async (url: string) => {
    themeFetch.calls.push(String(url));
    if (themeFetch.fail) throw new Error("offline");
    const id = String(url).split("/").pop()!;
    const theme = themeFetch.byId[id];
    return { ok: theme !== undefined, json: async () => theme } as Response;
  }) as never;
}

/** Let the effects' promises settle. */
export async function settle() {
  await act(async () => {
    for (let i = 0; i < 5; i++) await Promise.resolve();
  });
}

export const project = () => useProjectStore.getState().project!;
export const ui = () => project().ui;
export const pageOf = (id: string) => ui().pages.find((p) => p.id === id)!;
export const builder = () => useUIBuilderStore.getState();
export const undoLog = () => builder().undoStack.map((e) => [e.description, Object.keys(e.snapshot).sort().join(",")]);

/** Press a key at the document, as the page's listener hears it. */
export function press(key: string, mods: { ctrl?: boolean; meta?: boolean; shift?: boolean; alt?: boolean } = {}, target: EventTarget = document.body) {
  const ev = new KeyboardEvent("keydown", {
    key,
    ctrlKey: !!mods.ctrl,
    metaKey: !!mods.meta,
    shiftKey: !!mods.shift,
    altKey: !!mods.alt,
    bubbles: true,
    cancelable: true,
  });
  act(() => {
    target.dispatchEvent(ev);
  });
  return ev;
}

export async function renderPage(View: () => ReactNode) {
  const r = render(h(View as never));
  await settle();
  return r;
}
