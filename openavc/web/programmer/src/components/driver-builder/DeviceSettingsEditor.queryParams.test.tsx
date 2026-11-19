import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

// The Driver Builder half of a device setting's HTTP write taking
// query_params (the runtime half is setting_writes.py, through the request
// builder a command uses). What has to hold: an HTTP write shows its query
// parameters, an edit keeps them beside the write's other HTTP fields, and a
// transport switch away from HTTP drops them with the rest.

import { DeviceSettingsEditor } from "./DeviceSettingsEditor";
import { normalizeWriteForTransport, writeHasForeignKeys } from "./deviceSettingsHelpers";
import type { DriverDefinition } from "../../api/types";

function httpDraft(): DriverDefinition {
  return {
    id: "acme_widget",
    name: "Acme Widget",
    manufacturer: "Acme",
    category: "utility",
    version: "1.0.0",
    transport: "http",
    state_variables: { name: { type: "string", label: "Name" } },
    device_settings: {
      name: {
        type: "string",
        label: "Name",
        write: { method: "GET", path: "/api/name", query_params: { set: "{value}" } },
      },
    },
  };
}

describe("a setting's HTTP write and its query parameters", () => {
  it("shows them and keeps them beside the write's other fields when one changes", () => {
    const onUpdate = vi.fn();
    render(<DeviceSettingsEditor draft={httpDraft()} onUpdate={onUpdate} />);
    fireEvent.click(screen.getByTestId("device-setting-name"));

    expect(screen.getByText("Query parameters (optional)")).toBeTruthy();
    const value = screen.getByDisplayValue("{value}");
    fireEvent.change(value, { target: { value: "{value:d}" } });

    const written = onUpdate.mock.calls.at(-1)?.[0].device_settings.name.write;
    expect(written).toEqual({ method: "GET", path: "/api/name", query_params: { set: "{value:d}" } });
  });

  it("are an HTTP field, dropped when the write moves to another transport", () => {
    const write = { method: "GET", path: "/api/name", query_params: { set: "{value}" } };
    expect(writeHasForeignKeys(write, "http")).toBe(false);
    expect(normalizeWriteForTransport(write, "http")).toEqual(write);
    expect(normalizeWriteForTransport(write, "tcp")).toEqual({});
  });
});
