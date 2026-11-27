import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";

// The picker's trigger group and each trigger type's line list the fields the
// firing context's table (firingContext.gen.ts) lists, and no others.

import { MacroCanRead } from "./TriggerEditor";
import { FIRING_CONTEXTS } from "../../api/firingContext.gen";
import { triggerFieldRows, typedTriggerFieldRow } from "../shared/variableKeyPickerHelpers";

describe("What a trigger type's macro can read", () => {
  it("names every field its type gives", () => {
    const { container } = render(<MacroCanRead type="state_change" />);
    expect(container.textContent).toBe(
      "The macro can read $trigger.key, $trigger.old_value and $trigger.new_value.",
    );
  });

  it("says an event trigger also gives the event's own fields", () => {
    const { container } = render(<MacroCanRead type="event" />);
    expect(container.textContent).toBe(
      "The macro can read $trigger.event, and every field of the event as $trigger.<name>.",
    );
  });

  it("shows each field's description on hover", () => {
    const { container } = render(<MacroCanRead type="schedule" />);
    const cron = Array.from(container.querySelectorAll("code")).find((c) => c.textContent === "$trigger.cron");
    expect(cron?.getAttribute("title")).toBe("The trigger's schedule, as a cron expression");
  });

  it("says nothing for a type the table does not list", () => {
    const { container } = render(<MacroCanRead type="no_such_type" />);
    expect(container.textContent).toBe("");
  });
});

describe("The picker's trigger group", () => {
  it("lists every field in the table once, and no others", () => {
    const fields = new Set(
      Object.values(FIRING_CONTEXTS).flatMap((c) => c.fields.map((f) => `trigger.${f.name}`)),
    );
    const keys = triggerFieldRows().map((row) => row.key);
    expect(keys).toHaveLength(fields.size);
    expect(new Set(keys)).toEqual(fields);
  });

  it("says what gives each field", () => {
    const rows = Object.fromEntries(triggerFieldRows().map((row) => [row.key, row.description]));
    expect(rows["trigger.new_value"]).toBe("Its value after the change. From a state change trigger.");
    expect(rows["trigger.trigger_type"]).toBe(
      'The kind of trigger, "schedule" or "startup". From a schedule trigger or a startup trigger.',
    );
    expect(rows["trigger.tag"]).toBe(
      "The Tag you gave the control in the UI Builder. From a control that runs the macro.",
    );
  });

  it("offers a typed field of an event's own payload", () => {
    expect(typedTriggerFieldRow("trigger.source")).toEqual({
      key: "trigger.source",
      label: "source",
      description: "A field of the event. From an event trigger.",
    });
    expect(typedTriggerFieldRow(" $trigger.error ")?.key).toBe("trigger.error");
  });

  it("offers nothing for a search that names no trigger field", () => {
    expect(typedTriggerFieldRow("trigger.")).toBeNull();
    expect(typedTriggerFieldRow("var.level")).toBeNull();
    expect(typedTriggerFieldRow("trigger.a.b")).toBeNull();
  });
});
