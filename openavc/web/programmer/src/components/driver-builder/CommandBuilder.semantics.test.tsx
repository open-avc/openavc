import { describe, it, expect, vi } from "vitest";
import { useState } from "react";
import { fireEvent, render, screen } from "@testing-library/react";

// What a command's Reports and Sets State fields offer and write, read
// through the command list itself. A stateful host feeds every write back
// into the draft the way the Driver Editor does, so a row being typed into
// keeps its box across the re-render a write causes.

import { CommandBuilder } from "./CommandBuilder";
import type { DriverCommandDef, DriverDefinition } from "../../api/types";

function draftWith(
  commands: Record<string, Partial<DriverCommandDef>>,
  extra: Partial<DriverDefinition> = {},
): DriverDefinition {
  return {
    id: "acme_widget",
    name: "Acme Widget",
    manufacturer: "Acme",
    category: "utility",
    version: "1.0.0",
    transport: "tcp",
    default_config: { host: "", port: 4000 },
    config_schema: {},
    state_variables: {},
    commands,
    responses: [],
    ...extra,
  } as unknown as DriverDefinition;
}

function Host({ initial, seen }: { initial: DriverDefinition; seen: (d: DriverDefinition) => void }) {
  const [draft, setDraft] = useState(initial);
  return (
    <CommandBuilder
      draft={draft}
      onUpdate={(partial) =>
        setDraft((d) => {
          const next = { ...d, ...partial };
          seen(next);
          return next;
        })
      }
    />
  );
}

/** The editor over `initial`, with its first command opened. */
function host(initial: DriverDefinition) {
  const seen = vi.fn();
  const view = render(<Host initial={initial} seen={seen} />);
  const name = Object.keys(initial.commands)[0];
  fireEvent.click(screen.getByText(name));
  const cmd = (): DriverCommandDef => {
    const calls = seen.mock.calls;
    const commands = calls.length ? calls[calls.length - 1][0].commands : initial.commands;
    return commands[name];
  };
  return { ...view, seen, cmd };
}

const DEVICE_VARS = {
  power: { type: "boolean", label: "Power" },
  volume: { type: "integer", label: "Volume" },
  gain: { type: "number", label: "Gain" },
  trim: { type: "float", label: "Trim" },
  source: { type: "string", label: "Source" },
};

const ZONES = {
  zone: {
    id_format: { type: "integer", min: 1, max: 4 },
    state_variables: {
      mute: { type: "boolean" },
      level: { type: "integer" },
    },
  },
};

function reportsSelect(): HTMLSelectElement {
  return screen.getByText("Reports", { selector: "label" }).parentElement!.querySelector("select")!;
}
const keySelects = () =>
  screen.queryAllByTitle("State variable this command sets") as HTMLSelectElement[];
const modeSelects = () =>
  screen.queryAllByTitle("Set it to a parameter's value, or a literal") as HTMLSelectElement[];
const optionTexts = (el: HTMLElement) =>
  Array.from(el.querySelectorAll("option")).map((o) => `${o.value}=${o.textContent}`);
const groupLabels = (el: HTMLElement) =>
  Array.from(el.querySelectorAll("optgroup")).map((g) => g.getAttribute("label"));
/** The literal box of the row at `index` (the input in that row, if any). */
const literalBox = (index: number) =>
  keySelects()[index].parentElement!.querySelector("input") as HTMLInputElement | null;
const rowGrid = (index: number) =>
  (keySelects()[index].parentElement as HTMLElement).style.gridTemplateColumns;

describe("whether the fields show", () => {
  it("are absent with nothing to name and nothing written", () => {
    host(draftWith({ ping: { label: "Ping", send: "P\r" } }));
    expect(screen.queryByText("Sets State")).toBeNull();
    expect(screen.queryByText("Reports", { selector: "label" })).toBeNull();
  });

  it("show for a sets table naming nothing declared, with the name marked", () => {
    host(draftWith({ ping: { label: "Ping", send: "P\r", sets: { ghost: 1 } } }));
    expect(screen.getByText("Sets State")).toBeTruthy();
    expect(optionTexts(keySelects()[0])).toEqual(["ghost=ghost (not declared)"]);
    expect(screen.queryByText("+ Set a variable")).toBeNull();
  });

  it("show for a query_for alone, the Reports box marking it", () => {
    host(draftWith({ ping: { label: "Ping", send: "P\r", query_for: "ghost" } }));
    expect(reportsSelect().value).toBe("ghost");
    expect(optionTexts(reportsSelect())).toEqual(["=Reports (auto)", "ghost=Reports ghost (not declared)"]);
    expect(keySelects()).toHaveLength(0);
  });

  it("read a sets list as no rows and a non-string query_for as auto", () => {
    host(
      draftWith(
        { ping: { label: "Ping", send: "P\r", sets: ["power"] as never, query_for: 3 as never } },
        { state_variables: DEVICE_VARS } as never,
      ),
    );
    expect(keySelects()).toHaveLength(0);
    expect(reportsSelect().value).toBe("");
  });
});

describe("Reports", () => {
  it("offers every device variable, and a pick writes query_for", () => {
    const h = host(draftWith({ status: { label: "Status", send: "S?\r" } }, { state_variables: DEVICE_VARS } as never));
    expect(optionTexts(reportsSelect())).toEqual([
      "=Reports (auto)",
      "power=Reports power",
      "volume=Reports volume",
      "gain=Reports gain",
      "trim=Reports trim",
      "source=Reports source",
    ]);
    expect(groupLabels(reportsSelect())).toEqual([]);
    fireEvent.change(reportsSelect(), { target: { value: "volume" } });
    expect(h.cmd().query_for).toBe("volume");
    fireEvent.change(reportsSelect(), { target: { value: "" } });
    expect("query_for" in h.cmd()).toBe(false);
  });

  it("groups a child-addressed command's names, the child's shadowing the device's", () => {
    host(
      draftWith(
        {
          zone_status: {
            label: "Zone Status",
            send: "Z{zone}?\r",
            params: { zone: { type: "child_id", child_type: "zone" } },
          },
        },
        {
          state_variables: { power: { type: "boolean" }, level: { type: "number" } },
          child_entity_types: ZONES,
        } as never,
      ),
    );
    expect(groupLabels(reportsSelect())).toEqual(["Device variables", "zone variables"]);
    expect(optionTexts(reportsSelect())).toEqual([
      "=Reports (auto)",
      "power=Reports power",
      "mute=Reports mute",
      "level=Reports level",
    ]);
    expect(screen.getByText(/Variables of the/).textContent).toContain("zone child type");
  });

  it("does not group for two child_id params or an undeclared child type", () => {
    const vars = { state_variables: { power: { type: "boolean" } }, child_entity_types: ZONES } as never;
    const two = host(
      draftWith(
        {
          link: {
            label: "Link",
            send: "L{a}{b}\r",
            params: {
              a: { type: "child_id", child_type: "zone" },
              b: { type: "child_id", child_type: "zone" },
            },
          },
        },
        vars,
      ),
    );
    expect(groupLabels(reportsSelect())).toEqual([]);
    expect(optionTexts(reportsSelect())).toEqual(["=Reports (auto)", "power=Reports power"]);
    expect(screen.queryByText(/Variables of the/)).toBeNull();
    two.unmount();
    host(
      draftWith(
        { solo: { label: "Solo", send: "S{a}\r", params: { a: { type: "child_id", child_type: "room" } } } },
        vars,
      ),
    );
    expect(groupLabels(reportsSelect())).toEqual([]);
  });

  it("leaves out an empty group", () => {
    host(
      draftWith(
        { zone_mute: { label: "Mute", send: "M{zone}\r", params: { zone: { type: "child_id", child_type: "zone" } } } },
        { child_entity_types: ZONES } as never,
      ),
    );
    expect(groupLabels(reportsSelect())).toEqual(["zone variables"]);
  });
});

describe("Sets State rows", () => {
  const vars = { state_variables: DEVICE_VARS } as never;

  it("adds the first unused variable, seeded with the first parameter", () => {
    const h = host(
      draftWith(
        { set_power: { label: "Power", send: "P{state}{other}\r", params: { state: { type: "boolean" }, other: { type: "string" } } } },
        vars,
      ),
    );
    fireEvent.click(screen.getByText("+ Set a variable"));
    expect(h.cmd().sets).toEqual({ power: "{state}" });
    fireEvent.click(screen.getByText("+ Set a variable"));
    expect(JSON.stringify(h.cmd().sets)).toBe('{"power":"{state}","volume":"{state}"}');
    expect(modeSelects()[0].value).toBe("{state}");
    expect(rowGrid(0)).toBe("1fr 1fr auto");
    expect(optionTexts(modeSelects()[0])).toEqual([
      "{state}={state}",
      "{other}={other}",
      "__literal__=literal…",
    ]);
  });

  it("adds a literal row with no parameters, and stops offering once every variable is used", () => {
    const h = host(
      draftWith({ on: { label: "On", send: "ON\r" } }, { state_variables: { power: { type: "boolean" } } } as never),
    );
    fireEvent.click(screen.getByText("+ Set a variable"));
    expect(h.cmd().sets).toEqual({ power: "" });
    expect(screen.queryByText("+ Set a variable")).toBeNull();
    expect(modeSelects()[0].value).toBe("__literal__");
    expect(rowGrid(0)).toBe("1fr 1fr 1fr auto");
    expect(literalBox(0)!.placeholder).toBe("true / false");
  });

  it("renames a row's variable in place, offering only names the other rows do not use", () => {
    const h = host(
      draftWith({ preset: { label: "Preset", send: "X\r", sets: { power: true, volume: 3, gain: 1.5 } } }, vars),
    );
    expect(optionTexts(keySelects()[1])).toEqual([
      "volume=volume",
      "trim=trim",
      "source=source",
    ]);
    expect(keySelects().map((el) => el.value)).toEqual(["power", "volume", "gain"]);
    keySelects()[1].focus();
    fireEvent.change(keySelects()[1], { target: { value: "source" } });
    expect(document.activeElement).toBe(keySelects()[1]);
    expect(JSON.stringify(h.cmd().sets)).toBe('{"power":true,"source":3,"gain":1.5}');
    expect(keySelects().map((el) => el.value)).toEqual(["power", "source", "gain"]);
  });

  it("switches a row between a parameter and a literal", () => {
    const h = host(
      draftWith(
        { set_volume: { label: "Volume", send: "V{v}\r", params: { v: { type: "integer" } }, sets: { volume: 5 } } },
        vars,
      ),
    );
    expect(modeSelects()[0].value).toBe("__literal__");
    expect(literalBox(0)!.value).toBe("5");
    fireEvent.change(modeSelects()[0], { target: { value: "{v}" } });
    expect(h.cmd().sets).toEqual({ volume: "{v}" });
    expect(literalBox(0)).toBeNull();
    fireEvent.change(modeSelects()[0], { target: { value: "__literal__" } });
    expect(h.cmd().sets).toEqual({ volume: "" });
    expect(literalBox(0)!.value).toBe("");
  });

  it("changes one row's value and leaves the others as they were", () => {
    const h = host(draftWith({ x: { label: "X", send: "X\r", sets: { power: true, volume: 2, gain: 0.5 } } }, vars));
    fireEvent.change(literalBox(1)!, { target: { value: "9" } });
    expect(JSON.stringify(h.cmd().sets)).toBe('{"power":true,"volume":"9","gain":0.5}');
  });

  it("shows a reference to an undeclared parameter as a literal", () => {
    host(draftWith({ x: { label: "X", send: "X\r", sets: { source: "{ghost}" } } }, vars));
    expect(modeSelects()[0].value).toBe("__literal__");
    expect(literalBox(0)!.value).toBe("{ghost}");
    expect(literalBox(0)!.placeholder).toBe("value");
  });

  it("removes a row, and the last one takes the key with it", () => {
    const h = host(draftWith({ x: { label: "X", send: "X\r", sets: { power: true, volume: 2 } } }, vars));
    const remove = () => keySelects()[0].parentElement!.querySelector("button")!;
    fireEvent.click(remove());
    expect(h.cmd().sets).toEqual({ volume: 2 });
    fireEvent.click(remove());
    expect("sets" in h.cmd()).toBe(false);
  });
});

describe("a typed literal", () => {
  const vars = { state_variables: DEVICE_VARS } as never;

  /** Type `raw` into the literal box of a row setting `variable`, then leave it. */
  function typeAndLeave(variable: string, raw: string) {
    const h = host(draftWith({ x: { label: "X", send: "X\r", sets: { [variable]: "" } } }, vars));
    const box = literalBox(0)!;
    box.focus();
    fireEvent.change(box, { target: { value: raw } });
    const typed = h.cmd().sets![variable];
    expect(document.activeElement).toBe(literalBox(0));
    fireEvent.blur(literalBox(0)!);
    const left = h.cmd().sets![variable];
    h.unmount();
    return { typed, left };
  }

  it("is kept as typed until the box is left", () => {
    expect(typeAndLeave("volume", "42").typed).toBe("42");
  });

  it("becomes the variable's type on leaving", () => {
    expect(typeAndLeave("power", "true").left).toBe(true);
    expect(typeAndLeave("power", " false ").left).toBe(false);
    expect(typeAndLeave("power", "yes").left).toBe("yes");
    expect(typeAndLeave("volume", " 42 ").left).toBe(42);
    expect(typeAndLeave("volume", "-7").left).toBe(-7);
    expect(typeAndLeave("volume", "4.2").left).toBe("4.2");
    expect(typeAndLeave("gain", "3.5").left).toBe(3.5);
    expect(typeAndLeave("gain", "abc").left).toBe("abc");
    expect(typeAndLeave("gain", "  ").left).toBe("  ");
    expect(typeAndLeave("trim", "-1e2").left).toBe(-100);
    expect(typeAndLeave("source", "12").left).toBe("12");
  });

  it("follows the addressed child's type over the device's", () => {
    const h = host(
      draftWith(
        {
          zone_level: {
            label: "Level",
            send: "L{zone}\r",
            params: { zone: { type: "child_id", child_type: "zone" } },
            sets: { level: "" },
          },
        },
        { state_variables: { level: { type: "number" } }, child_entity_types: ZONES } as never,
      ),
    );
    fireEvent.change(literalBox(0)!, { target: { value: "2.5" } });
    fireEvent.blur(literalBox(0)!);
    expect(h.cmd().sets).toEqual({ level: "2.5" });
    fireEvent.change(literalBox(0)!, { target: { value: "2" } });
    fireEvent.blur(literalBox(0)!);
    expect(h.cmd().sets).toEqual({ level: 2 });
  });
});
