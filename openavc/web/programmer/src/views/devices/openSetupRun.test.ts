import { beforeEach, describe, expect, it } from "vitest";
import { useNavigationStore } from "../../store/navigationStore";
import {
  forgetOpenSetupRun,
  rememberOpenSetupRun,
  reopenSetupRun,
  takeReopenedRun,
} from "./openSetupRun";

const run = { deviceId: "dev1", actionId: "provision", runId: "r1" };

beforeEach(() => {
  sessionStorage.clear();
  takeReopenedRun("dev1");
});

describe("openSetupRun", () => {
  it("reopens a remembered run's device once, and hands its wizard the run", () => {
    rememberOpenSetupRun(run);
    reopenSetupRun();

    expect(useNavigationStore.getState().activeView).toBe("devices");
    expect(useNavigationStore.getState().consumeFocus()).toEqual({ type: "device", id: "dev1" });
    expect(takeReopenedRun("other")).toBeNull();
    expect(takeReopenedRun("dev1")).toEqual(run);
    expect(takeReopenedRun("dev1")).toBeNull();
    expect(sessionStorage.length).toBe(0);
  });

  it("forgets only its own run, and reopens nothing then", () => {
    rememberOpenSetupRun(run);
    forgetOpenSetupRun("r2");
    expect(sessionStorage.length).toBe(1);
    forgetOpenSetupRun("r1");
    expect(sessionStorage.length).toBe(0);
    reopenSetupRun();
    expect(takeReopenedRun("dev1")).toBeNull();
  });

  it("reopens nothing for a stored value it cannot read", () => {
    sessionStorage.setItem("openavc.setup.open_run", "{not json");
    reopenSetupRun();
    expect(takeReopenedRun("dev1")).toBeNull();
    sessionStorage.setItem("openavc.setup.open_run", JSON.stringify({ deviceId: "dev1" }));
    reopenSetupRun();
    expect(takeReopenedRun("dev1")).toBeNull();
  });
});
