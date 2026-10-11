import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { ScriptFileTree } from "./ScriptFileTree";

// A create the server refuses (no admin password yet, a name already taken)
// leaves the form open with what was typed, so the person can fix it and try
// again instead of typing it all over.

function renderTree(overrides: Partial<Parameters<typeof ScriptFileTree>[0]> = {}) {
  const props = {
    scripts: [],
    drivers: [],
    uiFiles: [],
    selectedId: null,
    selectedType: null,
    onSelectScript: vi.fn(),
    onSelectDriver: vi.fn(),
    onSelectUiFile: vi.fn(),
    onCreateScript: vi.fn(async () => true),
    onCreateDriver: vi.fn(),
    onCreateUiFile: vi.fn(async () => true),
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

function openScriptForm() {
  fireEvent.click(screen.getByTitle("New script"));
  const id = screen.getByPlaceholderText("Script ID (e.g. room_logic)") as HTMLInputElement;
  fireEvent.change(id, { target: { value: "room_logic" } });
  return id;
}

describe("creating a script from the Code view", () => {
  it("keeps the form and the typed id when the create is refused", async () => {
    const props = renderTree({ onCreateScript: vi.fn(async () => false) });
    openScriptForm();
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    await waitFor(() => expect(props.onCreateScript).toHaveBeenCalled());
    const id = screen.getByPlaceholderText("Script ID (e.g. room_logic)") as HTMLInputElement;
    expect(id.value).toBe("room_logic");
  });

  it("closes the form once the script is created", async () => {
    const props = renderTree();
    openScriptForm();
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    await waitFor(() => expect(props.onCreateScript).toHaveBeenCalledWith("room_logic", "room_logic.py", ""));
    await waitFor(() =>
      expect(screen.queryByPlaceholderText("Script ID (e.g. room_logic)")).not.toBeInTheDocument(),
    );
  });
});
