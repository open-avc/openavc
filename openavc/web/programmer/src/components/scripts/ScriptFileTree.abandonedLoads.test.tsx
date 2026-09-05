import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { ScriptFileTree } from "./ScriptFileTree";

// A load the server gave up on is invisible everywhere else: the script is not
// running, a thread of its top-level code may still be, and the damage shows up
// as devices flapping. So the list has to say so, and it has to distinguish the
// one that was stopped from the one that is still going -- only the second
// needs a restart.

const SCRIPTS = [
  { id: "router", file: "router.py", enabled: true, description: "Source routing" },
  { id: "lights", file: "lights.py", enabled: true, description: "Lighting scenes" },
];

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

describe("abandoned script loads on the list", () => {
  it("says when a thread of the script is still running", () => {
    renderTree({ abandonedLoads: { router: { attempts: 1, running: true } } });
    expect(screen.getByText("Load abandoned and still running")).toBeTruthy();
  });

  it("says something quieter once the load has been stopped", () => {
    renderTree({ abandonedLoads: { router: { attempts: 2, running: false } } });
    expect(screen.getByText("Load timed out and was stopped")).toBeTruthy();
    expect(screen.queryByText(/still running/)).toBeNull();
  });

  it("leaves every other script alone", () => {
    renderTree({ abandonedLoads: { router: { attempts: 1, running: true } } });
    expect(screen.getByText("Lighting scenes")).toBeTruthy();
  });

  it("says nothing when no load was abandoned", () => {
    renderTree();
    expect(screen.queryByText(/Load abandoned/)).toBeNull();
    expect(screen.queryByText(/Load timed out/)).toBeNull();
  });

  it("does not talk over a load error, which names the actual cause", () => {
    renderTree({
      abandonedLoads: { router: { attempts: 1, running: true } },
      loadErrors: { router: "timed out during loading (>10s)" },
    });
    expect(screen.queryByText(/Load abandoned/)).toBeNull();
    expect(screen.getByText(/timed out during loading/)).toBeTruthy();
  });

  it("outranks a dead handler, which is the smaller problem", () => {
    renderTree({
      abandonedLoads: { router: { attempts: 1, running: true } },
      deadHandlers: { router: 2 },
    });
    expect(screen.getByText("Load abandoned and still running")).toBeTruthy();
    expect(screen.queryByText(/never run/)).toBeNull();
  });
});
