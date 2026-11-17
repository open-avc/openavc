import { describe, expect, it, vi } from "vitest";
import {
  actOnProjectReload,
  decideProjectReload,
  reloadStillNeeded,
  type ProjectStoreSnapshot,
} from "./projectReload";

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

  it("treats this tab's broadcast during its own project save as its own", () => {
    expect(decideProjectReload({ ...clean, saving: true }, { type: "project.reloaded", revision: "r2", origin_client: "me" }, "me")).toBe("own");
    expect(decideProjectReload({ ...dirty, saving: true }, { type: "project.reloaded", revision: "r2", origin_client: "me" }, "me")).toBe("own");
  });

  it("decides another writer's broadcast during its own project save once the save is done", () => {
    // Another tab, a learned setting, a cloud push, landing while this tab's
    // save is out: if the save lands too, the server is past the revision it
    // answers with, and a clean store has to fetch what it missed.
    expect(decideProjectReload({ ...dirty, saving: true }, { type: "project.reloaded", revision: "r2", origin_client: "someone-else" }, "me")).toBe("after-save");
    expect(decideProjectReload({ ...dirty, saving: true }, { type: "project.reloaded", revision: "r2" }, "me")).toBe("after-save");
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

describe("actOnProjectReload", () => {
  const notice = { type: "project.reloaded" as const, revision: "r9", origin_client: "someone-else" };

  function effects(during: ProjectStoreSnapshot, after: ProjectStoreSnapshot) {
    let store = during;
    let settle: () => void = () => {};
    const saved = new Promise<void>((resolve) => {
      settle = () => {
        store = after;
        resolve();
      };
    });
    return {
      fx: {
        getStore: () => store,
        whenSaved: () => saved,
        refetch: vi.fn(),
        warn: vi.fn(),
      },
      settle: () => settle(),
    };
  }

  it("fetches another writer's change once the save it landed during is done", async () => {
    const { fx, settle } = effects({ ...dirty, saving: true }, { ...clean, revision: "r2" });
    const acted = actOnProjectReload(notice, fx, "me");
    await Promise.resolve();
    expect(fx.refetch).not.toHaveBeenCalled();
    settle();
    await acted;
    expect(fx.refetch).toHaveBeenCalledWith("r9");
    expect(fx.warn).not.toHaveBeenCalled();
  });

  it("says nothing more when the save it landed during conflicted", async () => {
    const { fx, settle } = effects({ ...dirty, saving: true }, { ...dirty, conflictDetected: true });
    const acted = actOnProjectReload(notice, fx, "me");
    settle();
    await acted;
    expect(fx.refetch).not.toHaveBeenCalled();
    expect(fx.warn).not.toHaveBeenCalled();
  });

  it("does nothing for this tab's own save", async () => {
    const { fx } = effects({ ...dirty, saving: true }, clean);
    await actOnProjectReload({ ...notice, origin_client: "me" }, fx, "me");
    expect(fx.refetch).not.toHaveBeenCalled();
    expect(fx.warn).not.toHaveBeenCalled();
  });
});
