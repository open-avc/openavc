import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import yaml from "js-yaml";
import type { DriverDefinition, ProjectConfig } from "../api/types";

vi.mock("../api/restClient", () => ({
  listDriverDefinitions: vi.fn(),
  createDriverDefinition: vi.fn(),
  updateDriverDefinition: vi.fn(),
}));
vi.mock("../api/driverClient", () => ({ validateDriverDefinition: vi.fn() }));
vi.mock("../components/driver-builder/InstalledDriversView", () => ({ InstalledDriversView: () => null }));
vi.mock("../components/driver-builder/CommunityBrowser", () => ({ CommunityBrowser: () => null }));
vi.mock("../components/driver-builder/DriverEditor", () => ({
  DriverEditor: ({ error }: { error: string | null }) => <div>{error}</div>,
}));

import * as api from "../api/restClient";
import { validateDriverDefinition } from "../api/driverClient";
import { useDriverBuilderStore } from "../store/driverBuilderStore";
import { useProjectStore } from "../store/projectStore";
import { DriverPanel } from "./DriverBuilderView";
import { serializeDriverDefinition } from "../store/driverBuilderStore.helpers";

const original = {
  id: "acme_amp", name: "Acme amplifier", transport: "tcp", source: "user",
  default_config: { port: 4000 }, config_schema: {}, state_variables: {},
  commands: { mute: { label: "Mute", send: "MUTE\\n" } }, responses: [], polling: {},
} as unknown as DriverDefinition;
const edited = { ...original, version: "2.0.0", commands: {
  ...original.commands, level: { label: "Level", send: "LEVEL {level}\\n", params: {
    level: { type: "integer", min: -60, max: 0 },
  } },
} } as DriverDefinition;

async function offerImport(definition = edited) {
  render(<DriverPanel />);
  fireEvent.click(screen.getByRole("button", { name: "Create", exact: true }));
  await waitFor(() => expect(useDriverBuilderStore.getState().loading).toBe(false));
  fireEvent.click(screen.getByRole("button", { name: "Import from File" }));
  fireEvent.change(screen.getByRole("textbox"), { target: { value: JSON.stringify(definition) } });
  fireEvent.click(screen.getByRole("button", { name: "Import", exact: true }));
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.listDriverDefinitions).mockResolvedValue([original]);
  vi.mocked(validateDriverDefinition).mockResolvedValue({ issues: [] });
  vi.mocked(api.updateDriverDefinition).mockResolvedValue({ status: "updated", driver_id: original.id });
  vi.mocked(api.createDriverDefinition).mockResolvedValue({ status: "created", driver_id: original.id });
  useDriverBuilderStore.setState({ definitions: [original], selectedId: null, dirty: false,
    saving: false, loading: false, error: null, saveRefusal: null });
  useProjectStore.setState({ project: { devices: [
    { id: "room_amp", name: "Room amplifier", driver: original.id, config: { password: "private-device-secret" } },
  ] } as unknown as ProjectConfig });
});

describe("Importing an edited driver", () => {
  it("asks before updating, names affected devices, and calls update with the same ID", async () => {
    await offerImport();
    expect(await screen.findByRole("alertdialog", { name: "Update Existing Driver" })).toHaveTextContent("Room amplifier");
    expect(screen.queryByText("private-device-secret")).toBeNull();
    expect(api.updateDriverDefinition).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Update Driver", exact: true }));
    await waitFor(() => expect(api.updateDriverDefinition).toHaveBeenCalledWith(original.id,
      expect.objectContaining({ id: original.id, version: "2.0.0", commands: edited.commands })));
    expect(api.createDriverDefinition).not.toHaveBeenCalled();
    expect(useProjectStore.getState().project?.devices[0].id).toBe("room_amp");
  });

  it("cancels without replacing the saved definition or the current draft", async () => {
    const draft = useDriverBuilderStore.getState().draft;
    await offerImport();
    await screen.findByRole("alertdialog");
    fireEvent.click(screen.getByRole("button", { name: "Cancel", exact: true }));
    expect(api.createDriverDefinition).not.toHaveBeenCalled();
    expect(api.updateDriverDefinition).not.toHaveBeenCalled();
    expect(useDriverBuilderStore.getState().draft).toBe(draft);
    expect(useDriverBuilderStore.getState().definitions[0]).toEqual(original);
  });

  it("imports a separate copy without changing existing device bindings", async () => {
    await offerImport();
    await screen.findByRole("alertdialog");
    fireEvent.click(screen.getByRole("button", { name: "Import a Copy" }));
    await waitFor(() => expect(api.createDriverDefinition).toHaveBeenCalledWith(
      expect.objectContaining({ id: "acme_amp_copy", commands: edited.commands, verified: false })));
    expect(api.updateDriverDefinition).not.toHaveBeenCalled();
    expect(useProjectStore.getState().project?.devices[0].driver).toBe(original.id);
  });

  it("never offers to overwrite a built-in definition", async () => {
    vi.mocked(api.listDriverDefinitions).mockResolvedValue([{ ...original, source: "builtin" }]);
    await offerImport();
    expect(await screen.findByRole("alertdialog", { name: "Import a Driver Copy" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Update Driver" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Import a Copy" }));
    await waitFor(() => expect(api.createDriverDefinition).toHaveBeenCalled());
    expect(api.updateDriverDefinition).not.toHaveBeenCalled();
  });

  it("keeps invalid updates as an editable draft without writing over the running driver", async () => {
    vi.mocked(validateDriverDefinition).mockResolvedValue({ issues: [
      { severity: "error", path: "commands.level", message: "Invalid command" },
    ] });
    await offerImport();
    await screen.findByRole("alertdialog");
    fireEvent.click(screen.getByRole("button", { name: "Update Driver", exact: true }));
    await waitFor(() => expect(useDriverBuilderStore.getState().dirty).toBe(true));
    expect(useDriverBuilderStore.getState().selectedId).toBe(original.id);
    expect(useDriverBuilderStore.getState().definitions[0]).toEqual(original);
    expect(useDriverBuilderStore.getState().draft.commands).toEqual(edited.commands);
    expect(api.updateDriverDefinition).not.toHaveBeenCalled();
    expect(api.createDriverDefinition).not.toHaveBeenCalled();
  });

  it("does not fall back to create when the server refuses an update", async () => {
    vi.mocked(api.updateDriverDefinition).mockRejectedValueOnce(new Error("Driver is protected"));
    await useDriverBuilderStore.getState().importDriver(edited, true);
    expect(useDriverBuilderStore.getState().error).toContain("Driver is protected");
    expect(useDriverBuilderStore.getState().definitions[0]).toEqual(original);
    expect(api.createDriverDefinition).not.toHaveBeenCalled();
  });

  it("still imports a new ID directly", async () => {
    await offerImport({ ...edited, id: "another_amp" });
    await waitFor(() => expect(api.createDriverDefinition).toHaveBeenCalledWith(
      expect.objectContaining({ id: "another_amp" })));
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(api.updateDriverDefinition).not.toHaveBeenCalled();
  });
});

it("exports contract fields and nested source values without listing metadata", () => {
  const definition = { ...edited, _comment_lines: 4, _source_file: "/private/path",
    source_url: "https://example.com/protocol", default_config: { source: "input1" },
    future_field: "preserve so validation can identify it",
  } as DriverDefinition;
  const result = yaml.load(serializeDriverDefinition(definition)) as Record<string, unknown>;
  expect(result).not.toHaveProperty("source");
  expect(result).not.toHaveProperty("_comment_lines");
  expect(result).not.toHaveProperty("_source_file");
  expect(result).toMatchObject({ commands: edited.commands, default_config: { source: "input1" },
    source_url: "https://example.com/protocol", future_field: "preserve so validation can identify it" });
  expect(definition).toHaveProperty("source", "user");
});

it("keeps wire line endings unchanged when another section is appended to an export", () => {
  const definition = { ...original,
    delimiter: "\n",
    commands: {
      read: { send: "STATUS\n" },
      write: { send: "SET\r\n" },
      blank: { send: "\n\n" },
    },
    auth: { type: "telnet_login", line_ending: "\n" },
  } as DriverDefinition;
  const exported = serializeDriverDefinition(definition);
  expect(exported).toContain('line_ending: "\\n"');
  expect(exported).toContain('send: "STATUS\\n"');
  // This ordinary edit used to extend a trailing |+ login delimiter to two LF.
  const extended = yaml.load(exported + '\n\non_connect:\n  - "STATUS\\n"\n') as DriverDefinition;
  expect(extended.auth?.line_ending).toBe("\n");
  expect(extended.delimiter).toBe("\n");
  expect(extended.commands).toEqual(definition.commands);
  expect(extended.on_connect).toEqual(["STATUS\n"]);
});
