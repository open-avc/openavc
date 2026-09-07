import { beforeEach, describe, it, expect, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

/**
 * What a trigger card says about the macro it last fired.
 *
 * A trigger that errored on every fire was indistinguishable from one doing
 * its job: the card flashed the same colour on `trigger.fired`, the flash was
 * gone in 1.5 seconds, and the only stored tell -- `last_fired` -- moves
 * BEFORE the macro runs, so failing looked freshly successful. `Fire now`
 * answered the same for a macro whose every step failed and reported nothing
 * at all to the person who pressed it.
 */

const mocks = vi.hoisted(() => ({
  testTrigger: vi.fn(async () => ({ status: "completed" })),
  showSuccess: vi.fn(),
  showError: vi.fn(),
  showInfo: vi.fn(),
}));

vi.mock("../../api/restClient", () => ({
  testTrigger: mocks.testTrigger,
}));

vi.mock("../../store/toastStore", () => ({
  showSuccess: mocks.showSuccess,
  showError: mocks.showError,
  showInfo: mocks.showInfo,
}));

import { TriggerList } from "./TriggerList";
import { useLogStore } from "../../store/logStore";
import type { TriggerRun } from "../../store/logStore";

const TRIGGER = {
  id: "trg_nightly",
  type: "schedule",
  enabled: true,
  cron: "0 22 * * *",
};

function renderCard(run?: TriggerRun) {
  useLogStore.setState({ triggerRuns: run ? { [TRIGGER.id]: run } : {} });
  return render(
    <TriggerList
      triggers={[TRIGGER as never]}
      devices={[]}
      allMacros={[]}
      onUpdate={() => {}}
    />
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  useLogStore.setState({ triggerRuns: {}, recentlyFired: {}, triggerPending: {} });
});

describe("the card's last-run marker", () => {
  it("says so when the last fire failed", () => {
    renderCard({ outcome: "failed", firedAt: Date.now() / 1000 - 120 });
    expect(screen.getByText("Last run failed")).toBeTruthy();
  });

  it("says so when the macro was not there to fire", () => {
    renderCard({ outcome: "error", error: "Macro 'gone' not found" });
    expect(screen.getByText("Last run failed")).toBeTruthy();
  });

  it("says a clean run is clean rather than saying nothing", () => {
    // "It ran and it worked" is the answer somebody opens this card to get.
    renderCard({ outcome: "completed", firedAt: Date.now() / 1000 - 120 });
    expect(screen.getByText(/Ran OK/)).toBeTruthy();
    expect(screen.queryByText("Last run failed")).toBeNull();
  });

  it("does not call a cancelled run a failure", () => {
    // A cancel_group preemption is ordinary operation; marking it red would
    // cry wolf on exactly the trigger somebody set up to be preempted.
    renderCard({ outcome: "cancelled" });
    expect(screen.getByText("Last run cancelled")).toBeTruthy();
    expect(screen.queryByText("Last run failed")).toBeNull();
  });

  it("claims nothing about a trigger that has never fired", () => {
    renderCard();
    expect(screen.queryByText(/Last run/)).toBeNull();
    expect(screen.queryByText(/Ran OK/)).toBeNull();
  });
});

describe("Fire now", () => {
  it("says the macro failed instead of looking like it worked", async () => {
    mocks.testTrigger.mockResolvedValueOnce({ status: "failed" });
    renderCard();

    await userEvent.click(screen.getByTitle("Fire now (bypasses conditions)"));

    await waitFor(() => expect(mocks.showError).toHaveBeenCalled());
    expect(mocks.showError.mock.calls[0][0]).toContain("the macro failed");
    expect(mocks.showSuccess).not.toHaveBeenCalled();
  });

  it("confirms a clean run", async () => {
    mocks.testTrigger.mockResolvedValueOnce({ status: "completed" });
    renderCard();

    await userEvent.click(screen.getByTitle("Fire now (bypasses conditions)"));

    await waitFor(() => expect(mocks.showSuccess).toHaveBeenCalled());
    expect(mocks.showError).not.toHaveBeenCalled();
  });

  it("does not report a still-running macro as a failure", async () => {
    // The macro door already learned this: a `wait_until` waiting for a
    // projector is the feature, not a fault.
    mocks.testTrigger.mockResolvedValueOnce({ status: "running" });
    renderCard();

    await userEvent.click(screen.getByTitle("Fire now (bypasses conditions)"));

    await waitFor(() => expect(mocks.showInfo).toHaveBeenCalled());
    expect(mocks.showError).not.toHaveBeenCalled();
  });
});
