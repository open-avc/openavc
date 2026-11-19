/** THE Programmer's reading of `/api/startup-status`: where the server is in
 *  its life, from its run-state record (middleware/startup_splash.py), and
 *  the version that started. Both of the Programmer's waits on a server read
 *  it here: the restart dialog (components/shared/restartWait.ts) and a tab
 *  whose connection came back (api/serverReady.ts). The server answers it
 *  from the moment it listens, through a failed start and on through its
 *  stop, and lets a page on its own network read it from another address. */

export type ServerPhase = "starting" | "ready" | "failed" | "stopping";

export interface StartupStatus {
  phase: ServerPhase;
  /** The OpenAVC version that started; "" from a server too old to say. */
  version: string;
  /** Why the start failed: only with a failed start. */
  error: string | null;
  /** A failed start that waits on the server's page for Start again. */
  held: boolean;
  /** Seconds until a failed start ends to be started again, else null. */
  retryIn: number | null;
}

/** One ask: whether anything answered, and the status when the answer is
 *  one (null for another server, a proxy's error page, a refusal). */
export interface StatusProbe {
  answered: boolean;
  status: StartupStatus | null;
}

const PHASES: ReadonlySet<string> = new Set(["starting", "ready", "failed", "stopping"]);

/** The status in an answer's body, or null when the body is not one. A
 *  server older than the phase says only `ready` and `error`. */
export function readStartupStatus(body: unknown): StartupStatus | null {
  const b = asRecord(body);
  const phase = b ? phaseOf(b) : null;
  if (!b || !phase) return null;
  return {
    phase,
    version: textOr(b.version, ""),
    error: phase === "failed" ? textOr(b.error, null) : null,
    held: b.held === true,
    retryIn: typeof b.retry_in === "number" ? b.retry_in : null,
  };
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : null;
}

function textOr<T>(value: unknown, fallback: T): string | T {
  return typeof value === "string" && value ? value : fallback;
}

function phaseOf(b: Record<string, unknown>): ServerPhase | null {
  if (typeof b.phase === "string") return PHASES.has(b.phase) ? (b.phase as ServerPhase) : null;
  if (typeof b.ready !== "boolean") return null;
  if (b.ready) return "ready";
  return b.error ? "failed" : "starting";
}

/** Ask for the status at `url`. Never throws. */
export async function askStartupStatus(url: string, init?: RequestInit): Promise<StatusProbe> {
  let res: Response;
  try {
    res = await fetch(url, init);
  } catch {
    return { answered: false, status: null };
  }
  if (!res.ok) return { answered: true, status: null };
  try {
    return { answered: true, status: readStartupStatus(await res.json()) };
  } catch {
    return { answered: true, status: null };
  }
}
