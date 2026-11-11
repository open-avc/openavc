import { describe, it, expect, vi } from "vitest";
import { useState } from "react";
import { fireEvent, render, screen } from "@testing-library/react";

// What a command's parameter editor offers and writes, read through the
// command list (where a rename also renames the placeholder) and on its own
// (an action's input override, which has no command strings to follow). A
// stateful host feeds every write back, so a name being typed, a refused
// rename and a unit list mid-entry are seen across the re-render a write
// causes.

import { CommandBuilder } from "./CommandBuilder";
import { ParamEditor } from "./ParamEditor";
import type { DriverCommandDef, DriverDefinition, DriverParamDef } from "../../api/types";

type Params = Record<string, Partial<DriverParamDef>>;

function draftWith(params: Params, extra: Partial<DriverDefinition> = {}): DriverDefinition {
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
    commands: { go: { label: "Go", send: "GO {a}\r", params } },
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

/** The command list over a one-command draft, that command opened. */
function host(params: Params, extra: Partial<DriverDefinition> = {}) {
  const initial = draftWith(params, extra);
  const seen = vi.fn();
  const view = render(<Host initial={initial} seen={seen} />);
  fireEvent.click(screen.getByText("go"));
  const cmd = (): DriverCommandDef => {
    const calls = seen.mock.calls;
    return (calls.length ? calls[calls.length - 1][0] : initial).commands.go;
  };
  const params_ = () => cmd().params as Record<string, DriverParamDef>;
  return { ...view, seen, cmd, params: params_, writes: () => seen.mock.calls.length };
}

/** The parameter editor alone, as an action's override uses it (no onRename). */
function OwnHost({ initial, seen }: { initial: Params; seen: (p: Params) => void }) {
  const [params, setParams] = useState(initial);
  return (
    <ParamEditor
      params={params as Record<string, DriverParamDef>}
      childTypes={[]}
      stateKeys={[]}
      onChange={(next) => {
        seen(next);
        setParams(next);
      }}
    />
  );
}

const nameBox = (name: string) => screen.getAllByDisplayValue(name)[0] as HTMLInputElement;
/** The control under a parameter row's caption. */
function under(caption: string, scope: HTMLElement = document.body): HTMLInputElement {
  const span = Array.from(scope.querySelectorAll("span")).find((s) => s.textContent === caption)!;
  return span.parentElement!.querySelector("input, select") as HTMLInputElement;
}
const captions = (scope: HTMLElement) =>
  Array.from(scope.querySelectorAll("span")).map((s) => s.textContent);
/** The grid of help/default/min/max/decimals/unit boxes in a row. */
const valueGrid = (scope: HTMLElement) =>
  (under("Help Text", scope).parentElement!.parentElement as HTMLElement).style.gridTemplateColumns;

function type(el: HTMLElement, value: string) {
  (el as HTMLInputElement).focus();
  fireEvent.change(el, { target: { value } });
}

describe("the parameter list", () => {
  it("adds the first free paramN after the count, as a string", () => {
    const h = host({ param2: { type: "integer" } });
    fireEvent.click(screen.getByText("+ Add Parameter"));
    expect(JSON.stringify(h.params())).toBe('{"param2":{"type":"integer"},"param3":{"type":"string"}}');
    fireEvent.click(screen.getByText("+ Add Parameter"));
    expect(Object.keys(h.params())).toEqual(["param2", "param3", "param4"]);
  });

  it("skips every taken name after the count", () => {
    const h = host({ param3: { type: "string" }, param4: { type: "integer" } });
    fireEvent.click(screen.getByText("+ Add Parameter"));
    expect(JSON.stringify(h.params())).toBe(
      '{"param3":{"type":"string"},"param4":{"type":"integer"},"param5":{"type":"string"}}',
    );
  });

  it("removes a parameter", () => {
    const h = host({ a: { type: "string" }, b: { type: "integer" } });
    fireEvent.click(screen.getAllByTitle("Remove parameter")[0]);
    expect(h.params()).toEqual({ b: { type: "integer" } });
  });
});

describe("renaming a parameter", () => {
  it("renames it in place and in the command's send, on leaving the box", () => {
    const h = host({ z: { type: "string" }, a: { type: "integer", label: "A" }, c: { type: "string" } });
    type(nameBox("a"), "level");
    expect(h.writes()).toBe(0);
    fireEvent.blur(nameBox("level"));
    expect(Object.keys(h.params())).toEqual(["z", "level", "c"]);
    expect(h.params().level).toEqual({ type: "integer", label: "A" });
    expect(h.cmd().send).toBe("GO {level}\r");
  });

  it("trims the name, and Enter commits", () => {
    const h = host({ a: { type: "string" } });
    const box = nameBox("a");
    type(box, "  b  ");
    fireEvent.keyDown(box, { key: "Enter" });
    expect(Object.keys(h.params())).toEqual(["b"]);
    expect(nameBox("b").value).toBe("b");
  });

  it("refuses a taken name, keeps the text and says why", () => {
    const h = host({ a: { type: "string" }, b: { type: "string" } });
    type(nameBox("a"), "b");
    fireEvent.blur(screen.getAllByDisplayValue("b")[0]);
    expect(h.writes()).toBe(0);
    expect(screen.getByText('A parameter named "b" already exists.')).toBeTruthy();
    const box = screen.getAllByDisplayValue("b")[0] as HTMLInputElement;
    expect(box.style.borderColor).toBe("var(--color-error)");
  });

  it("refuses a name that is not an identifier", () => {
    const h = host({ a: { type: "string" } });
    type(nameBox("a"), "9 lives");
    fireEvent.blur(nameBox("9 lives"));
    expect(h.writes()).toBe(0);
    expect(
      screen.getByText("Use letters, digits, and underscores only. Must start with a letter or underscore."),
    ).toBeTruthy();
    expect(nameBox("9 lives").value).toBe("9 lives");
  });

  it("writes nothing when the box is left unchanged", () => {
    const h = host({ a: { type: "string" } });
    const box = nameBox("a");
    box.focus();
    fireEvent.blur(box);
    expect(h.writes()).toBe(0);
    expect(screen.queryByText(/already exists|Use letters/)).toBeNull();
  });

  it("clears a refusal when the original name is typed back", () => {
    const h = host({ a: { type: "string" }, b: { type: "string" } });
    const box = nameBox("a");
    type(box, "b");
    fireEvent.blur(box);
    expect(screen.getByText(/already exists/)).toBeTruthy();
    type(box, "a");
    fireEvent.blur(box);
    expect(screen.queryByText(/already exists/)).toBeNull();
    expect(h.writes()).toBe(0);
  });

  it("Escape puts the name back and clears the refusal", () => {
    host({ a: { type: "string" }, b: { type: "string" } });
    type(nameBox("a"), "b");
    fireEvent.blur(screen.getAllByDisplayValue("b")[0]);
    fireEvent.keyDown(screen.getAllByDisplayValue("b")[0], { key: "Escape" });
    expect(screen.queryByText(/already exists/)).toBeNull();
    expect(screen.getAllByDisplayValue("a")).toHaveLength(1);
  });

  it("writes nothing for a blank name or the same name, and clears a refusal", () => {
    const h = host({ a: { type: "string" }, b: { type: "string" } });
    const box = nameBox("a");
    type(box, "b");
    fireEvent.blur(box);
    expect(screen.getByText(/already exists/)).toBeTruthy();
    type(box, "");
    fireEvent.blur(box);
    expect(h.writes()).toBe(0);
    expect(screen.queryByText(/already exists/)).toBeNull();
    // A blanked box stays blank: nothing renamed, so nothing re-syncs it.
    expect(box.value).toBe("");
    const other = nameBox("b");
    type(other, " b ");
    fireEvent.blur(other);
    expect(h.writes()).toBe(0);
    expect(other.value).toBe(" b ");
  });

  it("on its own (an action's inputs) writes the renamed list through onChange", () => {
    const seen = vi.fn();
    render(<OwnHost initial={{ a: { type: "string" }, c: { type: "integer" } }} seen={seen} />);
    type(nameBox("a"), "b");
    fireEvent.blur(nameBox("b"));
    expect(seen).toHaveBeenCalledTimes(1);
    expect(JSON.stringify(seen.mock.calls[0][0])).toBe('{"b":{"type":"string"},"c":{"type":"integer"}}');
    fireEvent.click(screen.getByText("+ Add Parameter"));
    expect(Object.keys(seen.mock.calls[1][0])).toEqual(["b", "c", "param3"]);
  });
});

describe("a parameter's name, label, type and required mark", () => {
  it("writes a label, and a cleared one is removed", () => {
    const h = host({ input_id: { type: "string" } });
    const label = under("Display Label");
    expect(label.placeholder).toBe("Input ID");
    type(label, "Input");
    expect(h.params().input_id).toEqual({ type: "string", label: "Input" });
    type(under("Display Label"), "");
    expect(h.params().input_id).toEqual({ type: "string" });
  });

  it("ticks and unticks Required", () => {
    const h = host({ a: { type: "string" } });
    const box = screen.getByText("Required").querySelector("input")!;
    fireEvent.click(box);
    expect(h.params().a).toEqual({ type: "string", required: true });
    fireEvent.click(screen.getByText("Required").querySelector("input")!);
    expect(h.params().a).toEqual({ type: "string" });
  });

  it("offers the six types in order, showing the parameter's own", () => {
    host({ a: { type: "integer" } });
    expect((under("Type") as unknown as HTMLSelectElement).value).toBe("integer");
    const opts = Array.from(under("Type").querySelectorAll("option")).map((o) => `${o.value}=${o.textContent}`);
    expect(opts).toEqual([
      "string=String",
      "integer=Integer",
      "number=Number",
      "boolean=Boolean",
      "enum=Enum",
      "child_id=Child ID",
    ]);
  });

  it("drops the fields the new type has no use for, and keeps the rest", () => {
    const h = host({
      a: {
        type: "number",
        label: "A",
        min: 0,
        max: 10,
        decimals: 1,
        scale: 2,
        offset: 1,
        unit: "dB",
        pattern: "x",
        values: ["p"],
        child_type: "zone",
      } as never,
    });
    fireEvent.change(under("Type"), { target: { value: "integer" } });
    expect(JSON.stringify(h.params().a)).toBe(
      '{"type":"integer","label":"A","min":0,"max":10,"scale":2,"offset":1,"unit":"dB","pattern":"x"}',
    );
    fireEvent.change(under("Type"), { target: { value: "enum" } });
    expect(JSON.stringify(h.params().a)).toBe('{"type":"enum","label":"A","unit":"dB","pattern":"x"}');
  });

  it("shows Allowed Values on an enum only, and a change to string drops them", () => {
    const h = host({ a: { type: "string", values: ["x"] } as never });
    expect(captions(document.body)).not.toContain("Allowed Values");
    fireEvent.change(under("Type"), { target: { value: "string" } });
    expect(JSON.stringify(h.params().a)).toBe('{"type":"string"}');
  });

  it("keeps an enum's values and a number's decimals on their own type", () => {
    const h = host({ a: { type: "enum", values: ["x"], decimals: 2 } as never });
    fireEvent.change(under("Type"), { target: { value: "enum" } });
    expect(JSON.stringify(h.params().a)).toBe('{"type":"enum","values":["x"]}');
    h.unmount();
    const n = host({ a: { type: "integer", decimals: 2, values: ["x"] } as never });
    fireEvent.change(under("Type"), { target: { value: "number" } });
    expect(JSON.stringify(n.params().a)).toBe('{"type":"number","decimals":2}');
  });

  it("starts a Child ID on the first child type, or keeps the one it has", () => {
    const zones = { child_entity_types: { zone: { id_format: { type: "integer" } }, output: { id_format: { type: "integer" } } } } as never;
    const h = host({ a: { type: "string" } }, zones);
    fireEvent.change(under("Type"), { target: { value: "child_id" } });
    expect(JSON.stringify(h.params().a)).toBe('{"type":"child_id","child_type":"zone"}');
    h.unmount();
    const k = host({ a: { type: "string", child_type: "output" } }, zones);
    fireEvent.change(under("Type"), { target: { value: "child_id" } });
    expect(JSON.stringify(k.params().a)).toBe('{"type":"child_id","child_type":"output"}');
    k.unmount();
    const none = host({ a: { type: "string" } });
    fireEvent.change(under("Type"), { target: { value: "child_id" } });
    expect(JSON.stringify(none.params().a)).toBe('{"type":"child_id"}');
  });
});

describe("the value boxes", () => {
  it("are help and default on a string, with min, max and unit on an integer and decimals on a number", () => {
    const s = host({ a: { type: "string" } });
    expect(valueGrid(document.body)).toBe("1fr 1fr");
    expect(captions(document.body)).not.toContain("Min");
    expect(captions(document.body)).not.toContain("Unit");
    s.unmount();
    const i = host({ a: { type: "integer" } });
    expect(valueGrid(document.body)).toBe("1fr 1fr 1fr 1fr 1fr");
    expect(captions(document.body).filter((c) => ["Help Text", "Default", "Min", "Max", "Decimals", "Unit"].includes(c!))).toEqual(
      ["Help Text", "Default", "Min", "Max", "Unit"],
    );
    expect(screen.getByPlaceholderText("scale (1)")).toBeTruthy();
    i.unmount();
    const n = host({ a: { type: "number" } });
    expect(valueGrid(document.body)).toBe("1fr 1fr 1fr 1fr 1fr 1fr");
    expect(captions(document.body)).toContain("Decimals");
    n.unmount();
    const b = host({ a: { type: "boolean", unit: "x" } });
    expect(valueGrid(document.body)).toBe("1fr 1fr 1fr");
    expect(captions(document.body)).toContain("Unit");
    expect(screen.queryByPlaceholderText("scale (1)")).toBeNull();
  });

  it("writes help, dropping a legacy description, and shows the description when there is no help", () => {
    const h = host({ a: { type: "string", description: "Old words" } as never });
    expect(under("Help Text").value).toBe("Old words");
    type(under("Help Text"), "New words");
    expect(JSON.stringify(h.params().a)).toBe('{"type":"string","help":"New words"}');
    type(under("Help Text"), "");
    expect(h.params().a).toEqual({ type: "string" });
  });

  it("reads a default as the parameter's type", () => {
    const cases: [string, string, unknown][] = [
      ["integer", "12abc", 12],
      ["integer", "abc", "abc"],
      ["integer", "1.5", 1],
      ["number", "1.5x", 1.5],
      ["number", "x", "x"],
      ["boolean", "true", true],
      ["boolean", "yes", false],
      ["string", "007", "007"],
      ["enum", "5", "5"],
    ];
    for (const [t, raw, want] of cases) {
      const h = host({ a: { type: t } });
      type(under("Default"), raw);
      expect([t, raw, h.params().a.default]).toEqual([t, raw, want]);
      type(under("Default"), "");
      expect("default" in h.params().a).toBe(false);
      h.unmount();
    }
  });

  it("says what a default takes", () => {
    const placeholders = ["boolean", "enum", "string"].map((t) => {
      const h = host({ a: { type: t, default: t === "boolean" ? false : null } as never });
      const p = under("Default").placeholder;
      const v = under("Default").value;
      h.unmount();
      return `${t}:${p}:${v}`;
    });
    expect(placeholders).toEqual(["boolean:true / false:false", "enum:must be one of values:", "string::"]);
  });

  it("reads min and max as the type's numbers, and decimals as a whole number", () => {
    const i = host({ a: { type: "integer" } });
    type(under("Min"), "2.7");
    type(under("Max"), "10");
    expect(JSON.stringify(i.params().a)).toBe('{"type":"integer","min":2,"max":10}');
    type(under("Min"), "");
    expect(JSON.stringify(i.params().a)).toBe('{"type":"integer","max":10}');
    i.unmount();
    const n = host({ a: { type: "number" } });
    type(under("Min"), "-0.5");
    type(under("Max"), "2.5");
    type(under("Decimals"), "2.9");
    expect(JSON.stringify(n.params().a)).toBe('{"type":"number","min":-0.5,"max":2.5,"decimals":2}');
    type(under("Decimals"), "");
    type(under("Max"), "");
    expect(JSON.stringify(n.params().a)).toBe('{"type":"number","min":-0.5}');
  });

  it("writes a unit and removes a cleared one", () => {
    const h = host({ a: { type: "integer" } });
    type(screen.getByTestId("param-unit-a"), "ms");
    expect(h.params().a).toEqual({ type: "integer", unit: "ms" });
    type(screen.getByTestId("param-unit-a"), "");
    expect(h.params().a).toEqual({ type: "integer" });
  });

  it("writes scale and offset on a numeric parameter", () => {
    const h = host({ a: { type: "integer" } });
    type(screen.getByPlaceholderText("scale (1)"), "0.5");
    type(screen.getByPlaceholderText("offset (0)"), "-10");
    expect(JSON.stringify(h.params().a)).toBe('{"type":"integer","scale":0.5,"offset":-10}');
  });
});

describe("the free-text aids", () => {
  it("show on a string, or on any type already carrying one", () => {
    const shown = (p: Partial<DriverParamDef>) => {
      const h = host({ a: p });
      const there = screen.queryByTestId("param-pattern-a") !== null;
      h.unmount();
      return there;
    };
    expect(shown({ type: "string" })).toBe(true);
    expect(shown({ type: "integer" })).toBe(false);
    expect(shown({ type: "integer", pattern: "" })).toBe(true);
    expect(shown({ type: "enum", trim: true })).toBe(true);
    expect(shown({ type: "boolean", secret: false })).toBe(true);
  });

  it("write a pattern, Trim spaces off and on, and Masked on and off", () => {
    const h = host({ a: { type: "string" } });
    type(screen.getByTestId("param-pattern-a"), "\\d+");
    expect(h.params().a).toEqual({ type: "string", pattern: "\\d+" });
    type(screen.getByTestId("param-pattern-a"), "");
    expect(h.params().a).toEqual({ type: "string" });
    const trim = () => screen.getByTestId("param-trim-a") as HTMLInputElement;
    expect(trim().checked).toBe(true);
    fireEvent.click(trim());
    expect(h.params().a).toEqual({ type: "string", trim: false });
    expect(trim().checked).toBe(false);
    fireEvent.click(trim());
    expect(h.params().a).toEqual({ type: "string" });
    const masked = () => screen.getByTestId("param-secret-a") as HTMLInputElement;
    expect(masked().checked).toBe(false);
    fireEvent.click(masked());
    expect(h.params().a).toEqual({ type: "string", secret: true });
    fireEvent.click(masked());
    expect(h.params().a).toEqual({ type: "string" });
  });

  it("keep the Trim spaces and Masked titles on one line, with their tooltips", () => {
    host({ a: { type: "string" } });
    const label = (testId: string) => screen.getByTestId(testId).parentElement as HTMLLabelElement;
    expect(label("param-trim-a").style.whiteSpace).toBe("nowrap");
    expect(label("param-trim-a").title).toMatch(/^On by default: surrounding spaces are stripped/);
    expect(label("param-secret-a").style.whiteSpace).toBe("nowrap");
    expect(label("param-secret-a").title).toMatch(/^Mask the input when the command is run/);
    const required = screen.getByText("Required") as HTMLLabelElement;
    expect(required.style.whiteSpace).toBe("");
    expect(required.hasAttribute("title")).toBe(false);
  });

  it("read a trim of true as on and a secret of anything but true as off", () => {
    host({ a: { type: "string", trim: true, secret: "yes" } as never });
    expect((screen.getByTestId("param-trim-a") as HTMLInputElement).checked).toBe(true);
    expect((screen.getByTestId("param-secret-a") as HTMLInputElement).checked).toBe(false);
  });
});

describe("an enum's values and a Child ID's type", () => {
  it("edits an enum's values", () => {
    const h = host({ a: { type: "enum", values: ["x"] } as never });
    expect(captions(document.body)).toContain("Allowed Values");
    type(screen.getByDisplayValue("x"), "y");
    expect(h.params().a).toEqual({ type: "enum", values: ["y"] });
  });

  it("says to declare a child type first when there is none", () => {
    host({ a: { type: "child_id" } });
    expect(screen.getByText(/No child entity types declared yet/)).toBeTruthy();
    expect(screen.getByText(/registered/).textContent).toContain("registered children for");
  });

  it("picks a child type, and an empty pick removes it", () => {
    const h = host(
      { a: { type: "child_id", child_type: "zone" } },
      { child_entity_types: { zone: { id_format: { type: "integer" } }, output: { id_format: { type: "integer" } } } } as never,
    );
    const sel = under("Child Type");
    expect(Array.from(sel.querySelectorAll("option")).map((o) => o.value)).toEqual(["", "zone", "output"]);
    expect(sel.value).toBe("zone");
    expect(screen.getByText(/registered/).querySelector("code")!.textContent).toBe("zone");
    fireEvent.change(sel, { target: { value: "output" } });
    expect(h.params().a).toEqual({ type: "child_id", child_type: "output" });
    expect(under("Child Type").value).toBe("output");
    fireEvent.change(under("Child Type"), { target: { value: "" } });
    expect(h.params().a).toEqual({ type: "child_id" });
    expect(under("Child Type").value).toBe("");
  });

  it("offers a wire value map", () => {
    const h = host({ a: { type: "integer" } });
    fireEvent.click(screen.getByText("+ Wire value map"));
    expect(h.params().a).toEqual({ type: "integer", map: { "": "" } });
  });
});

describe("where a parameter's choices come from", () => {
  const source = (n = "a") => screen.getByTestId(`param-options-source-${n}`) as HTMLSelectElement;
  const vars = { state_variables: { input_list: { type: "string" }, other_list: { type: "string" } } } as never;

  it("reads the source from what the parameter declares", () => {
    const h = host({
      a: { type: "string" },
      b: { type: "string", options_state: "" },
      c: { type: "string", options_state: "x", options_from: { param: "", source: "child_schema" } },
    } as never);
    expect([source("a").value, source("b").value, source("c").value]).toEqual(["fixed", "state", "cascade"]);
    expect(Array.from(source("a").options).map((o) => `${o.value}=${o.textContent}`)).toEqual([
      "fixed=Whatever the type allows",
      "state=A list the device publishes",
      "cascade=The controls of a child picked in another parameter",
    ]);
    h.unmount();
  });

  it("switches to a published list on the existing key, else the first state variable", () => {
    const h = host({ a: { type: "string", options_from: { param: "z", source: "child_schema" } } } as never, vars);
    fireEvent.change(source(), { target: { value: "state" } });
    expect(JSON.stringify(h.params().a)).toBe('{"type":"string","options_state":"input_list"}');
    const box = screen.getByTestId("param-options-state-a") as HTMLInputElement;
    expect(box.getAttribute("list")).toBe("param-options-state-list-a");
    expect(Array.from(document.querySelectorAll("#param-options-state-list-a option")).map((o) => o.getAttribute("value"))).toEqual(
      ["input_list", "other_list"],
    );
    type(box, "");
    expect(JSON.stringify(h.params().a)).toBe('{"type":"string","options_state":""}');
    fireEvent.change(source(), { target: { value: "fixed" } });
    expect(JSON.stringify(h.params().a)).toBe('{"type":"string"}');
    fireEvent.change(source(), { target: { value: "state" } });
    expect(h.params().a.options_state).toBe("input_list");
    h.unmount();
    const none = host({ a: { type: "string" } });
    fireEvent.change(source(), { target: { value: "state" } });
    expect(JSON.stringify(none.params().a)).toBe('{"type":"string","options_state":""}');
    none.unmount();
    const kept = host({ a: { type: "string", options_state: "mine" } } as never, vars);
    fireEvent.change(source(), { target: { value: "state" } });
    expect(kept.params().a.options_state).toBe("mine");
  });

  it("switches to a cascade on the first Child ID sibling, or says to add one", () => {
    const h = host({
      a: { type: "string", options_state: "x" },
      z1: { type: "child_id", child_type: "zone" },
      z2: { type: "child_id", child_type: "zone" },
    } as never);
    fireEvent.change(source(), { target: { value: "cascade" } });
    expect(JSON.stringify(h.params().a)).toBe('{"type":"string","options_from":{"param":"z1","source":"child_schema"}}');
    const pick = screen.getByTestId("param-options-from-a") as HTMLSelectElement;
    expect(Array.from(pick.querySelectorAll("option")).map((o) => o.value)).toEqual(["", "z1", "z2"]);
    expect(pick.value).toBe("z1");
    fireEvent.change(pick, { target: { value: "z2" } });
    expect((screen.getByTestId("param-options-from-a") as HTMLSelectElement).value).toBe("z2");
    expect(JSON.stringify(h.params().a.options_from)).toBe('{"param":"z2","source":"child_schema"}');
    expect(screen.getByText("Only Offer Controls Of Type")).toBeTruthy();
    h.unmount();
    const lone = host({ a: { type: "string" } });
    fireEvent.change(source(), { target: { value: "cascade" } });
    expect(JSON.stringify(lone.params().a)).toBe('{"type":"string","options_from":{"param":"","source":"child_schema"}}');
    expect(screen.getByText(/has no Child ID parameter to cascade from/)).toBeTruthy();
    expect(screen.queryByTestId("param-options-from-a")).toBeNull();
  });

  it("drops a cascade when switched back to whatever the type allows", () => {
    const h = host({
      a: { type: "string", options_from: { param: "z", source: "child_schema", units: ["dB"] } },
      z: { type: "child_id" },
    } as never);
    fireEvent.change(source(), { target: { value: "fixed" } });
    expect(JSON.stringify(h.params().a)).toBe('{"type":"string"}');
    expect(source().value).toBe("fixed");
  });

  it("never offers a Child ID parameter as its own cascade", () => {
    const h = host({ z1: { type: "child_id" }, z2: { type: "child_id" } } as never);
    fireEvent.change(source("z1"), { target: { value: "cascade" } });
    expect(JSON.stringify(h.params().z1.options_from)).toBe('{"param":"z2","source":"child_schema"}');
    const pick = screen.getByTestId("param-options-from-z1") as HTMLSelectElement;
    expect(Array.from(pick.querySelectorAll("option")).map((o) => o.value)).toEqual(["", "z2"]);
  });

  it("keeps a cascade's param and its narrowing when re-chosen, and puts source after param", () => {
    const h = host({
      a: { type: "string", options_from: { source: "child_schema", param: "z", types: ["boolean"] } },
      z: { type: "child_id" },
    } as never);
    fireEvent.change(source(), { target: { value: "cascade" } });
    expect(JSON.stringify(h.params().a.options_from)).toBe('{"param":"z","source":"child_schema"}');
    h.unmount();
    const k = host({
      a: { type: "string", options_from: { source: "child_schema", param: "z", units: ["dB"] } },
      z: { type: "child_id" },
      y: { type: "child_id" },
    } as never);
    fireEvent.change(screen.getByTestId("param-options-from-a"), { target: { value: "y" } });
    expect(JSON.stringify(k.params().a.options_from)).toBe('{"source":"child_schema","param":"y","units":["dB"]}');
  });

  it("shows a cascade with no param picked yet as unpicked", () => {
    host({ a: { type: "string", options_from: { source: "child_schema" } }, z: { type: "child_id" } } as never);
    expect((screen.getByTestId("param-options-from-a") as HTMLSelectElement).value).toBe("");
  });

  it("offers Input Type Follows only beside a cascade, or when one is set, marking one that is not", () => {
    const h = host({ a: { type: "string" }, b: { type: "string" } });
    expect(screen.queryByTestId("param-type-from-a")).toBeNull();
    h.unmount();
    const c = host({
      value: { type: "string" },
      control: { type: "string", options_from: { param: "zone", source: "child_schema" } },
      zone: { type: "child_id" },
    } as never);
    const follows = () => screen.getByTestId("param-type-from-value") as HTMLSelectElement;
    expect(Array.from(follows().querySelectorAll("option")).map((o) => o.value)).toEqual(["", "control"]);
    expect(screen.queryByTestId("param-type-from-control")).toBeNull();
    expect(follows().value).toBe("");
    fireEvent.change(follows(), { target: { value: "control" } });
    expect(c.params().value).toEqual({ type: "string", type_from: { param: "control" } });
    expect(follows().value).toBe("control");
    fireEvent.change(follows(), { target: { value: "" } });
    expect(c.params().value).toEqual({ type: "string" });
    c.unmount();
    const odd = host({ a: { type: "string", type_from: { param: "b" } }, b: { type: "string" } } as never);
    const opts = Array.from((screen.getByTestId("param-type-from-a") as HTMLSelectElement).querySelectorAll("option"));
    expect(opts.map((o) => `${o.value}=${o.textContent}`)).toEqual(["=Nothing (use the type above)", "b=b (not a cascade)"]);
    expect((screen.getByTestId("param-type-from-a") as HTMLSelectElement).value).toBe("b");
  });
});

describe("narrowing a cascade", () => {
  const cascade = (optionsFrom: Record<string, unknown>) => ({
    a: { type: "string", options_from: { param: "z", source: "child_schema", ...optionsFrom } },
    z: { type: "child_id" },
  }) as never;

  it("ticks and unticks types, removing an emptied list", () => {
    const h = host(cascade({ types: ["number"] }));
    fireEvent.click(screen.getByTestId("param-options-from-type-a-boolean"));
    expect(h.params().a.options_from!.types).toEqual(["number", "boolean"]);
    fireEvent.click(screen.getByTestId("param-options-from-type-a-number"));
    fireEvent.click(screen.getByTestId("param-options-from-type-a-boolean"));
    expect(JSON.stringify(h.params().a.options_from)).toBe('{"param":"z","source":"child_schema"}');
    expect(
      ["boolean", "number", "integer", "float", "enum", "string"].map(
        (t) => screen.getByTestId(`param-options-from-type-a-${t}`).parentElement!.textContent,
      ),
    ).toEqual(["Boolean", "Number", "Integer", "Float", "Enum", "String"]);
  });

  it("keeps a unit list as typed while writing the parsed one", () => {
    const h = host(cascade({ units: ["dB"] }));
    const units = () => screen.getByTestId("param-options-from-units-a") as HTMLInputElement;
    expect(units().value).toBe("dB");
    type(units(), "dB, ");
    expect(units().value).toBe("dB, ");
    expect(document.activeElement).toBe(units());
    expect(h.params().a.options_from!.units).toEqual(["dB"]);
    type(units(), "dB, %");
    expect(h.params().a.options_from!.units).toEqual(["dB", "%"]);
    type(units(), " , ");
    expect("units" in h.params().a.options_from!).toBe(false);
    expect(units().value).toBe(" , ");
  });
});
