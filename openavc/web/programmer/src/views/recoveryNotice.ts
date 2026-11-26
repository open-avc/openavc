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
  /** The backup that was loaded, or the one keeping what an unfinished
   *  replace had moved out, to match against the Backups list. "" when none. */
  backupFile: string;
  /** Above the backups tried when none would open. "" when none was tried. */
  triedHeading: string;
  /** The newest of the backups tried, to match against the Backups list. */
  triedFiles: string[];
  /** How many more were tried than `triedFiles` lists. "" when it lists them all. */
  triedMore: string;
  /** What the one button says. It always opens the Project view. */
  action: string;
}

const NOTHING: RecoveryNotice = {
  show: false,
  severe: false,
  title: "",
  body: "",
  backupFile: "",
  triedHeading: "",
  triedFiles: [],
  triedMore: "",
  action: "",
};

/** The tried backups a notice lists by name; the rest are counted. */
const TRIED_SHOWN = 5;

type TriedList = Pick<RecoveryNotice, "triedHeading" | "triedFiles" | "triedMore">;

const NONE_TRIED: TriedList = { triedHeading: "", triedFiles: [], triedMore: "" };

/** `system.project_recovery_tried`, one backup's name a line, newest first. */
function triedList(value: string): TriedList {
  const names = value.split("\n").map((name) => name.trim()).filter(Boolean);
  if (names.length === 0) return NONE_TRIED;
  const rest = names.length - TRIED_SHOWN;
  return {
    triedHeading: "Backups that would not open:",
    triedFiles: names.slice(0, TRIED_SHOWN),
    triedMore: rest > 0 ? `and ${rest} more, listed under Backups` : "",
  };
}

/** The record as the notices read it, taken from state once. */
interface Reading {
  /** The sentence that says what was wrong with the project file. */
  opening: string;
  /** When the backup that runs was taken, as the person reads a time. "" when unknown. */
  when: string;
  /** The backup that runs. "" when none does. */
  backupFile: string;
  /** The backups tried when none would open. */
  tried: TriedList;
}

function defaultFormat(iso: string): string {
  const when = new Date(iso);
  return isNaN(when.getTime()) ? "" : when.toLocaleString();
}

/** A restore or an open that stopped part way: the backup it took first is
 *  what runs, so nothing saved is missing; the other project is. */
function interruptedNotice({ backupFile, when }: Reading): RecoveryNotice {
  const from = when ? `, from the backup taken at ${when}` : "";
  return {
    show: true,
    severe: false,
    title: "Project restored from a backup",
    body: "Opening or restoring a project did not finish before this system stopped."
      + ` This system is running the project it was replacing${from}.`
      + " Open or restore the other project again.",
    backupFile,
    ...NONE_TRIED,
    action: "Open Project",
  };
}

/** A restore or an open that stopped part way, whose backup could not be
 *  put back (gone, damaged, or none taken): the files run as it left them, and what it had moved out is
 *  kept under Backups (`backupFile`) when there was any. */
function unfinishedNotice({ backupFile }: Reading): RecoveryNotice {
  const kept = backupFile ? " The files it had already moved out are kept under Backups:" : "";
  return {
    show: true,
    severe: true,
    title: "Opening or restoring did not finish",
    body: "Opening or restoring a project did not finish before this system stopped,"
      + " and the project before it could not be put back."
      + " This system is running the project files as they were left, which may mix the two projects."
      + " Open or restore the project you want."
      + kept,
    backupFile,
    ...NONE_TRIED,
    action: "Open Project",
  };
}

/** A missing file whose backups hold no copy of it that opens. The
 *  backups that are listed are another project's, or would not open:
 *  `tried` names the ones that would not. */
function starterNotice({ tried }: Reading): RecoveryNotice {
  return {
    show: true,
    severe: true,
    title: "Project file missing",
    body: "The project file was missing at startup, and no backup of that project could be opened,"
      + " so this system is running the starter project. Earlier backups are listed under Backups.",
    backupFile: "",
    ...tried,
    action: "Review backups",
  };
}

/** Nothing opened: an empty project is running. */
function resetNotice({ opening, tried }: Reading): RecoveryNotice {
  return {
    show: true,
    severe: true,
    title: "Project could not be recovered",
    body: `${opening} No backup of it could be opened either, so this system is running an empty project.`,
    backupFile: "",
    ...tried,
    // Not "Review backups": nothing here opened, and the Project view is
    // also where a saved project is opened, which is the way out.
    action: "Open Project",
  };
}

/** A backup opened in place of the project file. */
function restoredNotice({ opening, when, backupFile }: Reading): RecoveryNotice {
  const which = when ? `the backup from ${when}` : "the most recent backup that would open";
  return {
    show: true,
    severe: false,
    title: "Project restored from a backup",
    body: `${opening} This system is running ${which},`
      + " so anything saved after that is not in this project.",
    backupFile,
    ...NONE_TRIED,
    action: "Review backups",
  };
}

const OUTCOMES = new Set(["restored", "reset", "starter", "unfinished"]);

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
  if (!OUTCOMES.has(outcome)) return NOTHING;

  const reason = String(state["system.project_recovery_reason"] ?? "");
  const cutoff = String(state["system.project_recovery_cutoff"] ?? "");
  const reading: Reading = {
    opening: reason === "missing"
      ? "The project file was missing at startup."
      : "The project file could not be read at startup.",
    when: cutoff ? formatTime(cutoff) : "",
    backupFile: String(state["system.project_recovery_backup"] ?? ""),
    // Named only when no backup opened; a restored notice names the one that did.
    tried: triedList(String(state["system.project_recovery_tried"] ?? "")),
  };

  if (outcome === "starter") return starterNotice(reading);
  if (outcome === "unfinished") return unfinishedNotice(reading);
  // Otherwise "restored": the server put back the backup a replace took.
  if (reason === "interrupted") return interruptedNotice(reading);
  return outcome === "reset" ? resetNotice(reading) : restoredNotice(reading);
}
