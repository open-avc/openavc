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

  it("says the starter is running when a missing file left no backup of its project that opens", () => {
    const n = recoveryNotice({
      "system.project_recovery": "starter",
      "system.project_recovery_reason": "missing",
    }, at);
    expect(n.show).toBe(true);
    expect(n.severe).toBe(true);
    expect(n.title).toBe("Project file missing");
    expect(n.body).toBe(
      "The project file was missing at startup, and no backup of that project could be opened,"
      + " so this system is running the starter project. Earlier backups are listed under Backups.",
    );
    expect(n.backupFile).toBe("");
    expect(n.action).toBe("Review backups");
  });

  it("says a replace that did not finish put back the project it was replacing", () => {
    const n = recoveryNotice({
      "system.project_recovery": "restored",
      "system.project_recovery_reason": "interrupted",
      "system.project_recovery_backup": "backups/backup_20260903_211430_before_restore.zip",
      "system.project_recovery_cutoff": "2026-09-03T21:14:30+00:00",
    }, at);
    expect(n.show).toBe(true);
    expect(n.severe).toBe(false);
    expect(n.body).toBe(
      "Opening or restoring a project did not finish before this system stopped."
      + " This system is running the project it was replacing, from the backup taken at 3 Sep 2026, 21:14."
      + " Open or restore the other project again.",
    );
    expect(n.backupFile).toBe("backups/backup_20260903_211430_before_restore.zip");
    expect(n.action).toBe("Open Project");
  });

  it("says a replace that did not finish could not be put back, and where its files are kept", () => {
    const n = recoveryNotice({
      "system.project_recovery": "unfinished",
      "system.project_recovery_reason": "interrupted",
      "system.project_recovery_backup": "backups/backup_20260903_211430_before_recovery_opening_or_restoring.zip",
    }, at);
    expect(n.show).toBe(true);
    expect(n.severe).toBe(true);
    expect(n.title).toBe("Opening or restoring did not finish");
    expect(n.body).toBe(
      "Opening or restoring a project did not finish before this system stopped,"
      + " and the project before it could not be put back."
      + " This system is running the project files as they were left, which may mix the two projects."
      + " Open or restore the project you want."
      + " The files it had already moved out are kept under Backups:",
    );
    expect(n.backupFile).toBe("backups/backup_20260903_211430_before_recovery_opening_or_restoring.zip");
    expect(n.action).toBe("Open Project");
  });

  it("says nothing was kept when an unfinished replace had moved nothing out", () => {
    const n = recoveryNotice({
      "system.project_recovery": "unfinished",
      "system.project_recovery_reason": "interrupted",
    }, at);
    expect(n.body).not.toContain("kept under Backups");
    expect(n.backupFile).toBe("");
  });

  it("names the backups it tried when none would open", () => {
    const n = recoveryNotice({
      "system.project_recovery": "reset",
      "system.project_recovery_reason": "unreadable",
      "system.project_recovery_tried":
        "backups/backup_20260903_211430_auto.zip\nbackups/backup_20260903_204430_auto.zip",
    }, at);
    expect(n.body).toContain("No backup of it could be opened either");
    expect(n.triedHeading).toBe("Backups that would not open:");
    expect(n.triedFiles).toEqual([
      "backups/backup_20260903_211430_auto.zip",
      "backups/backup_20260903_204430_auto.zip",
    ]);
    expect(n.triedMore).toBe("");
  });

  it("names the backups it tried beside the starter", () => {
    const n = recoveryNotice({
      "system.project_recovery": "starter",
      "system.project_recovery_reason": "missing",
      "system.project_recovery_tried": "backups/backup_20260903_211430_auto.zip",
    }, at);
    expect(n.title).toBe("Project file missing");
    expect(n.triedFiles).toEqual(["backups/backup_20260903_211430_auto.zip"]);
  });

  it("lists the five newest tried and counts the rest", () => {
    const names = Array.from({ length: 7 }, (_, i) => `backups/backup_2026090${i}_000000_auto.zip`);
    const n = recoveryNotice({
      "system.project_recovery": "reset",
      "system.project_recovery_tried": names.join("\n"),
    }, at);
    expect(n.triedFiles).toEqual(names.slice(0, 5));
    expect(n.triedMore).toBe("and 2 more, listed under Backups");
  });

  it("lists nothing tried when none was, or when a backup opened", () => {
    const none = recoveryNotice({
      "system.project_recovery": "reset",
      "system.project_recovery_tried": "",
    }, at);
    expect(none.triedHeading).toBe("");
    expect(none.triedFiles).toEqual([]);
    const restored = recoveryNotice({
      "system.project_recovery": "restored",
      "system.project_recovery_reason": "unreadable",
      "system.project_recovery_tried": "backups/backup_20260903_211430_auto.zip",
    }, at);
    expect(restored.triedFiles).toEqual([]);
    expect(recoveryNotice({}).triedFiles).toEqual([]);
  });

  it("ignores an outcome it does not know", () => {
    expect(recoveryNotice({ "system.project_recovery": "something_else" }).show).toBe(false);
  });
});
