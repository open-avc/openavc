import { describe, expect, it } from "vitest";
import { decideProjectReload } from "./projectReload";

const clean = { saving: false, dirty: false, revision: "r1" };
const dirty = { saving: false, dirty: true, revision: "r1" };

describe("decideProjectReload", () => {
  it("treats a broadcast carrying this tab's id as its own", () => {
    expect(decideProjectReload(dirty, { type: "project.reloaded", revision: "r2", origin_client: "me" }, "me")).toBe("own");
    expect(decideProjectReload(clean, { type: "project.reloaded", revision: "r2", origin_client: "me" }, "me")).toBe("own");
  });

  it("treats a broadcast during its own project save as its own", () => {
    expect(decideProjectReload({ ...dirty, saving: true }, { type: "project.reloaded", revision: "r2" }, "me")).toBe("own");
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
