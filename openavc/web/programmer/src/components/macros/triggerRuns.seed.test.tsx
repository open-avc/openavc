import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";

/**
 * The trigger card's last-run record as the server keeps it, read back on
 * every connect: a fire its macro refused overnight is still said in the
 * morning, with the macro's reason and when it happened. A refused fire does
 * not move `last_fired` (that is when the trigger last started its macro), so
 * the record's own time is the one the card dates it by.
 */

const mocks = vi.hoisted(() => ({ listTriggers: vi.fn() }));

vi.mock("../../api/restClient", () => ({ listTriggers: mocks.listTriggers }));

import { useTriggerRuns } from "./triggerRuns";
import { useConnectionStore } from "../../store/connectionStore";
import { useLogStore } from "../../store/logStore";

const BASE = {
  type: "schedule", enabled: true, macro_id: "m", macro_name: "M",
  last_error: null, has_pending_delay: false, has_pending_debounce: false,
};

beforeEach(() => {
  mocks.listTriggers.mockReset();
  useLogStore.setState({ triggerRuns: {} });
  useConnectionStore.setState({ connected: true });
});

describe("useTriggerRuns", () => {
  it("keeps a refused fire's reason and dates it by the record", async () => {
    const reason = {
      code: "macro_cooldown",
      message: "The macro's Cooldown (60 seconds) had not passed since it last started.",
    };
    mocks.listTriggers.mockResolvedValue({
      triggers: [{
        ...BASE, id: "trg", last_fired: 1000, last_outcome: "skipped",
        last_reason: reason, last_run_at: 2000,
      }],
    });

    renderHook(() => useTriggerRuns());

    await waitFor(() => expect(useLogStore.getState().triggerRuns.trg).toBeTruthy());
    expect(useLogStore.getState().triggerRuns.trg).toEqual({
      outcome: "skipped", error: undefined, reason, endedAt: 2000,
    });
  });

  it("dates a record from an older server by when it last fired", async () => {
    mocks.listTriggers.mockResolvedValue({
      triggers: [{ ...BASE, id: "trg", last_fired: 1000, last_outcome: "completed" }],
    });

    renderHook(() => useTriggerRuns());

    await waitFor(() => expect(useLogStore.getState().triggerRuns.trg).toBeTruthy());
    expect(useLogStore.getState().triggerRuns.trg).toEqual({
      outcome: "completed", error: undefined, reason: undefined, endedAt: 1000,
    });
  });
});
