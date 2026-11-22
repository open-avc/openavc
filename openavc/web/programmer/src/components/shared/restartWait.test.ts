import { describe, it, expect } from "vitest";
import { waitForRestart, type RestartWaitView } from "./restartWait";
import type { StartupStatus, StatusProbe } from "../../api/startupStatus";
import { CERT_ERROR_MIN_ATTEMPTS, CERT_ERROR_THRESHOLD } from "./restartPollHelpers";

const HERE = "http://192.168.1.20:8080";
const THERE = "http://127.0.0.1:8080";

function status(phase: StartupStatus["phase"], more: Partial<StartupStatus> = {}): StartupStatus {
  return { phase, version: "0.41.0", error: null, held: false, retryIn: null, problems: [], ...more };
}

const said = (s: StartupStatus): StatusProbe => ({ answered: true, status: s });
const SILENT: StatusProbe = { answered: false, status: null };
const NOT_A_STATUS: StatusProbe = { answered: true, status: null };
const stopping = said(status("stopping"));
const starting = said(status("starting"));
const ready = said(status("ready"));
const failed = (more: Partial<StartupStatus> = {}) =>
  said(status("failed", { error: "Loading the project failed: bad JSON", retryIn: 60, ...more }));

/** Runs the wait against a script of rounds, one entry an origin per round;
 *  the last round repeats. */
function run(
  rounds: Array<Record<string, StatusProbe>>,
  opts: { origins?: string[]; attempts?: number; expectsNewCert?: boolean; lost?: boolean; maxRounds?: number } = {},
) {
  const origins = opts.origins ?? [HERE];
  const views: RestartWaitView[] = [];
  const asked: string[] = [];
  const events: string[] = [];
  let round = 0;
  const end = waitForRestart({
    origins,
    attempts: opts.attempts ?? 60,
    expectsNewCert: opts.expectsNewCert ?? false,
    askedMayStillAnswer: opts.lost ?? false,
    probe: async (origin) => {
      asked.push(origin);
      events.push(`probe ${origin}`);
      const script = rounds[Math.min(round, rounds.length - 1)];
      return script[origin] ?? SILENT;
    },
    pause: async () => {
      events.push("pause");
      round += 1;
    },
    cancelled: () => round >= (opts.maxRounds ?? 1000),
    show: (view) => views.push(view),
  });
  return { end, views, asked, events, rounds: () => round };
}

describe("waitForRestart", () => {
  it("asks at once, with no fixed delay first", async () => {
    const { end, events } = run([{ [HERE]: ready }]);
    await end;
    expect(events[0]).toBe(`probe ${HERE}`);
  });

  it("never takes the server it asked, on its way down, for the new one", async () => {
    const { end, views } = run([
      { [HERE]: stopping }, { [HERE]: stopping }, { [HERE]: SILENT }, { [HERE]: starting }, { [HERE]: ready },
    ]);
    await expect(end).resolves.toEqual({ kind: "back", origin: HERE, status: status("ready") });
    expect(views.map((v) => v.kind)).toEqual(["waiting", "waiting", "waiting", "starting"]);
  });

  it("shows a failed start's reason and keeps asking, so a retry that starts lands", async () => {
    const { end, views } = run([
      { [HERE]: failed({ retryIn: 60 }) }, { [HERE]: failed({ retryIn: 59 }) },
      { [HERE]: SILENT }, { [HERE]: starting }, { [HERE]: ready },
    ]);
    await expect(end).resolves.toMatchObject({ kind: "back", origin: HERE });
    expect(views).toEqual([
      { kind: "failed", origin: HERE, status: status("failed", { error: "Loading the project failed: bad JSON", retryIn: 60 }) },
      { kind: "failed", origin: HERE, status: status("failed", { error: "Loading the project failed: bad JSON", retryIn: 59 }) },
      { kind: "starting", again: true },
      { kind: "starting", again: true },
    ]);
  });

  it("never gives up on a failed start while the dialog is open", async () => {
    const { end, rounds } = run([{ [HERE]: failed({ held: true, retryIn: null }) }], { attempts: 3, maxRounds: 20 });
    await expect(end).resolves.toEqual({ kind: "cancelled" });
    expect(rounds()).toBe(20);
  });

  it("waits through a long start: only rounds the new server is not heard count", async () => {
    const script = [...Array(10).fill({ [HERE]: starting }), { [HERE]: ready }];
    const { end } = run(script, { attempts: 3 });
    await expect(end).resolves.toMatchObject({ kind: "back" });
  });

  it("gives up after the attempts with nothing heard", async () => {
    const { end, asked } = run([{ [HERE]: SILENT }], { attempts: 5 });
    await expect(end).resolves.toEqual({ kind: "gave-up" });
    expect(asked).toHaveLength(5);
  });

  it("never lands on an answer that is not this server's status", async () => {
    const { end } = run([{ [HERE]: NOT_A_STATUS }], { attempts: 4 });
    await expect(end).resolves.toEqual({ kind: "gave-up" });
  });

  it("after a lost restart answer, trusts ready only once the server has been heard going", async () => {
    const { end, rounds } = run([{ [HERE]: ready }, { [HERE]: ready }, { [HERE]: stopping }, { [HERE]: ready }], { lost: true });
    await expect(end).resolves.toMatchObject({ kind: "back" });
    expect(rounds()).toBe(3);
  });

  it("lands where the new server says ready, and shows a failure where it is", async () => {
    const one = run([{ [HERE]: SILENT, [THERE]: ready }], { origins: [HERE, THERE] });
    await expect(one.end).resolves.toMatchObject({ kind: "back", origin: THERE });

    const two = run([{ [HERE]: SILENT, [THERE]: failed() }], { origins: [HERE, THERE], maxRounds: 1 });
    await two.end;
    expect(two.views[0]).toMatchObject({ kind: "failed", origin: THERE });
  });

  it("blames the certificate only once silence outlasts a normal restart", async () => {
    const { end, asked } = run([{ [HERE]: SILENT }], { expectsNewCert: true });
    await expect(end).resolves.toEqual({ kind: "cert-error" });
    expect(asked.length).toBe(Math.max(CERT_ERROR_MIN_ATTEMPTS + 1, CERT_ERROR_THRESHOLD));
  });

  it("stops asking once cancelled", async () => {
    const { end, asked } = run([{ [HERE]: stopping }], { maxRounds: 2 });
    await expect(end).resolves.toEqual({ kind: "cancelled" });
    expect(asked).toHaveLength(2);
  });
});
