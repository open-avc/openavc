import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

/**
 * The Child Entities panel, on a device whose roster comes from its settings.
 *
 * Both of these were seen on a real Extron frame: the panel told a connected
 * device to connect, and it went on saying "Outputs 0" after the setting that
 * fills it had been saved and the children had registered -- while the Live
 * State panel on the same screen reported 56 child keys living up here.
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

import { ChildEntities } from "./ChildEntities";

/** A declarative driver covering a family of frames: this one's size is a
 *  number somebody types into the device, not a fact about the driver. */
const DRIVER_INFO = {
  config_schema: {
    output_count: { type: "integer", default: 0, label: "Output Count" },
  },
};

function payload(outputs: number[]) {
  return {
    device_id: "mx",
    child_entity_types: {
      output: {
        label: "Output",
        label_plural: "Outputs",
        id_format: { type: "integer", min: 1, max: 128 },
        instances: { count_from: "output_count", label: "Output {id}" },
        state_variables: {},
        summary_fields: [],
      },
    },
    children: {
      output: outputs.map((id) => ({
        local_id: id,
        local_id_padded: String(id),
        label: `Output ${id}`,
        config: {},
        registered: true,
        state: {},
      })),
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
      driverInfo={DRIVER_INFO}
      {...props}
    />
  );
}

beforeEach(() => {
  mocks.listChildEntities.mockReset();
  mocks.refreshChildEntities.mockClear();
});

describe("the empty state", () => {
  it("names the setting that fills the roster, not the cable", async () => {
    mocks.listChildEntities.mockResolvedValue(payload([]));
    render(panel());

    const empty = await screen.findByTestId("child-empty");
    // The label the settings form uses, not the YAML field name.
    expect(empty.textContent).toContain("Output Count");
    expect(empty.textContent).not.toContain("Connect the device");
  });

  it("still says connect it when the device is down and the setting is made", async () => {
    /** Which also fixes the order: an empty count field beats a dead link,
     *  because connecting a frame nobody has sized still registers nothing. */
    mocks.listChildEntities.mockResolvedValue(payload([]));
    render(panel({ connected: false, config: { output_count: 4 } }));

    const empty = await screen.findByTestId("child-empty");
    expect(empty.textContent).toContain("Connect the device");
  });

  it("asks the device again once the setting is filled in", async () => {
    mocks.listChildEntities.mockResolvedValue(payload([]));
    render(panel({ config: { output_count: 4 } }));

    const empty = await screen.findByTestId("child-empty");
    expect(empty.textContent).toContain("has not reported any");
    expect(empty.textContent).not.toContain("Output Count");
  });
});

describe("keeping up with the roster", () => {
  it("re-fetches when child keys appear, without anybody pressing anything", async () => {
    mocks.listChildEntities.mockResolvedValue(payload([]));
    const { rerender } = render(panel());
    await screen.findByTestId("child-empty");
    expect(mocks.listChildEntities).toHaveBeenCalledTimes(1);

    // The device registered four outputs, so its state keys arrived.
    mocks.listChildEntities.mockResolvedValue(payload([1, 2, 3, 4]));
    rerender(panel({ childKeyCount: 8, config: { output_count: 4 } }));

    await waitFor(
      () => expect(mocks.listChildEntities).toHaveBeenCalledTimes(2),
      { timeout: 3000 },
    );
    await waitFor(() => expect(screen.queryByTestId("child-empty")).toBeNull());
  });

  it("re-fetches when the device gets its driver back", async () => {
    /** The types this panel is built from come from a LIVE driver, so a
     *  device disabled while the page was open answered with none — and the
     *  whole section stayed gone after it was switched back on. */
    mocks.listChildEntities.mockResolvedValue({
      device_id: "mx", child_entity_types: {}, children: {},
    });
    const { rerender } = render(panel({ connected: false }));
    await waitFor(() => expect(mocks.listChildEntities).toHaveBeenCalledTimes(1));
    expect(screen.queryByText("Child Entities")).toBeNull();

    mocks.listChildEntities.mockResolvedValue(payload([]));
    rerender(panel({ connected: true }));
    expect(await screen.findByTestId("child-empty")).toBeTruthy();
  });

  it("says what a refresh found, so it is not pressed again", async () => {
    mocks.listChildEntities.mockResolvedValue(payload([]));
    render(panel());
    await screen.findByTestId("child-empty");

    await userEvent.click(screen.getByTestId("child-driver-refresh"));
    const outcome = await screen.findByTestId("child-refresh-outcome");
    expect(outcome.textContent).toContain("The device reported no children");

    mocks.listChildEntities.mockResolvedValue(payload([1, 2, 3, 4]));
    await userEvent.click(screen.getByTestId("child-driver-refresh"));
    await waitFor(() =>
      expect(screen.getByTestId("child-refresh-outcome").textContent)
        .toContain("Refreshed: 4 outputs."),
    );
  });
});
