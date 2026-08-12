import { describe, it, expect } from "vitest";
import { filesFromDataTransfer, filesFromList, folderOf } from "./dropFiles";

// What is actually being pinned: a dropped FOLDER carries its structure in the
// filesystem-entry API and NOWHERE ELSE. The files that walk produces have an
// empty `webkitRelativePath` (only an <input webkitdirectory> fills that in),
// so a drop handler that reads the path off the File gets "" for every file
// and flattens the control into one folder. These fakes are shaped like the
// browser's: entries, a reader that hands back batches, and files with no path
// of their own.

interface FakeEntry {
  isFile: boolean;
  isDirectory: boolean;
  name: string;
  file?: (ok: (f: File) => void, err?: (e: unknown) => void) => void;
  createReader?: () => { readEntries: (ok: (e: FakeEntry[]) => void) => void };
}

function fileEntry(name: string): FakeEntry {
  return {
    isFile: true,
    isDirectory: false,
    name,
    // Deliberately no webkitRelativePath — that is the browser's behaviour.
    file: (ok) => ok(new File(["x"], name)),
  };
}

function unreadableFileEntry(name: string): FakeEntry {
  return {
    isFile: true,
    isDirectory: false,
    name,
    file: (_ok, err) => err?.(new Error("nope")),
  };
}

/** `batches` are handed out one readEntries() call at a time, then []. */
function dirEntry(name: string, batches: FakeEntry[][]): FakeEntry {
  const queue = [...batches, []];
  return {
    isFile: false,
    isDirectory: true,
    name,
    createReader: () => ({
      readEntries: (ok) => ok(queue.shift() ?? []),
    }),
  };
}

function transferOf(entries: FakeEntry[], files: File[] = []): DataTransfer {
  return {
    items: entries.map((e) => ({ webkitGetAsEntry: () => e })),
    files,
  } as unknown as DataTransfer;
}

describe("folderOf", () => {
  it("takes the folder half of a path, and nothing when there is none", () => {
    expect(folderOf("room_map/img/floor.png")).toBe("room_map/img");
    expect(folderOf("index.html")).toBe("");
    expect(folderOf("room_map\\map.css")).toBe("room_map");
  });
});

describe("a dropped folder", () => {
  it("keeps its structure, which is not on the File objects", async () => {
    const dt = transferOf([
      dirEntry("room_map", [[
        fileEntry("index.html"),
        fileEntry("map.css"),
        dirEntry("img", [[fileEntry("floor.png")]]),
      ]]),
    ]);

    const dropped = await filesFromDataTransfer(dt);

    expect(dropped.map((d) => `${d.folder}/${d.file.name}`)).toEqual([
      "room_map/index.html",
      "room_map/map.css",
      "room_map/img/floor.png",
    ]);
    // The thing that makes the walk necessary in the first place.
    for (const d of dropped) {
      expect((d.file as File & { webkitRelativePath?: string }).webkitRelativePath ?? "").toBe("");
    }
  });

  it("reads every batch, not just the first", async () => {
    // readEntries() hands back at most 100 entries per call in Chromium and
    // ends with an empty batch. A single call is the first hundred files of a
    // directory, not the directory.
    const dt = transferOf([
      dirEntry("big", [
        [fileEntry("a.js")],
        [fileEntry("b.js")],
        [fileEntry("c.js")],
      ]),
    ]);

    const dropped = await filesFromDataTransfer(dt);

    expect(dropped.map((d) => d.file.name)).toEqual(["a.js", "b.js", "c.js"]);
  });

  it("skips a file the browser will not hand over rather than losing the drop", async () => {
    const dt = transferOf([
      dirEntry("room_map", [[unreadableFileEntry("locked.png"), fileEntry("index.html")]]),
    ]);

    const dropped = await filesFromDataTransfer(dt);

    expect(dropped.map((d) => d.file.name)).toEqual(["index.html"]);
  });
});

describe("a dropped file", () => {
  it("lands at the top of the tree", async () => {
    const dt = transferOf([fileEntry("index.html")]);

    const dropped = await filesFromDataTransfer(dt);

    expect(dropped).toHaveLength(1);
    expect(dropped[0].folder).toBe("");
    expect(dropped[0].file.name).toBe("index.html");
  });

  it("falls back to the flat list when the entry API is not there", async () => {
    const dt = { items: [], files: [new File(["x"], "control.zip")] } as unknown as DataTransfer;

    const dropped = await filesFromDataTransfer(dt);

    expect(dropped.map((d) => d.file.name)).toEqual(["control.zip"]);
  });
});

describe("a picked folder", () => {
  it("uses the relative path the input DOES fill in", () => {
    const withPath = (name: string, rel: string) => {
      const f = new File(["x"], name);
      Object.defineProperty(f, "webkitRelativePath", { value: rel });
      return f;
    };

    const picked = filesFromList([
      withPath("index.html", "room_map/index.html"),
      withPath("floor.png", "room_map/img/floor.png"),
      withPath("loose.css", ""),
    ]);

    expect(picked.map((p) => p.folder)).toEqual(["room_map", "room_map/img", ""]);
  });
});
