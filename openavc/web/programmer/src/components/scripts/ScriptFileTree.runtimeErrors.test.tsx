import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { ScriptFileTree } from "./ScriptFileTree";

// A script that loads and then throws is the failure the list was blindest to:
// the script IS running, so nothing here marked it, and an integrator whose
// button handler raised saw a dead button and a scripts view with nothing
// wrong on it. The count is on the row because one failure is a glitch and
// forty is a button that has never once worked.

const SCRIPTS = [
  { id: "router", file: "router.py", enabled: true, description: "Source routing" },
  { id: "lights", file: "lights.py", enabled: true, description: "Lighting scenes" },
];

const FAILED = {
  count: 1,
  handler: "on_press",
  event: "ui.press.select_pc",
  error: "division by zero",
};

function renderTree(overrides: Partial<Parameters<typeof ScriptFileTree>[0]> = {}) {
  const props = {
    scripts: SCRIPTS,
    drivers: [],
    uiFiles: [],
    selectedId: null,
    selectedType: null,
    onSelectScript: vi.fn(),
    onSelectDriver: vi.fn(),
    onSelectUiFile: vi.fn(),
    onCreateScript: vi.fn(),
    onCreateDriver: vi.fn(),
    onCreateUiFile: vi.fn(),
    onImportDriver: vi.fn(),
    onImportUiFiles: vi.fn(),
    onExportDriver: vi.fn(),
    onDeleteScript: vi.fn(),
    onDeleteDriver: vi.fn(),
    onDeleteUiFile: vi.fn(),
    onDropUiFiles: vi.fn(),
    ...overrides,
  } as Parameters<typeof ScriptFileTree>[0];
  render(<ScriptFileTree {...props} />);
  return props;
}

describe("script runtime failures on the list", () => {
  it("marks a script that threw while running", () => {
    renderTree({ runtimeErrors: { router: FAILED } });
    expect(screen.getByText("Failed once while running")).toBeTruthy();
  });

  it("counts the failures, because a handler that throws throws every time", () => {
    renderTree({ runtimeErrors: { router: { ...FAILED, count: 12 } } });
    expect(screen.getByText("Failed 12 times while running")).toBeTruthy();
  });

  it("carries what actually failed, so the row is worth hovering", () => {
    renderTree({ runtimeErrors: { router: FAILED } });
    const row = screen.getByText("Failed once while running");
    expect(row.getAttribute("title")).toContain("on_press");
    expect(row.getAttribute("title")).toContain("ui.press.select_pc");
    expect(row.getAttribute("title")).toContain("division by zero");
  });

  it("leaves every other script alone", () => {
    renderTree({ runtimeErrors: { router: FAILED } });
    expect(screen.getByText("Lighting scenes")).toBeTruthy();
  });

  it("says nothing when every script is behaving", () => {
    renderTree();
    expect(screen.queryByText(/Failed/)).toBeNull();
  });

  it("does not talk over a load error, which means it never ran at all", () => {
    renderTree({
      runtimeErrors: { router: FAILED },
      loadErrors: { router: "NameError: name 'projector' is not defined" },
    });
    expect(screen.queryByText(/Failed once while running/)).toBeNull();
    expect(screen.getByText(/NameError/)).toBeTruthy();
  });

  it("does not talk over a runaway load, which needs a restart", () => {
    renderTree({
      runtimeErrors: { router: FAILED },
      abandonedLoads: { router: { attempts: 1, running: true } },
    });
    expect(screen.getByText("Load abandoned and still running")).toBeTruthy();
    expect(screen.queryByText(/Failed once while running/)).toBeNull();
  });

  it("outranks a dead handler, which has not failed -- it has never fired", () => {
    renderTree({
      runtimeErrors: { router: FAILED },
      deadHandlers: { router: 2 },
    });
    expect(screen.getByText("Failed once while running")).toBeTruthy();
    expect(screen.queryByText(/no emitter/)).toBeNull();
  });

  it("replaces the description, which cannot say the script is broken", () => {
    renderTree({ runtimeErrors: { router: FAILED } });
    expect(screen.queryByText("Source routing")).toBeNull();
  });
});
