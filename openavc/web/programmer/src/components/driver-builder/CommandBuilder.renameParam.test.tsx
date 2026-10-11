import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

// Renaming a parameter renames its placeholder in that command's own wire
// strings, so the command keeps sending the value. Another command's
// same-named token belongs to that command and is left alone.

import { CommandBuilder, renamePlaceholder } from "./CommandBuilder";
import type { DriverCommandDef, DriverDefinition } from "../../api/types";

const DRAFT = {
  id: "acme_widget",
  name: "Acme Widget",
  manufacturer: "Acme",
  category: "utility",
  version: "1.0.0",
  transport: "tcp",
  default_config: { host: "", port: 4000 },
  config_schema: {},
  state_variables: { level: { type: "integer", label: "Level" } },
  commands: {
    set_level: {
      label: "Set Level",
      send: "LVL {param1}\r",
      params: { param1: { type: "integer" } },
      sets: { level: "{param1}" },
    },
    set_gain: {
      label: "Set Gain",
      send: "GAIN {param1}\r",
      params: { param1: { type: "integer" } },
    },
  },
  responses: [],
} as unknown as DriverDefinition;

describe("renaming a parameter", () => {
  it("renames its placeholder in that command's send string and Sets State", () => {
    const onUpdate = vi.fn();
    render(<CommandBuilder draft={DRAFT} onUpdate={onUpdate} />);
    fireEvent.click(screen.getByText("set_level"));
    const name = screen.getByDisplayValue("param1");
    fireEvent.change(name, { target: { value: "lvl" } });
    fireEvent.blur(name);
    const commands = onUpdate.mock.calls.at(-1)?.[0].commands;
    expect(Object.keys(commands.set_level.params)).toEqual(["lvl"]);
    expect(commands.set_level.send).toBe("LVL {lvl}\r");
    expect(commands.set_level.sets).toEqual({ level: "{lvl}" });
    expect(commands.set_gain.send).toBe("GAIN {param1}\r");
  });
});

describe("renamePlaceholder", () => {
  it("keeps a format spec and touches only the whole name", () => {
    const cmd = {
      label: "Set",
      send: "L{param1:02X} {param10} {param1}",
      path: "/x/{param1}",
      body: '{"v": {param1}}',
      address: "/ch/{param1}/fader",
      headers: { "X-Level": "{param1}" },
      query_params: { v: "{param1}" },
      args: [{ type: "f", value: "{param1}" }],
      udp: { payload: "{param1}", port: "{param1}" },
      params: { param1: { type: "integer" } },
    } as unknown as DriverCommandDef;
    const out = renamePlaceholder(cmd, "param1", "lvl");
    expect(out.send).toBe("L{lvl:02X} {param10} {lvl}");
    expect(out.path).toBe("/x/{lvl}");
    expect(out.body).toBe('{"v": {lvl}}');
    expect(out.address).toBe("/ch/{lvl}/fader");
    expect(out.headers).toEqual({ "X-Level": "{lvl}" });
    expect(out.query_params).toEqual({ v: "{lvl}" });
    expect(out.args).toEqual([{ type: "f", value: "{lvl}" }]);
    expect(out.udp).toEqual({ payload: "{lvl}", port: "{lvl}" });
  });

  it("leaves a command that never names the parameter unchanged", () => {
    const cmd = { label: "Set", send: "PWR 1\r", params: {} } as unknown as DriverCommandDef;
    expect(renamePlaceholder(cmd, "param1", "lvl")).toEqual({});
  });
});
