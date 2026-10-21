import { describe, expect, it } from "vitest";
import { decideProjectReload, reloadStillNeeded } from "./projectReload";

const clean = { saving: false, dirty: false, revision: "r1" };
const dirty = { saving: false, dirty: true, revision: "r1" };

describe("decideProjectReload", () => {
  it("treats a broadcast carrying this tab's id as its own while edits are unsaved", () => {
    expect(decideProjectReload(dirty, { type: "project.reloaded", revision: "r2", origin_client: "me" }, "me")).toBe("own");
  });

  it("refetches its own change when nothing is unsaved and the store is behind it", () => {
    // A child's label, a plugin uninstall, a plugin's own action: saved on the
    // server by this tab, re-synced by nobody.
    expect(decideProjectReload(clean, { type: "project.reloaded", revision: "r2", origin_client: "me" }, "me")).toBe("refetch");
  });

  it("leaves its own change alone when the store already holds it or cannot tell", () => {
    expect(decideProjectReload(clean, { type: "project.reloaded", revision: "r1", origin_client: "me" }, "me")).toBe("own");
    expect(decideProjectReload(clean, { type: "project.reloaded", origin_client: "me" }, "me")).toBe("own");
  });

  it("treats a broadcast during its own project save as its own", () => {
    expect(decideProjectReload({ ...dirty, saving: true }, { type: "project.reloaded", revision: "r2" }, "me")).toBe("own");
    expect(decideProjectReload({ ...clean, saving: true }, { type: "project.reloaded", revision: "r2", origin_client: "me" }, "me")).toBe("own");
  });

  it("refetches quietly when nothing is unsaved", () => {
    expect(decideProjectReload(clean, { type: "project.reloaded", revision: "r2", origin_client: "someone-else" }, "me")).toBe("refetch");
    expect(decideProjectReload(clean, { type: "project.reloaded", revision: "r2" }, "me")).toBe("refetch");
  });

  it("warns about another session when unsaved edits meet a newer revision", () => {
    expect(decideProjectReload(dirty, { type: "project.reloaded", revision: "r2", origin_client: "someone-else" }, "me")).toBe("warn-other-session");
  });

  it("warns generically when it cannot compare revisions", () => {
    expect(decideProjectReload(dirty, { type: "project.reloaded" }, "me")).toBe("warn-external");
    expect(decideProjectReload({ ...dirty, revision: null }, { type: "project.reloaded", revision: "r2" }, "me")).toBe("warn-external");
  });
});

describe("reloadStillNeeded", () => {
  it("skips a refetch the store has already made", () => {
    expect(reloadStillNeeded("r2", "r2")).toBe(false);
  });

  it("refetches when the store is behind or the notice names no revision", () => {
    expect(reloadStillNeeded("r1", "r2")).toBe(true);
    expect(reloadStillNeeded(null, "r2")).toBe(true);
    expect(reloadStillNeeded("r1", undefined)).toBe(true);
    expect(reloadStillNeeded("r1", null)).toBe(true);
  });
});
