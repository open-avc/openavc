import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { MonitorConfig } from "../../api/types";

/**
 * Watching a per-channel reading, from the table where per-channel readings
 * are read.
 *
 * The Live State list on the device page sends every child key up to this
 * panel — and this panel had no Monitor control at all, so on multi-channel
 * gear the readings most worth watching were the only ones that could not be
 * tagged from the page they live on. The second half of the same fault is the
 * form: a child reading arrived undeclared, so a level with a declared -80..0
 * dB range was offered "tick the values that mean everything is fine", which
 * cannot express a range at all.
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

const liveState: Record<string, unknown> = {
  "device.amp.channel.01.fader": -6.5,
  "device.amp.channel.01.mute": false,
};

vi.mock("../../store/connectionStore", () => ({
  useConnectionStore: (selector: (s: unknown) => unknown) =>
    selector({ liveState }),
}));

/** jsdom has no layout engine, so the real virtualizer measures its scroll box
 *  at zero and renders no rows. Same stand-in as the presence suite, and for
 *  the same reason: these tests are about what is drawn on a row. */
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

const PAYLOAD = {
  device_id: "amp",
  child_entity_types: {
    channel: {
      label: "Channel",
      label_plural: "Channels",
      id_format: { type: "integer", min: 1, max: 8, pad_width: 2 },
      state_variables: {
        fader: {
          type: "number",
          label: "Output Level (dB)",
          min: -80,
          max: 0,
          step: 0.5,
          unit: "dB",
        },
        mute: { type: "boolean", label: "Mute" },
      },
      summary_fields: ["fader"],
    },
  },
  children: {
    channel: [
      {
        local_id: 1,
        local_id_padded: "01",
        label: "",
        display_name: "House Left",
        config: {},
        registered: true,
        state: {},
      },
    ],
  },
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
  mocks.listChildEntities.mockResolvedValue(PAYLOAD);
});

async function expandFirstChannel() {
  const expander = await screen.findByTestId("child-expand-01");
  await userEvent.click(expander);
}

describe("tagging a per-channel reading", () => {
  it("offers the control on every reading of an expanded child", async () => {
    render(panel());
    await expandFirstChannel();

    const row = screen.getByTestId("child-row-01");
    expect(
      within(row).getAllByTitle(
        "Watch this reading on the Dashboard and in the cloud",
      ),
    ).toHaveLength(2);
  });

  it("writes the child's own key, and names the channel in it", async () => {
    const onMonitorsChange = vi.fn();
    render(panel({ onMonitorsChange }));
    await expandFirstChannel();

    const row = screen.getByTestId("child-row-01");
    await userEvent.click(
      within(row).getAllByTitle(
        "Watch this reading on the Dashboard and in the cloud",
      )[0],
    );

    expect(onMonitorsChange).toHaveBeenCalledTimes(1);
    const [created] = onMonitorsChange.mock.calls[0] as [MonitorConfig[], string];
    expect(created).toEqual([
      {
        key: "device.amp.channel.01.fader",
        // Not "Output Level (dB)": eight channels carrying one name is a
        // Dashboard that cannot be read.
        label: "House Left · Output Level (dB)",
        unit: "dB",
        type: "number",
      },
    ]);
  });

  it("asks for a range, because the reading declares one", async () => {
    const monitors: MonitorConfig[] = [
      { key: "device.amp.channel.01.fader", type: "number", unit: "dB" },
    ];
    render(panel({ monitors }));
    await expandFirstChannel();

    const row = screen.getByTestId("child-row-01");
    await userEvent.click(within(row).getByText("Set what normal looks like"));

    // The numeric form: a bound either side, pre-filled with the range the
    // driver declared. The undeclared form has neither, and offers a list of
    // exact values to tick instead — which cannot say "normal is -20 to 0".
    expect(within(row).getByPlaceholderText("-80")).toBeTruthy();
    expect(within(row).getByText("to")).toBeTruthy();
    expect(
      within(row).getByText(/Leave both blank to show the reading without judging it/),
    ).toBeTruthy();
    expect(
      within(row).queryByText(/Tick the values that mean everything is fine/),
    ).toBeNull();
  });

  it("marks the child row, so a tagged reading can be found without opening it", async () => {
    const monitors: MonitorConfig[] = [
      { key: "device.amp.channel.01.fader" },
      { key: "device.amp.channel.01.mute" },
    ];
    render(panel({ monitors }));

    const mark = await screen.findByTestId("child-monitored-01");
    expect(mark.getAttribute("title")).toContain("2 readings here are watched");
  });

  it("leaves an unwatched child unmarked", async () => {
    render(panel());
    await screen.findByTestId("child-row-01");
    expect(screen.queryByTestId("child-monitored-01")).toBeNull();
  });
});

describe("tagging one found by the filter", () => {
  it("offers the control on the rows the search turned up", async () => {
    const onMonitorsChange = vi.fn();
    render(panel({ search: "fader", onMonitorsChange }));

    const results = await screen.findByTestId("child-search-results");
    await userEvent.click(
      within(results).getByTitle(
        "Watch this reading on the Dashboard and in the cloud",
      ),
    );

    const [created] = onMonitorsChange.mock.calls[0] as [MonitorConfig[], string];
    expect(created[0].key).toBe("device.amp.channel.01.fader");
    expect(created[0].label).toBe("House Left · Output Level (dB)");
  });
});
