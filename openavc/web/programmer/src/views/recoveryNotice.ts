/** What to say when the project on disk was not the one that loaded.
 *
 *  The server publishes `system.project_recovery*` when a boot had to fall
 *  back to a backup, or could not find one. Turning those keys into the
 *  sentence lives here rather than in the card so the wording can be tested
 *  without mounting the Dashboard, and so the card renders what it is given.
 */

export interface RecoveryNotice {
  /** Nothing was recovered, so the Dashboard shows nothing. */
  show: boolean;
  /** No backup of the project opened: an empty project or the starter is running in its place. */
  severe: boolean;
  title: string;
  body: string;
  /** The backup that was loaded, to match against the Backups list. "" when none was. */
  backupFile: string;
  /** What the one button says. It always opens the Project view. */
  action: string;
}

const NOTHING: RecoveryNotice = {
  show: false,
  severe: false,
  title: "",
  body: "",
  backupFile: "",
  action: "",
};

function defaultFormat(iso: string): string {
  const when = new Date(iso);
  return isNaN(when.getTime()) ? "" : when.toLocaleString();
}

/** A restore or an open that stopped part way: the backup it took first is
 *  what runs, so nothing saved is missing; the other project is. */
function interruptedNotice(backupFile: string, when: string): RecoveryNotice {
  const from = when ? `, from the backup taken at ${when}` : "";
  return {
    show: true,
    severe: false,
    title: "Project restored from a backup",
    body: "Opening or restoring a project did not finish before this system stopped."
      + ` This system is running the project it was replacing${from}.`
      + " Open or restore the other project again.",
    backupFile,
    action: "Open Project",
  };
}

/** The notice for the current state, or `show: false` when there is none.
 *
 *  `formatTime` is passed in rather than read from the environment so a test
 *  can pin the wording without pinning a locale.
 */
export function recoveryNotice(
  state: Record<string, unknown>,
  formatTime: (iso: string) => string = defaultFormat,
): RecoveryNotice {
  const outcome = String(state["system.project_recovery"] ?? "");
  if (outcome !== "restored" && outcome !== "reset" && outcome !== "starter") return NOTHING;

  const reason = String(state["system.project_recovery_reason"] ?? "");
  const cutoff = String(state["system.project_recovery_cutoff"] ?? "");
  const when = cutoff ? formatTime(cutoff) : "";

  // Always "restored": the server puts back the backup a replace took.
  if (reason === "interrupted") {
    return interruptedNotice(String(state["system.project_recovery_backup"] ?? ""), when);
  }

  const missing = reason === "missing";
  const opening = missing
    ? "The project file was missing at startup."
    : "The project file could not be read at startup.";

  // A missing file whose backups hold no copy of it that opens. The
  // backups that are listed are another project's, or would not open.
  if (outcome === "starter") {
    return {
      show: true,
      severe: true,
      title: "Project file missing",
      body: "The project file was missing at startup, and no backup of that project would open,"
        + " so this system is running the starter project. Earlier backups are listed under Backups.",
      backupFile: "",
      action: "Review backups",
    };
  }

  if (outcome === "reset") {
    return {
      show: true,
      severe: true,
      title: "Project could not be recovered",
      body: `${opening} No backup would open either, so this system is running an empty project.`,
      backupFile: "",
      // Not "Review backups": nothing here opened, and the Project view is
      // also where a saved project is opened, which is the way out.
      action: "Open Project",
    };
  }

  const which = when ? `the backup from ${when}` : "the most recent backup that would open";
  return {
    show: true,
    severe: false,
    title: "Project restored from a backup",
    body: `${opening} This system is running ${which},`
      + " so anything saved after that is not in this project.",
    backupFile: String(state["system.project_recovery_backup"] ?? ""),
    action: "Review backups",
  };
}
