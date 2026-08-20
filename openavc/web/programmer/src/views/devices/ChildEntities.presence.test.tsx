import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

/**
 * What the Child Entities panel shows about a sub-unit that is not answering.
 *
 * It used to show the literal word "false" in a monospace column, only when
 * the driver happened to name `online` in summary_fields, with no dot, no
 * order and no filter. On an eight-decoder frame that is the answer sitting
 * on screen and needing to be hunted for, which is exactly what happened on
 * the bench: an afternoon spent on a power fault that did not exist.
 */

const mocks = vi.hoisted(() => ({
  listChildEntities: vi.fn(),
  refreshChildEntities: vi.fn(async () => ({
    status: "refreshed", device_id: "mx", result: {},
  })),
  patchChildEntity: vi.fn(),
}));

vi.mock("../../api/restClient", () => ({
  listChildEntities: mocks.listChildEntities,
  refreshChildEntities: mocks.refreshChildEntities,
  patchChildEntity: mocks.patchChildEntity,
}));

vi.mock("../../store/connectionStore", () => ({
  useConnectionStore: (selector: (s: unknown) => unknown) =>
    selector({ liveState: {} }),
}));

/**
 * jsdom has no layout engine, so the real virtualizer measures its scroll
 * box at zero and renders no rows at all -- which is why the panel's other
 * suite only ever asserts on the empty state. This stand-in renders every
 * row, which is what these tests are about: WHICH rows are shown, in WHAT
 * order, and what is drawn on each. It deliberately reads the same `count`
 * and `getItemKey` the component passes, so a component that stopped feeding
 * the virtualizer its filtered list still fails here.
 */
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

/** `down` names the endpoints that are not answering. */
function payload(ids: string[], down: Record<string, string | true> = {}) {
  return {
    device_id: "mx",
    child_entity_types: {
      decoder: {
        label: "Decoder",
        label_plural: "Decoders",
        id_format: { type: "string" },
        state_variables: {
          online: { type: "boolean" },
          offline_reason: { type: "string" },
          offline_detail: { type: "string" },
        },
        summary_fields: [],
      },
    },
    children: {
      decoder: ids.map((id) => {
        const bad = down[id];
        return {
          local_id: id,
          local_id_padded: id,
          label: `Decoder ${id}`,
          config: {},
          registered: true,
          state: bad
            ? {
                online: false,
                offline_reason: bad === true ? "" : bad,
                offline_detail: bad === true ? "" : `Detail for ${bad}`,
              }
            : { online: true, offline_reason: "", offline_detail: "" },
        };
      }),
    },
  };
}

function panel(props: Record<string, unknown> = {}) {
  return (
    <ChildEntities
      deviceId="mx"
      search=""
      connected={true}
      childKeyCount={0}
      config={{}}
      driverInfo={{}}
      {...props}
    />
  );
}

beforeEach(() => {
  mocks.listChildEntities.mockReset();
  mocks.refreshChildEntities.mockClear();
});

describe("the presence dot", () => {
  it("marks every row, so presence never depends on a summary column", async () => {
    // summary_fields is empty here on purpose: the old rendering showed
    // `online` only when the driver listed it, so the drivers that most
    // needed it were free to hide it.
    mocks.listChildEntities.mockResolvedValue(payload(["a", "b"], { b: true }));
    render(panel());

    await screen.findByTestId("child-row-a");
    const dots = screen.getAllByTestId("child-presence-dot");
    expect(dots).toHaveLength(2);
    expect(dots.map((d) => d.getAttribute("data-ok"))).toContain("false");
  });

  it("puts the driver's own sentence on the bad row", async () => {
    mocks.listChildEntities.mockResolvedValue(
      payload(["a"], { a: "service_fault" }),
    );
    render(panel());

    const row = await screen.findByTestId("child-row-a");
    const dot = within(row).getByTestId("child-presence-dot");
    expect(dot.getAttribute("title")).toBe("Detail for service_fault");
    expect(dot.getAttribute("data-reason")).toBe("service_fault");
  });

  it("still marks a row whose driver gave no reason at all", async () => {
    // Every driver written before the fault vocabulary. The dot is the point.
    mocks.listChildEntities.mockResolvedValue(payload(["a"], { a: true }));
    render(panel());

    const row = await screen.findByTestId("child-row-a");
    const dot = within(row).getByTestId("child-presence-dot");
    expect(dot.getAttribute("data-ok")).toBe("false");
    expect(dot.getAttribute("title")).toBe("Not answering");
  });
});

describe("ordering", () => {
  it("lifts what is not answering to the top", async () => {
    mocks.listChildEntities.mockResolvedValue(
      payload(["a", "b", "c", "d"], { c: true }),
    );
    render(panel());

    await screen.findByTestId("child-row-a");
    const rows = screen.getAllByTestId(/^child-row-/);
    expect(rows[0].getAttribute("data-testid")).toBe("child-row-c");
  });

  it("leaves a healthy roster in its own order", async () => {
    // Reordering rows under somebody for no reason would be worse than not
    // ordering at all, so a frame with nothing wrong is left alone.
    mocks.listChildEntities.mockResolvedValue(payload(["a", "b", "c"]));
    render(panel());

    await screen.findByTestId("child-row-a");
    const rows = screen.getAllByTestId(/^child-row-/);
    expect(rows.map((r) => r.getAttribute("data-testid"))).toEqual([
      "child-row-a", "child-row-b", "child-row-c",
    ]);
  });
});

describe("the trouble filter", () => {
  it("is not offered when there is nothing to filter to", async () => {
    mocks.listChildEntities.mockResolvedValue(payload(["a", "b"]));
    render(panel());

    await screen.findByTestId("child-row-a");
    expect(screen.queryByTestId("child-trouble-filter")).toBeNull();
  });

  it("narrows to the endpoints that are down", async () => {
    mocks.listChildEntities.mockResolvedValue(
      payload(["a", "b", "c"], { b: true, c: true }),
    );
    render(panel());

    const filter = await screen.findByTestId("child-trouble-filter");
    expect(filter.textContent).toContain("2 that are not answering");

    await userEvent.click(within(filter).getByRole("checkbox"));
    await waitFor(() => {
      expect(screen.queryByTestId("child-row-a")).toBeNull();
    });
    expect(screen.getByTestId("child-row-b")).toBeTruthy();
    expect(screen.getByTestId("child-row-c")).toBeTruthy();
  });

  it("reads as one endpoint when only one is down", async () => {
    mocks.listChildEntities.mockResolvedValue(payload(["a", "b"], { b: true }));
    render(panel());

    const filter = await screen.findByTestId("child-trouble-filter");
    expect(filter.textContent).toContain("the one that is not answering");
  });
});

describe("the type tab", () => {
  it("says how many are down, not just how many there are", async () => {
    mocks.listChildEntities.mockResolvedValue(
      payload(["a", "b", "c"], { c: true }),
    );
    render(panel());

    const badge = await screen.findByTestId("child-type-down-decoder");
    expect(badge.textContent).toContain("1 down");
  });

  it("says nothing extra when the whole type is fine", async () => {
    mocks.listChildEntities.mockResolvedValue(payload(["a", "b"]));
    render(panel());

    await screen.findByTestId("child-row-a");
    expect(screen.queryByTestId("child-type-down-decoder")).toBeNull();
  });
});

describe("the column fallback", () => {
  /** A type declaring nothing of its own -- what the docs tell dynamic-type
   *  authors to write. The schema the panel reads is the EFFECTIVE one, so
   *  the platform's reserved keys are in it, and the fallback used to pick
   *  them: `online` and `label` when there were two, and `offline_reason`
   *  once there were four. All three are drawn by the row itself now. */
  function bareTypePayload() {
    const p = payload(["a", "b"]);
    p.child_entity_types.decoder.state_variables = {
      online: { type: "boolean" },
      label: { type: "string" },
      offline_reason: { type: "string" },
      offline_detail: { type: "string" },
    } as never;
    p.child_entity_types.decoder.summary_fields = undefined as never;
    return p;
  }

  it("draws no columns of its own rather than repeating the row", async () => {
    mocks.listChildEntities.mockResolvedValue(bareTypePayload());
    render(panel());

    await screen.findByTestId("child-row-a");
    // Not `offline_reason`, and not `online` beside the dot that says it.
    expect(screen.queryByText("offline_reason")).toBeNull();
    expect(screen.queryByText("online")).toBeNull();
    // The row still carries everything that matters.
    expect(screen.getAllByTestId("child-presence-dot")).toHaveLength(2);
  });

  it("still falls back to the type's own fields when it declares some", async () => {
    const p = bareTypePayload();
    p.child_entity_types.decoder.state_variables = {
      resolution: { type: "string" },
      online: { type: "boolean" },
      label: { type: "string" },
      offline_reason: { type: "string" },
      offline_detail: { type: "string" },
    } as never;
    mocks.listChildEntities.mockResolvedValue(p);
    render(panel());

    await screen.findByTestId("child-row-a");
    expect(screen.getByText("resolution")).toBeTruthy();
    expect(screen.queryByText("offline_reason")).toBeNull();
  });

  it("honours an explicit summary_fields even when it names a reserved key", async () => {
    // The author picked it. Two shipped drivers list `online` first, and
    // silently dropping a column somebody asked for is worse than one that
    // repeats the dot.
    const p = payload(["a"]);
    p.child_entity_types.decoder.summary_fields = ["online"] as never;
    mocks.listChildEntities.mockResolvedValue(p);
    render(panel());

    await screen.findByTestId("child-row-a");
    expect(screen.getByText("online")).toBeTruthy();
  });
});
