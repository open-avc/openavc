import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

/**
 * A reading nobody is hearing any more says so.
 *
 * Seen on the bench: a mixer paused mid-session drew LEVEL 511 and LEVEL 211
 * in this table under red rings, and an unmarked 511 in a LEVEL column reads
 * as current. The values stay -- this page is where somebody diagnoses, and
 * "it was 511 when we lost it" is often what they came for -- but they are
 * marked as the last ones heard.
 *
 * The line this has to hold: the platform's own keys about a child (online,
 * the fault pair, the label) are true RIGHT NOW, and are how we know it is
 * not answering. Marking those "last heard" would be nonsense.
 */

const mocks = vi.hoisted(() => ({
  listChildEntities: vi.fn(),
  refreshChildEntities: vi.fn(async () => ({
    status: "refreshed", device_id: "amp", result: {},
  })),
  patchChildEntity: vi.fn(),
}));

vi.mock("../../api/restClient", () => ({
  listChildEntities: mocks.listChildEntities,
  refreshChildEntities: mocks.refreshChildEntities,
  patchChildEntity: mocks.patchChildEntity,
}));

const liveState: Record<string, unknown> = {};

vi.mock("../../store/connectionStore", () => ({
  useConnectionStore: (selector: (s: unknown) => unknown) =>
    selector({ liveState }),
}));

/** jsdom has no layout engine, so the real virtualizer renders no rows. */
vi.mock("@tanstack/react-virtual", () => ({
  useVirtualizer: (opts: {
    count: number;
    getItemKey: (i: number) => string | number;
  }) => ({
    getTotalSize: () => opts.count * 36,
    getVirtualItems: () =>
      Array.from({ length: opts.count }, (_, index) => ({
        index,
        key: opts.getItemKey(index),
        start: index * 36,
        size: 36,
      })),
    measureElement: () => undefined,
  }),
}));

import { ChildEntities } from "./ChildEntities";

/** One channel, in whatever state the test needs it in. */
function payload(state: Record<string, unknown>) {
  return {
    device_id: "amp",
    child_entity_types: {
      channel: {
        label: "Channel",
        label_plural: "Channels",
        id_format: { type: "integer", min: 1, max: 8, pad_width: 2 },
        state_variables: {
          level: { type: "number", label: "Output Level (dB)", unit: "dB" },
          online: { type: "boolean", label: "Online" },
          label: { type: "string", label: "Label" },
          offline_reason: { type: "string", label: "Fault" },
          offline_detail: { type: "string", label: "Fault Detail" },
        },
        summary_fields: ["level"],
      },
    },
    children: {
      channel: [
        {
          local_id: 1,
          local_id_padded: "01",
          label: "House Left",
          display_name: "House Left",
          config: {},
          registered: true,
          state,
        },
      ],
    },
  };
}

const IN_SERVICE = { level: -6.5, online: true, offline_reason: "", offline_detail: "" };
const PARENT_GONE = {
  level: -6.5,
  online: false,
  offline_reason: "parent_offline",
  offline_detail: "The device this belongs to is not answering.",
};

function panel(props: Record<string, unknown> = {}) {
  return (
    <ChildEntities
      deviceId="amp"
      search=""
      connected={true}
      childKeyCount={2}
      config={{}}
      driverInfo={{}}
      monitors={[]}
      onMonitorsChange={() => {}}
      {...props}
    />
  );
}

beforeEach(() => {
  mocks.listChildEntities.mockReset();
});

describe("a child that has stopped answering", () => {
  it("marks its readings as the last ones heard, and keeps the values", async () => {
    mocks.listChildEntities.mockResolvedValue(payload(PARENT_GONE));
    render(panel());

    await userEvent.click(await screen.findByTestId("child-expand-01"));
    const row = screen.getByTestId("child-row-01");

    // The value is still there. Blanking it is the obvious fix and the wrong
    // one: this page is where the last reading before the loss is read.
    expect(within(row).getAllByText("-6.5").length).toBeGreaterThan(0);
    expect(within(row).getAllByTestId("last-heard").length).toBe(1);

    const level = within(row).getByRole("row", { name: /^level/ });
    expect(within(level).getByTestId("last-heard")).toBeTruthy();
  });

  it("leaves the platform's own keys alone -- they are true right now", async () => {
    mocks.listChildEntities.mockResolvedValue(payload(PARENT_GONE));
    render(panel());

    await userEvent.click(await screen.findByTestId("child-expand-01"));
    const row = screen.getByTestId("child-row-01");

    for (const prop of ["online", "label", "offline_reason", "offline_detail"]) {
      const tr = within(row).getByRole("row", { name: new RegExp(`^${prop}`) });
      expect(within(tr).queryByTestId("last-heard")).toBeNull();
    }
  });

  it("dims the summary column, where there is no room for the words", async () => {
    mocks.listChildEntities.mockResolvedValue(payload(PARENT_GONE));
    render(panel());

    await screen.findByTestId("child-row-01");
    const cell = document.querySelector('[data-stale="true"]');
    expect(cell?.textContent).toBe("-6.5");
  });

  it("marks the rows the filter turns up too", async () => {
    mocks.listChildEntities.mockResolvedValue(payload(PARENT_GONE));
    render(panel({ search: "level" }));

    const results = await screen.findByTestId("child-search-results");
    expect(within(results).getByTestId("last-heard")).toBeTruthy();
  });
});

describe("a child that is answering", () => {
  it("marks nothing", async () => {
    mocks.listChildEntities.mockResolvedValue(payload(IN_SERVICE));
    render(panel());

    await userEvent.click(await screen.findByTestId("child-expand-01"));
    expect(screen.queryAllByTestId("last-heard")).toHaveLength(0);
    expect(document.querySelector('[data-stale="true"]')).toBeNull();
  });
});

describe("an empty slot", () => {
  it("has heard nothing, so there is nothing to call last heard", async () => {
    // `not_fitted` is not in service either, but a position with no hardware
    // in it never reported a reading -- its values are absent, not stale.
    mocks.listChildEntities.mockResolvedValue(
      payload({
        level: null, online: false,
        offline_reason: "not_fitted", offline_detail: "",
      }),
    );
    render(panel());

    await userEvent.click(await screen.findByTestId("child-expand-01"));
    expect(screen.queryAllByTestId("last-heard")).toHaveLength(0);
    expect(document.querySelector('[data-stale="true"]')).toBeNull();
  });
});
