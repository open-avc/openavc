import { describe, expect, it } from "vitest";
import type { InstalledDriver } from "../../api/types";
import { importedWithNotices, loadNoticesOf } from "./installedDriverList";

// A driver file that loaded but has mistakes in it: its Installed entry
// carries them, and an import that brought it in says them.

const entry = (over: Partial<InstalledDriver>): InstalledDriver => ({
  id: "acme_widget",
  name: "Acme Widget",
  format: "avcdriver",
  filename: "acme_widget.avcdriver",
  version: "1.0.0",
  ...over,
});

describe("a driver that loaded with mistakes in its file", () => {
  it("lists them from its Installed entry, and nothing for one without", () => {
    const byId = new Map([
      ["acme_widget", entry({ load_notices: ["version: must be a string; put it in quotes"] })],
      ["acme_good", entry({ id: "acme_good" })],
    ]);
    expect(loadNoticesOf(byId, "acme_widget")).toEqual(["version: must be a string; put it in quotes"]);
    expect(loadNoticesOf(byId, "acme_good")).toEqual([]);
    expect(loadNoticesOf(byId, "acme_absent")).toEqual([]);
  });

  it("says them after an import", () => {
    expect(
      importedWithNotices("acme_widget", ", connected 1 waiting device(s)", [
        "on_connect: must be a list; write a single entry as a one-item list",
        "version: must be a string; put it in quotes",
      ]),
    ).toBe(
      'Imported driver "acme_widget", connected 1 waiting device(s). Its file has mistakes the ' +
        "Driver Builder would not save; fix them in the file: on_connect: must be a list; write a " +
        "single entry as a one-item list; version: must be a string; put it in quotes",
    );
  });
});
