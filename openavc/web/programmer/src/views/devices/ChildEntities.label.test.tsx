import { afterEach, describe, it, expect, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

/**
 * A child's name is shown once, in the row's Label column.
 *
 * A driver can give each child a placeholder name ("Input 3") until the device
 * reports its own ("CH 3", a mixer's factory channel name). The Label column
 * shows the best one: the project's label, else the name the device reports,
 * else the placeholder. The expanded row used to list the reserved `label`
 * key among the readings as well, which still held the placeholder, so the
 * same channel read "CH 3" in one place and "Label: Input 3" in the other.
 */

const mocks = vi.hoisted(() => ({
  listChildEntities: vi.fn(),
  refreshChildEntities: vi.fn(async () => ({
    status: "refreshed", device_id: "mixer", result: {},
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

const PAYLOAD = {
  device_id: "mixer",
  child_entity_types: {
    input: {
      label: "Input",
      label_plural: "Inputs",
      id_format: { type: "integer", min: 1, max: 8 },
      state_variables: {
        name: { type: "string", label: "Channel Name" },
        mute: { type: "boolean", label: "Mute" },
        online: { type: "boolean", label: "Online" },
        label: { type: "string", label: "Label" },
        offline_reason: { type: "string", label: "Fault" },
        offline_detail: { type: "string", label: "Fault Detail" },
      },
      summary_fields: ["mute"],
      label_field: "name",
    },
  },
  children: {
    input: [
      {
        local_id: 3,
        local_id_padded: "3",
        label: "",
        display_name: "CH 3",
        config: {},
        registered: true,
        state: {
          name: "CH 3", mute: false, online: true, label: "Input 3",
          offline_reason: "", offline_detail: "",
        },
      },
    ],
  },
};

describe("a child's name", () => {
  it("is shown once, as the Label column has it", async () => {
    mocks.listChildEntities.mockResolvedValue(PAYLOAD);
    render(
      <ChildEntities
        deviceId="mixer"
        search=""
        connected={true}
        childKeyCount={2}
        config={{}}
        driverInfo={{}}
        monitors={[]}
        onMonitorsChange={() => {}}
      />,
    );

    await userEvent.click(await screen.findByTestId("child-expand-3"));
    const row = screen.getByTestId("child-row-3");

    expect(within(row).getAllByText("CH 3").length).toBeGreaterThan(0);
    expect(within(row).queryByText("Input 3")).toBeNull();
    expect(row.querySelector('td[title="label"]')).toBeNull();
    // The other platform keys stay: a monitor can be put on them here.
    expect(row.querySelector('td[title="online"]')).not.toBeNull();
    expect(row.querySelector('td[title="offline_reason"]')).not.toBeNull();
  });
});


/**
 * The list is fetched when the device connects, and a device usually reports
 * its channel names a moment after that. The names arrive in live state, so
 * the Label column reads them there. It used to keep the name from the fetch
 * and showed "(no label)" beside a Channel Name column that had the names.
 */
const NOT_YET_NAMED = {
  device_id: "mixer",
  child_entity_types: PAYLOAD.child_entity_types,
  children: {
    input: [
      {
        ...PAYLOAD.children.input[0],
        display_name: "",
        state: { ...PAYLOAD.children.input[0].state, name: null, label: "" },
      },
    ],
  },
};

function renderPanel(search = "") {
  return render(
    <ChildEntities
      deviceId="mixer"
      search={search}
      connected={true}
      childKeyCount={2}
      config={{}}
      driverInfo={{}}
      monitors={[]}
      onMonitorsChange={() => {}}
    />,
  );
}

describe("a name the device reports after the list was fetched", () => {
  afterEach(() => {
    for (const key of Object.keys(liveState)) delete liveState[key];
  });

  it("is shown in the Label column", async () => {
    mocks.listChildEntities.mockResolvedValue(NOT_YET_NAMED);
    liveState["device.mixer.input.3.name"] = "CH 3";
    renderPanel();

    expect(await screen.findByTestId("child-label-3")).toHaveTextContent("CH 3");
  });

  it("is shown on the filter's result", async () => {
    mocks.listChildEntities.mockResolvedValue(NOT_YET_NAMED);
    liveState["device.mixer.input.3.name"] = "CH 3";
    renderPanel("ch 3");

    const results = await screen.findByTestId("child-search-results");
    expect(within(results).queryByText("(no label)")).toBeNull();
    expect(within(results).getAllByText("CH 3").length).toBeGreaterThan(0);
  });

  it("gives way to the driver's own name for the slot while there is none", async () => {
    mocks.listChildEntities.mockResolvedValue(NOT_YET_NAMED);
    liveState["device.mixer.input.3.label"] = "Input 3";
    renderPanel();

    expect(await screen.findByTestId("child-label-3")).toHaveTextContent("Input 3");
  });
});


/**
 * A label typed on a row is the name from then on, wherever the panel shows
 * the child. The row list used to keep it to itself, so a search, or a tab
 * change, put the old name back on screen although the label was saved.
 */
describe("a label typed on a row", () => {
  afterEach(() => {
    for (const key of Object.keys(liveState)) delete liveState[key];
  });

  it("stays after the filter is used and cleared", async () => {
    mocks.listChildEntities.mockResolvedValue(NOT_YET_NAMED);
    mocks.patchChildEntity.mockResolvedValue({});
    liveState["device.mixer.input.3.name"] = "CH 3";
    const panel = (search: string) => (
      <ChildEntities
        deviceId="mixer"
        search={search}
        connected={true}
        childKeyCount={2}
        config={{}}
        driverInfo={{}}
        monitors={[]}
        onMonitorsChange={() => {}}
      />
    );
    const { rerender } = render(panel(""));

    await userEvent.click(await screen.findByTestId("child-label-3"));
    const input = screen.getByTestId("child-label-input-3");
    await userEvent.type(input, "Lobby{Enter}");
    expect(await screen.findByTestId("child-label-3")).toHaveTextContent("Lobby");

    rerender(panel("ch 3"));
    const results = await screen.findByTestId("child-search-results");
    expect(within(results).getByText("Lobby")).toBeInTheDocument();

    rerender(panel(""));
    expect(await screen.findByTestId("child-label-3")).toHaveTextContent("Lobby");
  });
});
