import { describe, it, expect } from "vitest";
import { recoveryNotice } from "./recoveryNotice";

// A fixed formatter, so the wording is pinned without pinning a locale.
const at = () => "3 Sep 2026, 21:14";

describe("recoveryNotice", () => {
  it("says nothing on an ordinary boot", () => {
    expect(recoveryNotice({}).show).toBe(false);
    expect(recoveryNotice({ "system.project_recovery": "" }).show).toBe(false);
  });

  it("names the cut-off, because that is the line work was lost after", () => {
    const n = recoveryNotice({
      "system.project_recovery": "restored",
      "system.project_recovery_reason": "unreadable",
      "system.project_recovery_backup": "backups/backup_20260903_211430_auto.zip",
      "system.project_recovery_cutoff": "2026-09-03T21:14:30+00:00",
    }, at);
    expect(n.show).toBe(true);
    expect(n.severe).toBe(false);
    expect(n.title).toBe("Project restored from a backup");
    expect(n.body).toContain("could not be read at startup");
    expect(n.body).toContain("the backup from 3 Sep 2026, 21:14");
    expect(n.body).toContain("anything saved after that is not in this project");
    expect(n.backupFile).toBe("backups/backup_20260903_211430_auto.zip");
    expect(n.action).toBe("Review backups");
  });

  it("says the file was missing when that is what happened", () => {
    const n = recoveryNotice({
      "system.project_recovery": "restored",
      "system.project_recovery_reason": "missing",
      "system.project_recovery_cutoff": "2026-09-03T21:14:30+00:00",
    }, at);
    expect(n.body).toContain("The project file was missing at startup.");
    expect(n.body).not.toContain("could not be read");
  });

  it("still says what was lost when the record carries no cut-off", () => {
    const n = recoveryNotice({
      "system.project_recovery": "restored",
      "system.project_recovery_reason": "unreadable",
    }, at);
    expect(n.show).toBe(true);
    expect(n.body).toContain("the most recent backup that would open");
    expect(n.body).not.toContain("undefined");
    expect(n.body).not.toContain("Invalid Date");
  });

  it("drops the time rather than printing a broken one", () => {
    const n = recoveryNotice({
      "system.project_recovery": "restored",
      "system.project_recovery_cutoff": "not a date",
    });
    expect(n.body).toContain("the most recent backup that would open");
  });

  it("is louder when nothing was recovered at all", () => {
    const n = recoveryNotice({
      "system.project_recovery": "reset",
      "system.project_recovery_reason": "unreadable",
    }, at);
    expect(n.severe).toBe(true);
    expect(n.title).toBe("Project could not be recovered");
    expect(n.body).toContain("running an empty project");
    expect(n.backupFile).toBe("");
    // Not "Review backups": nothing here opened.
    expect(n.action).toBe("Open Project");
  });

  it("ignores an outcome it does not know", () => {
    expect(recoveryNotice({ "system.project_recovery": "something_else" }).show).toBe(false);
  });
});
