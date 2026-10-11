import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";

// A network driver saved by an earlier Driver Builder has no IP Address or
// Port field: the Builder keeps those out of its Configuration Fields list and
// did not write them. The Add Device form takes a device's address only from
// the driver's own fields, so a device added with one had nowhere to enter it,
// and its author had no way to add them. Opening such a driver now adds them,
// says so, and lets the author save; looking at it and moving on discards
// nothing and asks nothing.

const api = vi.hoisted(() => ({
  updateDriverDefinition: vi.fn(),
  listDriverDefinitions: vi.fn(),
  validateDriverDefinition: vi.fn(),
}));
vi.mock("../../api/restClient", () => api);

import { DriverEditor } from "./DriverEditor";
import { addMissingConnectionFields } from "./transportPickerHelpers";
import { useDriverBuilderStore } from "../../store/driverBuilderStore";
import type { DriverDefinition } from "../../api/types";

const NETWORK = {
  host: { type: "string", required: true, label: "IP Address" },
  port: { type: "integer", required: true, label: "Port" },
};

function definition(over: Record<string, unknown>): DriverDefinition {
  return {
    id: "acme_widget",
    name: "Acme Widget",
    manufacturer: "Acme",
    category: "utility",
    version: "1.0.0",
    transport: "tcp",
    delimiter: "\r",
    default_config: { port: 4000 },
    config_schema: {},
    state_variables: {},
    responses: [],
    commands: {},
    polling: {},
    source: "user",
    ...over,
  } as unknown as DriverDefinition;
}

function open(def: DriverDefinition) {
  useDriverBuilderStore.setState({
    definitions: [def],
    selectedId: null,
    dirty: false,
    addedConnectionFields: [],
  });
  useDriverBuilderStore.getState().selectDriver(def.id);
  return useDriverBuilderStore.getState();
}

beforeEach(() => {
  vi.clearAllMocks();
  api.listDriverDefinitions.mockResolvedValue([]);
  api.updateDriverDefinition.mockResolvedValue({});
  api.validateDriverDefinition.mockResolvedValue({ valid: true, errors: [], warnings: [] });
});

describe("the connection fields a network driver is missing", () => {
  it("are added first, keeping every field it has", () => {
    const schema = { display_id: { type: "integer", label: "Display ID" } };
    const { schema: next, added } = addMissingConnectionFields(schema, "tcp");
    expect(Object.keys(next)).toEqual(["host", "port", "display_id"]);
    expect(next.host).toEqual(NETWORK.host);
    expect(added).toEqual(["host", "port"]);
  });

  it("are only the ones it lacks", () => {
    const port = { type: "integer", label: "Control Port", default: 23 };
    const { schema, added } = addMissingConnectionFields({ port }, "tcp");
    expect(added).toEqual(["host"]);
    expect(schema.port).toEqual(port);
  });

  it("are none for a driver that has them, a serial driver or a bridge driver", () => {
    expect(addMissingConnectionFields({ ...NETWORK }, "udp").added).toEqual([]);
    // Add Device's serial picker has its own port and baud fields.
    expect(addMissingConnectionFields({}, "serial").added).toEqual([]);
    expect(addMissingConnectionFields({}, "bridge").added).toEqual([]);
  });
});

describe("opening a TCP driver an earlier Builder saved", () => {
  it("adds IP Address and Port without marking unsaved changes", () => {
    const state = open(definition({}));
    expect(state.draft.config_schema).toEqual(NETWORK);
    expect(state.addedConnectionFields).toEqual(["host", "port"]);
    expect(state.dirty).toBe(false);
  });

  it("leaves a built-in driver alone", () => {
    const state = open(definition({ source: "builtin" }));
    expect(state.draft.config_schema).toEqual({});
    expect(state.addedConnectionFields).toEqual([]);
  });

  it("asks nothing when the author moves on to another driver", () => {
    const confirm = vi.spyOn(window, "confirm");
    open(definition({}));
    useDriverBuilderStore.getState().selectDriver(null);
    expect(confirm).not.toHaveBeenCalled();
    expect(useDriverBuilderStore.getState().addedConnectionFields).toEqual([]);
  });

  it("saves them, and the note goes", async () => {
    open(definition({}));
    await useDriverBuilderStore.getState().save();
    const sent = api.updateDriverDefinition.mock.calls[0][1] as DriverDefinition;
    expect(sent.config_schema).toEqual(NETWORK);
    expect(useDriverBuilderStore.getState().addedConnectionFields).toEqual([]);
  });
});

describe("the editor", () => {
  function editor(added: string[], dirty = false) {
    return (
      <DriverEditor
        draft={definition({ config_schema: NETWORK })}
        dirty={dirty}
        saving={false}
        error={null}
        isNew={false}
        readOnly={false}
        originalId="acme_widget"
        commentLines={0}
        addedConnectionFields={added}
        onUpdate={() => {}}
        onSave={() => {}}
        onExport={() => {}}
        onDuplicate={() => {}}
      />
    );
  }

  it("says which fields were added and offers Save", () => {
    render(editor(["host", "port"]));
    expect(
      screen.getByText(
        "Added the IP Address and Port fields, which Add Device shows for this driver. Save to keep them.",
      ),
    ).toBeTruthy();
    expect(screen.getByRole("button", { name: "Save" }).hasAttribute("disabled")).toBe(false);
  });

  it("names one field on its own", () => {
    render(editor(["host"]));
    expect(
      screen.getByText(
        "Added the IP Address field, which Add Device shows for this driver. Save to keep it.",
      ),
    ).toBeTruthy();
  });

  it("says nothing for a driver that had them", () => {
    render(editor([]));
    expect(screen.queryByText(/Add Device shows/)).toBeNull();
    expect(screen.getByRole("button", { name: "Save" }).hasAttribute("disabled")).toBe(true);
  });
});
