import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ProjectConfig } from "../../api/types";
import { syncAfterSetupAction, useProjectStore } from "../../store/projectStore";
import { ConfigTableEditor } from "./ConfigTableEditor";

const { getProject, updateDevice } = vi.hoisted(() => ({
  getProject: vi.fn(), updateDevice: vi.fn(),
}));
vi.mock("../../api/restClient", () => ({
  getProject, updateDevice, ConflictError: class ConflictError extends Error {},
}));

const original = { name: "Existing point", address: 1, enabled: true };
const imported = { name: "Imported point", address: 2, enabled: false };
const schema = {
  label: "Points", row_label: "point",
  columns: {
    name: { type: "string", label: "Name", required: true },
    address: { type: "integer", label: "Address" },
    enabled: { type: "boolean", label: "Enabled" },
  },
};

function project(rows = [original], extra = {}): ProjectConfig {
  return {
    project: { id: "test", name: "Test" },
    devices: [{ id: "widget", driver: "acme_widget", name: "Widget",
      config: { points: rows, interval: 30, ...extra } }],
    connections: {},
  } as unknown as ProjectConfig;
}

function editor(deviceId = "widget", fieldKey = "points") {
  return <ConfigTableEditor deviceId={deviceId} fieldKey={fieldKey}
    fieldSchema={schema} connected onSaved={() => {}} />;
}

async function externalUpdate(next: ProjectConfig) {
  getProject.mockResolvedValue({ ...next, _etag: '"updated"' });
  await act(async () => { await syncAfterSetupAction("widget"); });
}

async function rename(value: string, row = 1) {
  const input = screen.getByRole("textbox", { name: `point ${row} Name` });
  await userEvent.clear(input);
  await userEvent.type(input, value);
}

beforeEach(() => {
  getProject.mockReset().mockResolvedValue(project());
  updateDevice.mockReset().mockResolvedValue({ status: "ok" });
  useProjectStore.setState({ project: project(), dirty: false, saving: false,
    error: null, conflictDetected: false });
});

describe("table rows after a setup action", () => {
  it("shows imported rows in the mounted table and includes them in the next save", async () => {
    render(editor());
    await externalUpdate(project([original, imported]));
    expect(screen.getByDisplayValue("Imported point")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "point 1 Enabled" })).toBeChecked();
    await rename("Renamed import", 2);
    getProject.mockResolvedValue(project([original, { ...imported, name: "Renamed import" }]));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(updateDevice).toHaveBeenCalledWith("widget", { config: {
      points: [original, { ...imported, name: "Renamed import" }], interval: 30,
    } }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Save" })).toBeDisabled());
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("follows an empty table becoming populated, then cleared", async () => {
    useProjectStore.setState({ project: project([]) });
    render(editor());
    await externalUpdate(project([imported]));
    expect(screen.getByDisplayValue("Imported point")).toBeInTheDocument();
    await externalUpdate(project([]));
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  });

  it("saves typed number, boolean and enum cells", async () => {
    render(<ConfigTableEditor deviceId="widget" fieldKey="points" connected onSaved={() => {}}
      fieldSchema={{ ...schema, columns: { ...schema.columns,
        mode: { type: "enum", label: "Mode", values: [{ value: "manual", label: "Manual" }] },
      } }} />);
    const address = screen.getByRole("spinbutton", { name: "point 1 Address" });
    await userEvent.clear(address);
    await userEvent.type(address, "12");
    await userEvent.click(screen.getByRole("checkbox", { name: "point 1 Enabled" }));
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "point 1 Mode" }), "manual");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(updateDevice).toHaveBeenCalledWith("widget", { config: {
      points: [{ ...original, address: 12, enabled: false, mode: "manual" }], interval: 30,
    } });
  });

  it("keeps dirty rows through equal-value refreshes and saves unrelated config updates", async () => {
    render(editor());
    await rename("My edit");
    await externalUpdate(project([{ ...original }], { interval: 60 }));
    expect(screen.getByDisplayValue("My edit")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(updateDevice).toHaveBeenCalledWith("widget", { config: {
      points: [{ ...original, name: "My edit" }], interval: 60,
    } });
  });

  it("preserves edits but blocks a stale save until the user loads updated rows", async () => {
    render(editor());
    await rename("My unsaved edit");
    await externalUpdate(project([original, imported]));
    expect(screen.getByDisplayValue("My unsaved edit")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(updateDevice).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: /Discard my edits/ }));
    expect(screen.getByDisplayValue("Imported point")).toBeInTheDocument();
    expect(screen.getByDisplayValue("Existing point")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    await rename("Kept edit");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(updateDevice).toHaveBeenCalledWith("widget", { config: {
      points: [{ ...original, name: "Kept edit" }, imported], interval: 30,
    } });
  });

  it("retains a failed save for retry and disables row edits during the request", async () => {
    render(editor());
    await rename("Keep this edit");
    let rejectSave!: (reason: Error) => void;
    updateDevice.mockImplementationOnce(() => new Promise((_resolve, reject) => { rejectSave = reject; }));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(screen.getByRole("textbox", { name: "point 1 Name" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Add point" })).toBeDisabled();
    await act(async () => { rejectSave(new Error("Save failed")); });
    expect(screen.getByDisplayValue("Keep this edit")).toBeEnabled();
    expect(screen.getByRole("button", { name: "Save" })).toBeEnabled();
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(updateDevice).toHaveBeenCalledTimes(2);
  });

  it("does not carry drafts into another device or field", async () => {
    const initial = project([original], { other: [imported] });
    initial.devices.push({ ...initial.devices[0], id: "other-widget", config: { points: [imported] } });
    useProjectStore.setState({ project: initial });
    const view = render(editor());
    await rename("Unsaved");
    view.rerender(editor("widget", "other"));
    expect(screen.getByDisplayValue("Imported point")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    view.rerender(editor());
    await rename("Another edit");
    view.rerender(editor("other-widget"));
    expect(screen.getByDisplayValue("Imported point")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });
});
