/** The restart dialog's wait for the server it asked to restart.
 *
 *  It asks the server's status (api/startupStatus.ts) at each address the
 *  server may come back at, a round at a time, from the moment the restart
 *  is asked, with no fixed delay first. The server says stopping from the
 *  moment it accepts a restart and never says ready again, so the first
 *  ready is the new start's, and an answer that is not a status (another
 *  server, a proxy's error page) is never taken for it. When the restart's
 *  own answer was lost, ready is trusted only once a round has heard the
 *  server going (`askedMayStillAnswer`).
 *
 *  A failed start is shown with its reason, and the wait keeps asking while
 *  the dialog is open, so a retry that starts still lands. Only rounds in
 *  which the new start was not heard count toward giving up: a start that
 *  takes its time is waited for. Silence that outlasts a normal restart, for
 *  a page expecting a new certificate, is the browser refusing it
 *  (`shouldEnterCertError`). */
import type { StartupStatus, StatusProbe } from "../../api/startupStatus";
import { shouldEnterCertError } from "./restartPollHelpers";

/** What the dialog shows while it waits. */
export type RestartWaitView =
  /** The server asked is on its way down, or nothing answers yet. */
  | { kind: "waiting" }
  /** The new start answers and is not finished; `again` after a failure. */
  | { kind: "starting"; again: boolean }
  /** The start failed, at `origin`. */
  | { kind: "failed"; origin: string; status: StartupStatus };

/** How the wait ended. */
export type RestartWaitEnd =
  | { kind: "back"; origin: string; status: StartupStatus }
  | { kind: "cert-error" }
  | { kind: "gave-up" }
  | { kind: "cancelled" };

export interface RestartWaitOptions {
  /** Where to ask, best first. */
  origins: string[];
  /** Rounds without hearing the new start before giving up. */
  attempts: number;
  expectsNewCert: boolean;
  /** The restart's answer was lost: the server asked may not be restarting. */
  askedMayStillAnswer: boolean;
  probe: (origin: string) => Promise<StatusProbe>;
  /** The pause between rounds. */
  pause: () => Promise<void>;
  cancelled: () => boolean;
  show: (view: RestartWaitView) => void;
}

interface Heard {
  origin: string;
  probe: StatusProbe;
}

const CANCELLED: RestartWaitEnd = { kind: "cancelled" };

export async function waitForRestart(o: RestartWaitOptions): Promise<RestartWaitEnd> {
  const rounds = new Rounds(o);
  for (let round = 0; ; round++) {
    if (o.cancelled()) return CANCELLED;
    if (rounds.givenUp()) return { kind: "gave-up" };
    const heard = await askEach(o);
    const end = heard ? rounds.take(heard, round) : CANCELLED;
    if (end) return end;
    await o.pause();
  }
}

/** What the rounds so far have heard. */
class Rounds {
  private untrusted: boolean;
  private failedOnce = false;
  private unheard = 0;
  private silent = 0;
  private readonly o: RestartWaitOptions;

  constructor(o: RestartWaitOptions) {
    this.o = o;
    this.untrusted = o.askedMayStillAnswer;
  }

  /** Nothing heard of the new start for the attempts, and no failure. */
  givenUp(): boolean {
    return !this.failedOnce && this.unheard >= this.o.attempts;
  }

  /** One round's answers: how the wait ends, or null to ask again. */
  take(heard: Heard[], round: number): RestartWaitEnd | null {
    const ready = this.trustedReady(heard);
    if (ready) return { kind: "back", origin: ready.origin, status: ready.probe.status! };
    this.show(heard);
    this.silent = heard.some((h) => h.probe.answered) ? 0 : this.silent + 1;
    return shouldEnterCertError(this.o.expectsNewCert, this.silent, round) ? { kind: "cert-error" } : null;
  }

  /** A ready answer the wait can take: once a round without one has heard
   *  the server asked going. */
  private trustedReady(heard: Heard[]): Heard | null {
    const ready = saying(heard, "ready");
    if (!ready) this.untrusted = false;
    return this.untrusted ? null : (ready ?? null);
  }

  private show(heard: Heard[]): void {
    const failed = saying(heard, "failed");
    const starting = saying(heard, "starting") !== undefined;
    this.failedOnce ||= failed !== undefined;
    if (!failed && !starting) this.unheard += 1;
    this.o.show(viewOf(failed, starting, this.failedOnce));
  }
}

function saying(heard: Heard[], phase: StartupStatus["phase"]): Heard | undefined {
  return heard.find((h) => h.probe.status?.phase === phase);
}

/** One round: each origin asked in turn, or null once cancelled. */
async function askEach(o: RestartWaitOptions): Promise<Heard[] | null> {
  const heard: Heard[] = [];
  for (const origin of o.origins) {
    if (o.cancelled()) return null;
    heard.push({ origin, probe: await o.probe(origin) });
  }
  return o.cancelled() ? null : heard;
}

function viewOf(failed: Heard | undefined, starting: boolean, failedOnce: boolean): RestartWaitView {
  if (failed) return { kind: "failed", origin: failed.origin, status: failed.probe.status! };
  // After a failure, a start the service manager or Start again makes.
  if (starting || failedOnce) return { kind: "starting", again: failedOnce };
  return { kind: "waiting" };
}
