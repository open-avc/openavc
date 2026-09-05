import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { ScriptFileTree } from "./ScriptFileTree";

// A handler waiting for an event nothing sends is in a file nobody has opened
// -- that is the whole shape of the failure. So the mark has to be on the LIST,
// not only inside the editor, exactly as the macro lint marks its rows.

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

describe("dead handlers on the script list", () => {
  it("marks the row of a script whose handler nothing can reach", () => {
    renderTree({ deadHandlers: { router: 1 } });
    expect(screen.getByText("1 handler with no emitter")).toBeTruthy();
  });

  it("counts them, because one file can carry several", () => {
    renderTree({ deadHandlers: { router: 3 } });
    expect(screen.getByText("3 handlers with no emitter")).toBeTruthy();
  });

  it("leaves a script with nothing wrong showing its description", () => {
    renderTree({ deadHandlers: { router: 1 } });
    expect(screen.getByText("Lighting scenes")).toBeTruthy();
  });

  it("says nothing at all when no script has one", () => {
    renderTree();
    expect(screen.queryByText(/no emitter/)).toBeNull();
    expect(screen.getByText("Source routing")).toBeTruthy();
  });

  it("does not talk over a load error, which is the worse news", () => {
    renderTree({
      deadHandlers: { router: 1 },
      loadErrors: { router: "SyntaxError: invalid syntax (line 3)" },
    });
    expect(screen.queryByText(/no emitter/)).toBeNull();
    expect(screen.getByText(/SyntaxError/)).toBeTruthy();
  });
});
