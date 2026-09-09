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
  /** No backup opened: an empty project is running, and the saved one is still unreadable. */
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
  if (outcome !== "restored" && outcome !== "reset") return NOTHING;

  const missing = String(state["system.project_recovery_reason"] ?? "") === "missing";
  const opening = missing
    ? "The project file was missing at startup."
    : "The project file could not be read at startup.";

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

  const cutoff = String(state["system.project_recovery_cutoff"] ?? "");
  const when = cutoff ? formatTime(cutoff) : "";
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
