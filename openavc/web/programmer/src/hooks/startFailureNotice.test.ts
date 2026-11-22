import { describe, it, expect, beforeEach } from "vitest";
import { startFailureNotice } from "./startFailureNotice";
import { useToastStore } from "../store/toastStore";
import type { StartupStatus } from "../api/startupStatus";

function failed(more: Partial<StartupStatus> = {}): StartupStatus {
  return {
    phase: "failed", version: "0.41.0", error: "Loading the project failed: bad JSON",
    held: false, retryIn: 60, problems: [], ...more,
  };
}

const READY: StartupStatus = { phase: "ready", version: "0.41.0", error: null, held: false, retryIn: null, problems: [] };

const toasts = () => useToastStore.getState().toasts;

beforeEach(() => {
  useToastStore.setState({ toasts: [] });
});

describe("startFailureNotice", () => {
  it("says a failed start's reason and what happens next, and stays up", () => {
    const notice = startFailureNotice();
    expect(notice.heard(failed())).toBe(false);
    expect(toasts()).toHaveLength(1);
    expect(toasts()[0]).toMatchObject({
      severity: "error",
      duration: 0,
      message: "OpenAVC could not start. Loading the project failed: bad JSON. It tries again by itself.",
    });
  });

  it("sends a held start to the server's page for Start again", () => {
    const notice = startFailureNotice();
    notice.heard(failed({ held: true, retryIn: null }));
    expect(toasts()[0].message).toBe(
      "OpenAVC could not start. Loading the project failed: bad JSON. "
        + "Fix the problem, then reload this page and press Start again.",
    );
  });

  it("says it once for a run of failed starts, and again for another reason or next step", () => {
    const notice = startFailureNotice();
    notice.heard(failed());
    const first = toasts()[0].id;
    notice.heard(failed({ retryIn: 12 }));
    expect(toasts().map((t) => t.id)).toEqual([first]);
    notice.heard(failed({ held: true, retryIn: null }));
    expect(toasts()).toHaveLength(1);
    expect(toasts()[0].message).toContain("press Start again");
    notice.heard(failed({ error: "The project's scripts could not load." }));
    expect(toasts()).toHaveLength(1);
    expect(toasts()[0].message).toContain("The project's scripts could not load.");
  });

  it("is taken down once a start succeeds, and a later failure says so again", () => {
    const notice = startFailureNotice();
    notice.heard(failed());
    expect(notice.heard(READY)).toBe(true);
    expect(toasts()).toHaveLength(0);
    notice.heard(failed());
    expect(toasts()).toHaveLength(1);
  });

  it("leaves the notice alone when the connection it waited for is gone", () => {
    const notice = startFailureNotice();
    notice.heard(failed());
    expect(notice.heard(null)).toBe(false);
    expect(toasts()).toHaveLength(1);
  });

  it("is not put back once dismissed for the same failure", () => {
    const notice = startFailureNotice();
    notice.heard(failed());
    useToastStore.getState().removeToast(toasts()[0].id);
    notice.heard(failed());
    expect(toasts()).toHaveLength(0);
  });
});

describe("startFailureNotice: what a start went on past", () => {
  const CLOUD = { stage: "cloud_agent", detail: "The connection to OpenAVC Cloud did not start. Check the server log for the reason." };
  const UPDATES = { stage: "update_checks", detail: "The update checks did not start" };
  const withProblems = (...problems: StartupStatus["problems"]): StartupStatus => ({ ...READY, problems });
  const STARTING: StartupStatus = { ...READY, phase: "starting" };

  it("says each part that did not start, and stays up", () => {
    const notice = startFailureNotice();
    expect(notice.heard(withProblems(CLOUD, UPDATES))).toBe(true);
    expect(toasts()).toHaveLength(1);
    expect(toasts()[0]).toMatchObject({
      severity: "warning",
      duration: 0,
      message: "Some parts of OpenAVC did not start. The connection to OpenAVC Cloud did not start. "
        + "Check the server log for the reason. The update checks did not start.",
    });
  });

  it("says nothing for a start that went on past nothing", () => {
    startFailureNotice().heard(READY);
    expect(toasts()).toHaveLength(0);
  });

  it("says it once per start, so a dismissed one stays gone after a network drop", () => {
    const notice = startFailureNotice();
    notice.heard(withProblems(CLOUD));
    notice.heard(withProblems(CLOUD));
    expect(toasts()).toHaveLength(1);
    useToastStore.getState().removeToast(toasts()[0].id);
    notice.heard(withProblems(CLOUD));
    expect(toasts()).toHaveLength(0);
  });

  it("says it again for a new start, and takes it down when a start misses nothing", () => {
    const notice = startFailureNotice();
    notice.heard(withProblems(CLOUD));
    useToastStore.getState().removeToast(toasts()[0].id);
    notice.heard(STARTING);
    notice.heard(withProblems(CLOUD));
    expect(toasts()).toHaveLength(1);
    notice.heard(STARTING);
    notice.heard(READY);
    expect(toasts()).toHaveLength(0);
  });

  it("replaces the failed start's notice once a start gets going without some parts", () => {
    const notice = startFailureNotice();
    notice.heard(failed());
    notice.heard(withProblems(UPDATES));
    expect(toasts()).toHaveLength(1);
    expect(toasts()[0].severity).toBe("warning");
  });
});
