import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

// The second door for 2b: the person laying out the page drops a control onto
// the element they are configuring. What this pins is that the drop UPLOADS —
// the box looked like a drop target long before it accepted one, which is the
// worst version of this: a dashed rectangle that swallows a folder silently.

const listCustomUiFiles = vi.fn();
const uploadCustomUiFiles = vi.fn();

vi.mock("../../../api/customUiClient", () => ({
  listCustomUiFiles: (...a: unknown[]) => listCustomUiFiles(...a),
  uploadCustomUiFiles: (...a: unknown[]) => uploadCustomUiFiles(...a),
}));

import { CustomControlConfig } from "./CustomControlConfig";
import { useUiFilesStore } from "../../../store/uiFilesStore";

function folderTransfer() {
  const batches = [
    [
      {
        isFile: true,
        isDirectory: false,
        name: "index.html",
        file: (ok: (f: File) => void) => ok(new File(["<p>"], "index.html")),
      },
      {
        isFile: false,
        isDirectory: true,
        name: "img",
        createReader: () => {
          const inner = [
            [
              {
                isFile: true,
                isDirectory: false,
                name: "floor.png",
                file: (ok: (f: File) => void) => ok(new File(["x"], "floor.png")),
              },
            ],
            [],
          ];
          return { readEntries: (ok: (e: unknown[]) => void) => ok(inner.shift() ?? []) };
        },
      },
    ],
    [],
  ];
  return {
    items: [
      {
        webkitGetAsEntry: () => ({
          isFile: false,
          isDirectory: true,
          name: "room_map",
          createReader: () => ({
            readEntries: (ok: (e: unknown[]) => void) => ok(batches.shift() ?? []),
          }),
        }),
      },
    ],
    files: [],
  };
}

describe("dropping a control onto the element", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    listCustomUiFiles.mockResolvedValue({
      files: [], total_size: 0, max_total_size: 1, max_file_size: 1,
    });
    uploadCustomUiFiles.mockResolvedValue({ written: ["room_map/index.html"], skipped: [] });
    useUiFilesStore.setState({ version: 0 });
  });

  it("uploads every file with the folder it sat in", async () => {
    render(<CustomControlConfig file="" config={{}} onChange={vi.fn()} />);

    fireEvent.drop(screen.getByTestId("custom-ui-drop"), { dataTransfer: folderTransfer() });

    await waitFor(() => expect(uploadCustomUiFiles).toHaveBeenCalled());
    const entries = uploadCustomUiFiles.mock.calls[0][0] as { file: File; folder: string }[];
    expect(entries.map((e) => `${e.folder}/${e.file.name}`)).toEqual([
      "room_map/index.html",
      "room_map/img/floor.png",
    ]);
  });

  it("redraws the design canvas afterwards", async () => {
    render(<CustomControlConfig file="" config={{}} onChange={vi.fn()} />);

    fireEvent.drop(screen.getByTestId("custom-ui-drop"), { dataTransfer: folderTransfer() });

    // A control's markup is not project data, so this counter is the only
    // thing that tells the canvas to draw the new version.
    await waitFor(() => expect(useUiFilesStore.getState().version).toBe(1));
  });

  it("says which files the folder could not hold", async () => {
    uploadCustomUiFiles.mockResolvedValue({ written: ["room_map/index.html"], skipped: ["a.exe"] });
    render(<CustomControlConfig file="" config={{}} onChange={vi.fn()} />);

    fireEvent.drop(screen.getByTestId("custom-ui-drop"), { dataTransfer: folderTransfer() });

    expect(await screen.findByText(/Skipped 1 file/)).toBeInTheDocument();
  });

  it("still lists the pages on disk to point the element at", async () => {
    listCustomUiFiles.mockResolvedValue({
      files: [
        { path: "room_map/index.html", size: 1, modified: 0 },
        { path: "room_map/map.css", size: 1, modified: 0 },
      ],
      total_size: 2, max_total_size: 1, max_file_size: 1,
    });
    render(<CustomControlConfig file="" config={{}} onChange={vi.fn()} />);

    // Pages are entry points; the CSS beside one is fetched by the page.
    expect(await screen.findByRole("option", { name: "room_map/index.html" })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "room_map/map.css" })).not.toBeInTheDocument();
  });
});
