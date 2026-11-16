import { describe, expect, it } from "vitest";
import { importedWithNotices } from "./installedDriverList";

// An import that brought in a driver file with mistakes in it says them.

describe("a driver that loaded with mistakes in its file", () => {
  it("says them after an import", () => {
    expect(
      importedWithNotices("acme_widget", ", connected 1 waiting device(s)", [
        "on_connect: must be a list; write a single entry as a one-item list",
        "version: must be a string; put it in quotes",
      ]),
    ).toBe(
      'Imported driver "acme_widget", connected 1 waiting device(s). Its file has mistakes the ' +
        "Driver Builder and Code view would not save; fix them in the file: on_connect: must be a " +
        "list; write a " +
        "single entry as a one-item list; version: must be a string; put it in quotes",
    );
  });
});
