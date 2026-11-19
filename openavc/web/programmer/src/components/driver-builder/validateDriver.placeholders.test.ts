import { describe, expect, it } from "vitest";

import type { DriverCommandDef, DriverDefinition } from "../../api/types";
import { validateDriverSafely } from "./validateDriver";

function draft(commands: Record<string, DriverCommandDef>): DriverDefinition {
  return {
    id: "acme_widget",
    name: "Acme Widget",
    manufacturer: "Acme",
    category: "utility",
    version: "1.0.0",
    author: "Acme",
    description: "An Acme widget.",
    transport: "tcp",
    commands,
  };
}

const placeholderWarnings = (d: DriverDefinition) =>
  validateDriverSafely(d, [], d.id)
    .filter((i) => i.message.includes("{param1}"))
    .map((i) => i.message);

describe("the Builder's own placeholder check", () => {
  it("leaves a command that declares parameters to the server, which refuses the save", () => {
    expect(
      placeholderWarnings(
        draft({ set_level: { send: "LVL {param1}\r", params: { lvl: { type: "integer" } } } }),
      ),
    ).toEqual([]);
  });

  it("leaves its query parameters and OSC arguments to the server too", () => {
    expect(
      placeholderWarnings(
        draft({
          set_level: {
            method: "GET",
            path: "/level",
            query_params: { v: "{param1}" },
            params: { lvl: { type: "integer" } },
          },
          set_gain: {
            address: "/gain",
            args: [{ type: "f", value: "{param1}" }],
            params: { lvl: { type: "integer" } },
          },
        }),
      ),
    ).toEqual([]);
  });

  it("still warns about a command with no parameters, which the server does not judge", () => {
    expect(
      placeholderWarnings(draft({ set_level: { send: "LVL {param1}\r" } })),
    ).toHaveLength(1);
    expect(
      placeholderWarnings(
        draft({ set_level: { method: "GET", path: "/level", query_params: { v: "{param1}" } } }),
      ),
    ).toHaveLength(1);
  });
});
