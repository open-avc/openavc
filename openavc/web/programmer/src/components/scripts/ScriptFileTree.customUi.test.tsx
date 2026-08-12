import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { ScriptFileTree } from "./ScriptFileTree";

// The Code view is where hand-written code lives in this IDE, and a custom
// control is hand-written code. This pins the door: the section exists, it
// lists the project's ui/ tree grouped the way an author thinks about it (one
// control is one folder), and a folder dropped on it arrives with its
// structure intact rather than flattened.

const UI_FILES = [
  { path: "room_map/index.html", size: 2048, modified: 0 },
  { path: "room_map/map.css", size: 512, modified: 0 },
  { path: "notes.md", size: 64, modified: 0 },
];

function renderTree(overrides: Partial<Parameters<typeof ScriptFileTree>[0]> = {}) {
  const props = {
    scripts: [],
    drivers: [],
    uiFiles: UI_FILES,
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

describe("the Code view's Custom Controls section", () => {
  it("lists the project's ui/ files, grouped by control", () => {
    renderTree();

    expect(screen.getByText("Custom Controls (3)")).toBeInTheDocument();
    expect(screen.getByText("room_map/")).toBeInTheDocument();
    // Inside a control's folder the file shows its own name, not the path
    // again — the folder heading already said it.
    expect(screen.getByText("index.html")).toBeInTheDocument();
    expect(screen.getByText("map.css")).toBeInTheDocument();
    expect(screen.getByText("notes.md")).toBeInTheDocument();
  });

  it("opens a file by its full path", () => {
    const props = renderTree();

    fireEvent.click(screen.getByText("index.html"));

    expect(props.onSelectUiFile).toHaveBeenCalledWith("room_map/index.html");
  });

  it("offers to create one when the folder is empty", () => {
    const props = renderTree({ uiFiles: [] });

    fireEvent.click(screen.getByTitle("New custom control file"));
    fireEvent.change(screen.getByLabelText("New custom control file"), {
      target: { value: "lights/index.html" },
    });
    fireEvent.click(screen.getByText("Create"));

    expect(props.onCreateUiFile).toHaveBeenCalledWith("lights/index.html");
  });

  it("deletes a file", () => {
    const props = renderTree();

    fireEvent.click(screen.getAllByTitle("Delete file")[0]);

    expect(props.onDeleteUiFile).toHaveBeenCalledWith("room_map/index.html");
  });

  it("takes a dropped folder with its structure", async () => {
    const props = renderTree();

    const dataTransfer = {
      items: [
        {
          webkitGetAsEntry: () => ({
            isFile: false,
            isDirectory: true,
            name: "lights",
            createReader: () => {
              const batches = [
                [
                  {
                    isFile: true,
                    isDirectory: false,
                    name: "index.html",
                    file: (ok: (f: File) => void) => ok(new File(["<p>"], "index.html")),
                  },
                ],
                [],
              ];
              return { readEntries: (ok: (e: unknown[]) => void) => ok(batches.shift() ?? []) };
            },
          }),
        },
      ],
      files: [],
    };

    fireEvent.drop(screen.getByTestId("custom-ui-section"), { dataTransfer });

    await waitFor(() => expect(props.onDropUiFiles).toHaveBeenCalled());
    const dropped = (props.onDropUiFiles as ReturnType<typeof vi.fn>).mock.calls[0][0];
    expect(dropped).toHaveLength(1);
    expect(dropped[0].folder).toBe("lights");
    expect(dropped[0].file.name).toBe("index.html");
  });
});
