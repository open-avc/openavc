import type { MacroStep, MacroConfig, DeviceConfig, DeviceGroup, TriggerConfig } from "../../api/types";
import { quotedValueText } from "../shared/booleanWords";
import type { StepNames } from "./commandLabels";

export interface StepTypeInfo {
  action: string;
  label: string;
  description: string;
  color: string;
  /** One line for a collapsed step. `macros` is here for the same reason
   *  `devices` is: a step that names another object by id has to be able to
   *  print what that object is called. The trigger table next door has taken
   *  all three since it was written. `names` carries the rest the editor has
   *  fetched: a device's command labels, as its Command list shows them, and
   *  the project's device groups. */
  summary: (
    step: MacroStep,
    devices: DeviceConfig[],
    macros: MacroConfig[],
    names?: StepNames,
  ) => string;
  defaults: () => Partial<MacroStep>;
}

/**
 * What to call a macro on screen.
 *
 * A macro's id is generated (`macro_1788707883504_3`) and is shown nowhere
 * else in the IDE, so a summary that prints one tells the reader nothing about
 * which macro is meant, and a collapsed step has to be expanded to find out.
 *
 * A callee that is not in the list keeps its id and is marked: a step pointing
 * at a macro that no longer exists is a defect, and a friendly name in its
 * place would hide it. No list at all is not evidence of that, so it prints
 * the id plain.
 */
export function macroLabel(macroId: unknown, macros: MacroConfig[] | undefined): string {
  return namedLabel(macroId, macros);
}

/** What to call anything a step names by a generated id (a macro, a device
 *  group): its name, by the rule `macroLabel` describes. */
function namedLabel(objectId: unknown, objects: { id: string; name: string }[] | undefined): string {
  const id = typeof objectId === "string" ? objectId.trim() : "";
  if (!id) return "?";
  if (!objects) return id;
  const found = objects.find((o) => o.id === id);
  if (!found) return `${id} (missing)`;
  return found.name || id;
}

/**
 * A Group Command step's one line: "All Displays → Power On".
 *
 * The group by its name, marked like a missing macro when the project no
 * longer has it. The command by the label the first member that has one
 * gives it, which is where the step's Command list takes its labels from;
 * with none fetched, the command's id.
 */
function groupCommandSummary(step: MacroStep, names: StepNames | undefined): string {
  const members = names?.groups?.find((g) => g.id === step.group?.trim())?.device_ids ?? [];
  const label = members.map((d) => names?.commandLabel?.(d, step.command)).find(Boolean);
  return `${namedLabel(step.group, names?.groups)} → ${label ?? step.command ?? "?"}`;
}

export const STEP_TYPES: StepTypeInfo[] = [
  {
    action: "device.command",
    label: "Device Command",
    description: "Send a command to a device (power on, switch input, etc.)",
    color: "#3b82f6",
    summary: (step, devices, _macros, names) => {
      const dev = devices.find((d) => d.id === step.device);
      const name = dev ? dev.name : step.device ?? "?";
      const command = names?.commandLabel?.(step.device, step.command) ?? step.command;
      return `${name} → ${command ?? "?"}`;
    },
    defaults: () => ({ action: "device.command", device: "", command: "" }),
  },
  {
    action: "group.command",
    label: "Group Command",
    description: "Send a command to all devices in a group at once",
    color: "#0ea5e9",
    summary: (step, _devices, _macros, names) => groupCommandSummary(step, names),
    defaults: () => ({ action: "group.command", group: "", command: "" }),
  },
  {
    action: "delay",
    label: "Delay",
    description: "Wait a number of seconds before the next step",
    color: "#8b5cf6",
    summary: (step) => `${step.seconds ?? 0}s`,
    defaults: () => ({ action: "delay", seconds: 1 }),
  },
  {
    action: "state.set",
    label: "Set Variable or Control",
    description: "Set a project variable, or change a control's label, color or visibility",
    color: "#10b981",
    summary: (step) =>
      step.value === null && (step.key ?? "").startsWith("ui.")
        ? `${step.key} cleared`
        : `${step.key ?? "?"} = ${quotedValueText(step.value ?? "")}`,
    defaults: () => ({ action: "state.set", key: "", value: "" }),
  },
  {
    action: "event.emit",
    label: "Emit Event",
    description: "Fire a named event that scripts can listen for",
    color: "#f59e0b",
    summary: (step) => step.event ?? "?",
    defaults: () => ({ action: "event.emit", event: "" }),
  },
  {
    action: "macro",
    label: "Run Macro",
    description: "Execute another macro as a sub-routine",
    color: "#ec4899",
    summary: (step, _devices, macros) => macroLabel(step.macro, macros),
    defaults: () => ({ action: "macro", macro: "" }),
  },
  {
    action: "conditional",
    label: "Conditional",
    description: "Run steps only if a condition is true (if/else branching)",
    color: "#f97316",
    summary: (step) => {
      const cond = step.condition;
      if (!cond) return "No condition set";
      const op = cond.operator ?? "eq";
      const val = cond.value != null ? quotedValueText(cond.value) : "?";
      if (op === "truthy") return `${cond.key} is truthy`;
      if (op === "falsy") return `${cond.key} is falsy`;
      return `${cond.key} ${op} ${val}`;
    },
    defaults: () => ({
      action: "conditional",
      condition: { key: "", operator: "eq", value: "" },
      then_steps: [],
      else_steps: [],
    }),
  },
  {
    action: "ui.navigate",
    label: "Navigate Panel",
    description: "Send every panel to a specific page or overlay",
    color: "#0d9488",
    summary: (step) => {
      const page = step.page ?? "?";
      if (page === "$back") return "Back";
      if (page === "$dismiss") return "Dismiss overlay";
      return `Go to ${page}`;
    },
    defaults: () => ({ action: "ui.navigate", page: "" }),
  },
  {
    action: "help.request",
    label: "Ask for Help",
    description: "Raise a help request to whoever supports this space",
    color: "#e11d48",
    summary: (step) => step.message?.trim() || "Someone asked for help",
    defaults: () => ({ action: "help.request", message: "" }),
  },
  {
    action: "help.clear",
    label: "Clear Help Request",
    description: "Put the help state back to idle, for the end of a session",
    color: "#9f1239",
    summary: () => "Back to idle",
    defaults: () => ({ action: "help.clear" }),
  },
  {
    action: "wait_until",
    label: "Wait Until",
    description: "Pause until a state value matches a condition (with optional timeout)",
    color: "#14b8a6",
    summary: (step) => {
      const cond = step.condition;
      if (!cond?.key) return "No condition set";
      const op = cond.operator ?? "eq";
      const val = cond.value != null ? quotedValueText(cond.value) : "?";
      const condStr =
        op === "truthy"
          ? `${cond.key} is truthy`
          : op === "falsy"
          ? `${cond.key} is falsy`
          : `${cond.key} ${op} ${val}`;
      const tmo = step.timeout == null ? "no timeout" : `${step.timeout}s`;
      return `${condStr} (${tmo})`;
    },
    defaults: () => ({
      action: "wait_until",
      condition: { key: "", operator: "eq", value: "" },
      timeout: 30,
      on_timeout: "fail",
    }),
  },
];

export function getStepType(action: string): StepTypeInfo | undefined {
  return STEP_TYPES.find((t) => t.action === action);
}

/** Recurse into then/else branches to find any wait_until step that has a numeric timeout. */
function _macroUsesWaitUntilWithTimeout(steps: MacroStep[]): boolean {
  for (const step of steps) {
    if (step.action === "wait_until" && step.timeout != null) return true;
    if (step.action === "conditional") {
      if (_macroUsesWaitUntilWithTimeout((step as any).then_steps ?? [])) return true;
      if (_macroUsesWaitUntilWithTimeout((step as any).else_steps ?? [])) return true;
    }
  }
  return false;
}

/** Whether a device's command works while the device is offline (the driver
 * declares it available_offline, like a Wake-on-LAN Power On). */
export type WorksOffline = (deviceId: string, command: string) => boolean;

/** Every device a macro's command steps send to, its groups' members included. */
function _commandDeviceIds(steps: MacroStep[], groups: DeviceGroup[] | undefined, into: Set<string>): Set<string> {
  for (const step of steps) {
    if (step.action === "device.command" && step.device) into.add(step.device);
    if (step.action === "group.command") {
      for (const id of groups?.find((g) => g.id === step.group)?.device_ids ?? []) into.add(id);
    }
    if (step.action === "conditional") {
      _commandDeviceIds((step as any).then_steps ?? [], groups, into);
      _commandDeviceIds((step as any).else_steps ?? [], groups, into);
    }
  }
  return into;
}

/** Reads which of a macro's commands work while their device is offline, from
 * each device's driver. A device that cannot be read counts as having none. */
export async function loadWorksOffline(
  macro: MacroConfig,
  groups: DeviceGroup[] | undefined,
  getDevice: (id: string) => Promise<{ commands: Record<string, unknown> }>,
): Promise<WorksOffline> {
  const offline = new Map<string, Set<string>>();
  const ids = [..._commandDeviceIds(macro.steps, groups, new Set())];
  const infos = await Promise.all(ids.map((id) => getDevice(id).catch(() => null)));
  ids.forEach((id, i) => {
    const commands = infos[i]?.commands ?? {};
    const names = Object.entries(commands)
      .filter(([, def]) => !!(def as { available_offline?: boolean } | null)?.available_offline)
      .map(([name]) => name);
    if (names.length) offline.set(id, new Set(names));
  });
  return (deviceId, command) => offline.get(deviceId)?.has(command) ?? false;
}

export function macroToScript(
  macro: MacroConfig,
  groups?: DeviceGroup[],
  worksOffline?: WorksOffline,
): string {
  const triggers = macro.triggers ?? [];
  const hasStateChange = triggers.some((t) => t.type === "state_change" && t.enabled);
  const hasEvent = triggers.some((t) => t.type === "event" && t.enabled);
  const hasSchedule = triggers.some((t) => t.type === "schedule" && t.enabled);

  // Generate the body first; the import list depends on what it uses.
  const body: string[] = [];
  const writeSteps: StepWriter = (into) =>
    _generateStepLines(into, macro.steps, "    ", groups, worksOffline);

  // Generate decorator-based handlers for triggers
  for (const trigger of triggers) {
    if (trigger.enabled) _triggerHandler(body, trigger, macro, writeSteps);
  }

  // Always include a manual run() function
  body.push("");
  body.push("");
  body.push("async def run():");
  const runStart = body.length;
  if (macro.steps.length === 0) {
    body.push("    pass");
  } else {
    writeSteps(body);
  }
  _holdContext(body, runStart, RUN_CONTEXT.run);

  // Build import list based on what the script needs
  const bodyText = body.join("\n");
  const imports = ["devices", "state", "events", "macros", "log"];
  if (/\bcompare\(/.test(bodyText)) imports.push("compare");
  if (hasStateChange) imports.push("on_state_change");
  if (hasEvent) imports.push("on_event");
  // Startup triggers use on_event("system.started"), so add on_event if needed
  if (triggers.some((t) => t.type === "startup" && t.enabled) && !hasEvent) {
    imports.push("on_event");
  }
  // A schedule trigger's commented-out handler uses on_event once uncommented
  if (hasSchedule && !imports.includes("on_event")) {
    imports.push("on_event");
  }

  const needsTimeImport = _macroUsesWaitUntilWithTimeout(macro.steps);
  const lines: string[] = [
    `"""Auto-generated from macro '${_pyEscape(macro.name)}'."""`,
    `from openavc import ${imports.join(", ")}`,
    "import asyncio",
    ...(needsTimeImport ? ["import time as _t"] : []),
    "",
    ...body,
  ];

  return lines.join("\n") + "\n";
}

/** Writes the macro's steps into a function body, one level in. */
type StepWriter = (into: string[]) => void;

/** The namespace a condition key or a `$` reference reads the run's firing
 * context through (`value_resolver.TRIGGER_PREFIX` on the server):
 * `trigger.new_value` is the context's `new_value`. */
const TRIGGER_PREFIX = "trigger.";

/** What a run hands its steps, held as `trigger` in the function that runs
 * them, built as the trigger engine builds it for that trigger
 * (`core/trigger_engine.py`): the change; the event's name with its payload
 * spread over it; the startup. A run nothing fired (`run()`) has none.
 * tests/test_generated_scripts_load.py holds each to what the macro reads
 * when its trigger fires. */
const RUN_CONTEXT = {
  state_change: '{"key": key, "old_value": old_value, "new_value": new_value}',
  event: '{"event": event.name, **event.payload}',
  startup: '{"trigger_type": "startup"}',
  run: "{}  # nothing fired this run",
} as const;

/** One enabled trigger's handler, or the comment a schedule becomes. */
function _triggerHandler(
  body: string[],
  trigger: TriggerConfig,
  macro: MacroConfig,
  writeSteps: StepWriter,
): void {
  body.push("");
  if (trigger.type === "state_change" && trigger.state_key) {
    _stateChangeHandler(body, trigger, writeSteps);
  } else if (trigger.type === "event" && trigger.event_pattern) {
    body.push(`@on_event("${_pyEscape(trigger.event_pattern)}")`);
    body.push("async def on_trigger(event):");
    const start = body.length;
    _pushGuards(body, trigger);
    writeSteps(body);
    _holdContext(body, start, RUN_CONTEXT.event);
  } else if (trigger.type === "schedule" && trigger.cron) {
    // A schedule trigger runs its macro directly and emits no event, so
    // there is nothing for a script handler to listen for.
    _scheduleComment(body, trigger, macro);
  } else if (trigger.type === "startup") {
    body.push("@on_event(\"system.started\")");
    body.push("async def on_startup(event):");
    const start = body.length;
    if ((trigger.delay_seconds ?? 0) > 0) {
      body.push(`    await asyncio.sleep(${trigger.delay_seconds})`);
    }
    writeSteps(body);
    _holdContext(body, start, RUN_CONTEXT.startup);
  }
}

/** A state change trigger's handler: its operator's check (compare() mirrors
 * the trigger engine's coercion), its guards, its delay and re-check, then
 * the steps. */
function _stateChangeHandler(body: string[], trigger: TriggerConfig, writeSteps: StepWriter): void {
  body.push(`@on_state_change("${_pyEscape(trigger.state_key ?? "")}")`);
  body.push("async def on_trigger(key, old_value, new_value):");
  const start = body.length;
  const op = trigger.state_operator;
  const checksValue = Boolean(op) && op !== "any";
  if (checksValue) {
    body.push(_newValueGuard(op ?? "", trigger.state_value));
    body.push("        return");
  }
  _pushGuards(body, trigger);
  if ((trigger.delay_seconds ?? 0) > 0) {
    body.push(`    await asyncio.sleep(${trigger.delay_seconds})  # delay re-check`);
    if (checksValue) {
      // Re-check using same operator (inverted for guard return)
      body.push(_conditionToGuard({ key: trigger.state_key ?? "", operator: op, value: trigger.state_value }, "    "));
      body.push("        return");
    }
  }
  writeSteps(body);
  _holdContext(body, start, RUN_CONTEXT.state_change);
}

/** The line that ends a state change handler whose new value fails `op`. */
function _newValueGuard(op: string, value: unknown): string {
  if (op === "truthy") return "    if not new_value:";
  if (op === "falsy") return "    if new_value:";
  return `    if not compare(new_value, "${_pyEscape(op)}", ${_pyValue(value ?? "", false)}):`;
}

/** A trigger's guard conditions, each ending the handler when it fails. */
function _pushGuards(body: string[], trigger: TriggerConfig): void {
  for (const cond of trigger.conditions ?? []) {
    body.push(_conditionToGuard(cond, "    "));
    body.push("        return");
  }
}

/** `context` held as `trigger` at the top of the function body that starts
 * at `start`, when a line of it reads the run's firing context. Only
 * `_read` writes `trigger.get("`: a quote in a value is escaped, and a
 * comment line is passed over. */
function _holdContext(body: string[], start: number, context: string): void {
  if (_readsContext(body.slice(start))) body.splice(start, 0, `    trigger = ${context}`);
}

function _readsContext(lines: string[]): boolean {
  return lines.some((line) => !line.trimStart().startsWith("#") && line.includes('trigger.get("'));
}


/** What a step's condition key or `$` reference reads, where the macro engine
 * reads it (`value_resolver.read_operand` and `resolve_ref`): a
 * `trigger.<field>` key reads the run's firing context, any other key the
 * state. A Wait Until is judged with no firing context, so there a
 * `trigger.` key is a value that has not arrived (None). */
function _read(key: string, site: "step" | "wait_until" = "step"): string {
  if (!key.startsWith(TRIGGER_PREFIX)) return `state.get("${_pyEscape(key)}")`;
  if (site === "wait_until") return "None";
  return `trigger.get("${_pyEscape(key.slice(TRIGGER_PREFIX.length))}")`;
}

/** What a schedule trigger becomes: a comment, since scripts have no Schedule
 * trigger, with the handler to uncomment once the macro emits the event. */
function _scheduleComment(body: string[], trigger: TriggerConfig, macro: MacroConfig): void {
  const event = `custom.${macro.id}_schedule`;
  body.push(`# Schedule "${_pyComment(trigger.cron ?? "")}" is not converted: scripts have no Schedule trigger.`);
  body.push("# To run this script on it, keep the Schedule trigger on the macro, replace");
  body.push(`# the macro's steps with one Emit Event step that fires ${_pyComment(event)},`);
  body.push("# and uncomment:");
  body.push("#");
  // _pyEscape leaves no control character, so the line stays one comment line
  body.push(`# @on_event("${_pyEscape(event)}")`);
  body.push("# async def on_schedule(event):");
  body.push("#     await run()");
}

/** Escape a string for use inside a Python string literal (quotes, backslashes,
 * and control characters such as newlines, so a value can never break out of
 * the literal or out of a docstring). */
function _pyEscape(s: string): string {
  return s
    .replace(/\\/g, "\\\\")
    .replace(/"/g, '\\"')
    .replace(/'/g, "\\'")
    .replace(/[\x00-\x1f\x7f]/g, (c) => `\\x${c.charCodeAt(0).toString(16).padStart(2, "0")}`);
}

/** Sanitize free text for use inside a Python comment: control characters
 * (especially newlines, which would end the comment) collapse to a space. */
function _pyComment(s: string): string {
  return s.replace(/[\x00-\x1f\x7f]+/g, " ");
}

/** Render a JS value as a Python literal expression.
 *
 * When `resolveDollar` is true, a top-level string starting with `$` becomes a
 * read of what it references (`_read`: `$trigger.<field>` the run's firing
 * context, any other `$<key>` the state) — mirroring the macro engine, which
 * resolves `$` references in command params and state.set values at runtime
 * (top level only, so nested values recurse with resolution off). */
function _pyValue(v: unknown, resolveDollar: boolean): string {
  if (v === null || v === undefined) return "None";
  if (typeof v === "boolean") return v ? "True" : "False";
  if (typeof v === "number") return Number.isFinite(v) ? String(v) : "None";
  if (typeof v === "string") {
    if (resolveDollar && v.startsWith("$")) return _read(v.slice(1));
    return `"${_pyEscape(v)}"`;
  }
  if (Array.isArray(v)) {
    return `[${v.map((item) => _pyValue(item, false)).join(", ")}]`;
  }
  if (typeof v === "object") {
    const entries = Object.entries(v as Record<string, unknown>).map(
      ([k, item]) => `"${_pyEscape(k)}": ${_pyValue(item, false)}`
    );
    return `{${entries.join(", ")}}`;
  }
  return `"${_pyEscape(String(v))}"`;
}

/** Render a params dict as a Python dict literal, resolving top-level `$`
 * references (`_read`) like the macro engine's _resolve_params. */
function _pyParamsDict(params: Record<string, unknown>): string {
  const entries = Object.entries(params).map(
    ([k, v]) => `"${_pyEscape(k)}": ${_pyValue(v, true)}`
  );
  return `{${entries.join(", ")}}`;
}

/** Positive-sense condition expression (true when the condition matches), its
 * key read where the macro engine reads it at `site` (`_read`).
 * Comparison operators go through compare(), which applies the same alias
 * normalization and type coercion as the macro/trigger engines. */
function _conditionExpr(
  cond: { key?: string; operator?: string; value?: unknown },
  site: "step" | "wait_until" = "step",
): string {
  const actual = _read(cond.key ?? "", site);
  const op = cond.operator ?? "eq";
  if (op === "truthy") return actual;
  if (op === "falsy") return `not ${actual}`;
  return `compare(${actual}, "${_pyEscape(op)}", ${_pyValue(cond.value ?? "", false)})`;
}

/** Generate a guard condition (if ... return) line for a trigger condition/operator.
 * A guard reads the state only, `trigger.` keys included, as the trigger
 * engine's guard does (`TriggerEngine._check_conditions`). */
function _conditionToGuard(
  cond: { key?: string; operator?: string; value?: unknown },
  indent: string
): string {
  const key = _pyEscape(cond.key ?? "");
  const op = cond.operator ?? "eq";
  if (op === "truthy") return `${indent}if not state.get("${key}"):`;
  if (op === "falsy") return `${indent}if state.get("${key}"):`;
  return `${indent}if not compare(state.get("${key}"), "${_pyEscape(op)}", ${_pyValue(cond.value ?? "", false)}):`;
}

function _generateStepLines(
  lines: string[],
  steps: MacroStep[],
  indent: string,
  groups?: DeviceGroup[],
  worksOffline?: WorksOffline,
): void {
  const start = lines.length;
  for (const step of steps) {
    // skip_if guard — if the condition matches, skip the step (mirrors the
    // macro engine, including compare()'s operator aliases + type coercion)
    if (step.skip_if) {
      lines.push(`${indent}if ${_conditionExpr(step.skip_if)}:  # skip_if`);
      lines.push(`${indent}    pass  # skipped`);
      // Wrap the actual step in else
      lines.push(`${indent}else:`);
      // Re-enter with extra indent
      _generateStepLines(lines, [{ ...step, skip_if: undefined }], indent + "    ", groups, worksOffline);
      continue;
    }
    switch (step.action) {
      case "device.command": {
        const device = _pyEscape(step.device ?? "");
        const params = step.params ? `, ${_pyParamsDict(step.params)}` : "";
        const sendLine = `await devices.send("${device}", "${_pyEscape(step.command ?? "")}"${params})`;
        // A command that works while the device is offline is never skipped,
        // matching the macro engine: a Wake-on-LAN Power On is needed exactly then.
        if (step.skip_if_offline && !worksOffline?.(step.device ?? "", step.command ?? "")) {
          lines.push(`${indent}if state.get("device.${device}.connected"):  # skip_if_offline`);
          lines.push(`${indent}    ${sendLine}`);
        } else {
          lines.push(`${indent}${sendLine}`);
        }
        break;
      }
      case "group.command": {
        const params = step.params ? `, ${_pyParamsDict(step.params)}` : "";
        const group = groups?.find((g) => g.id === step.group);
        const deviceIds = group?.device_ids ?? [];
        lines.push(
          `${indent}# Group command: ${_pyComment(`${step.group ?? ""} -> ${step.command ?? ""}`)}`
        );
        lines.push(
          `${indent}for device_id in ${_pyValue(deviceIds, false)}:`
        );
        // Members whose command works while offline are sent it anyway, as the
        // macro engine does.
        const sendsOffline = deviceIds.filter((id) => worksOffline?.(id, step.command ?? ""));
        if (sendsOffline.length < deviceIds.length) {
          const exempt = sendsOffline.length
            ? ` and device_id not in ${_pyValue(sendsOffline, false)}`
            : "";
          lines.push(
            `${indent}    if not state.get(f"device.{device_id}.connected")${exempt}:`
          );
          lines.push(
            `${indent}        continue  # offline devices are skipped, matching the macro engine`
          );
        }
        lines.push(
          `${indent}    await devices.send(device_id, "${_pyEscape(step.command ?? "")}"${params})`
        );
        break;
      }
      case "delay":
        lines.push(`${indent}await asyncio.sleep(${step.seconds ?? 0})`);
        break;
      case "state.set":
        lines.push(
          `${indent}state.set("${_pyEscape(step.key ?? "")}", ${_pyValue(step.value, true)})`
        );
        break;
      case "event.emit": {
        // Payload values are passed through verbatim — the macro engine does
        // not resolve $-references in event payloads.
        const payload = step.payload ? `, ${_pyValue(step.payload, false)}` : "";
        lines.push(`${indent}await events.emit("${_pyEscape(step.event ?? "")}"${payload})`);
        break;
      }
      case "macro":
        lines.push(`${indent}await macros.execute("${_pyEscape(step.macro ?? "")}")`);
        break;
      case "ui.navigate":
        lines.push(
          `${indent}# Navigate panels to '${_pyComment(step.page ?? "")}'`
        );
        lines.push(
          `${indent}# (no script API for ui.navigate yet, call from a macro step instead)`
        );
        break;
      case "help.request":
        lines.push(
          `${indent}# Ask for help: '${_pyComment(step.message?.trim() || "Someone asked for help")}'`
        );
        lines.push(
          `${indent}# (no script API for Ask for Help, call from a macro step instead)`
        );
        break;
      case "help.clear":
        lines.push(`${indent}# Clear help request`);
        lines.push(
          `${indent}# (no script API for Clear Help Request, call from a macro step instead)`
        );
        break;
      case "conditional": {
        const cond = step.condition;
        if (cond) {
          lines.push(`${indent}if ${_conditionExpr(cond)}:`);
          const thenSteps = (step as any).then_steps ?? [];
          const elseSteps = (step as any).else_steps ?? [];
          _generateStepLines(lines, thenSteps, indent + "    ", groups, worksOffline);
          if (elseSteps.length > 0) {
            lines.push(`${indent}else:`);
            _generateStepLines(lines, elseSteps, indent + "    ", groups, worksOffline);
          }
        } else {
          lines.push(`${indent}# Conditional step with no condition set`);
          lines.push(`${indent}pass`);
        }
        break;
      }
      case "wait_until": {
        const cond = step.condition;
        if (!cond) {
          lines.push(`${indent}# wait_until step with no condition set`);
          lines.push(`${indent}pass`);
          break;
        }
        const key = _pyEscape(cond.key ?? "");
        const op = cond.operator ?? "eq";
        const checkExpr = _conditionExpr(cond, "wait_until");
        const timeout = step.timeout;
        const onTimeout = step.on_timeout ?? "fail";
        if (timeout == null) {
          // Never time out — poll until satisfied
          lines.push(`${indent}while not (${checkExpr}):`);
          lines.push(`${indent}    await asyncio.sleep(0.5)`);
        } else {
          const condDesc = _pyComment(
            `${cond.key ?? ""} ${op} ${JSON.stringify(cond.value ?? "")}`
          );
          lines.push(`${indent}# wait_until: ${condDesc} (timeout ${timeout}s, ${onTimeout})`);
          lines.push(`${indent}_deadline = _t.monotonic() + ${timeout}`);
          lines.push(`${indent}while not (${checkExpr}):`);
          lines.push(`${indent}    if _t.monotonic() >= _deadline:`);
          if (onTimeout === "fail") {
            lines.push(`${indent}        raise TimeoutError("wait_until: ${key} not satisfied after ${timeout}s")`);
          } else {
            lines.push(`${indent}        break  # on_timeout: continue`);
          }
          lines.push(`${indent}    await asyncio.sleep(0.5)`);
        }
        break;
      }
      default:
        if (step.action.includes(".")) {
          // Plugin-registered macro action — no script-side equivalent is
          // generated automatically. The plugin's script module (if any)
          // provides the call.
          const params = step.params ? JSON.stringify(step.params) : "{}";
          lines.push(
            `${indent}# Plugin action '${_pyComment(step.action)}', call the plugin's script API directly`
          );
          lines.push(`${indent}# Params: ${_pyComment(params)}`);
        } else {
          lines.push(`${indent}# Unsupported step type: ${_pyComment(step.action)}`);
        }
        break;
    }
  }
  if (_onlyComments(lines.slice(start))) lines.push(`${indent}pass`);
}

/** True when a block came out as comments only (no steps, or steps with no
 * script equivalent): Python refuses a block with no statement in it. */
function _onlyComments(block: string[]): boolean {
  return block.every((line) => line.trimStart().startsWith("#"));
}

/** Analyze a macro for potential script conversion issues. `macros`, the
 * project's, are what its Run Macro steps run. */
export function getConversionWarnings(
  macro: MacroConfig,
  groups?: DeviceGroup[],
  macros: MacroConfig[] = [],
): string[] {
  const warnings: string[] = [];
  const checkSteps = (steps: MacroStep[]) => {
    for (const step of steps) {
      if (step.action === "group.command") {
        const group = groups?.find((g) => g.id === step.group);
        if (!group) {
          warnings.push(`Group command references unknown group "${step.group}". The device list will be empty in the generated script.`);
        } else {
          warnings.push(`Group command "${step.group}" → "${step.command}" is converted to a loop over ${group.device_ids.length} device(s). If the group membership changes later, update the script manually.`);
        }
      }
      if (step.action === "wait_until" && step.timeout == null) {
        warnings.push(`"Wait Until" step with no timeout. The script will poll forever until the condition is met. Make sure something will eventually satisfy it.`);
      }
      if (step.then_steps) checkSteps(step.then_steps);
      if (step.else_steps) checkSteps(step.else_steps);
    }
  };
  checkSteps(macro.steps);

  const triggers = macro.triggers ?? [];
  if (triggers.some((t) => t.type === "schedule" && t.enabled)) {
    warnings.push("Schedule triggers are not converted: scripts have no Schedule trigger. To run the script on this schedule, keep the Schedule trigger on this macro and replace the macro's steps with one Emit Event step. The generated script shows the handler to add.");
  }
  warnings.push(..._runMacroWarnings(macro, macros));

  return warnings;
}

/** A warning for each macro a Run Macro step runs that reads what ran the
 * macro, itself or through a macro it runs: the macro engine hands it the
 * trigger's values, and a script's macros.execute hands it none. Only a
 * trigger's handler has values to lose. */
function _runMacroWarnings(macro: MacroConfig, macros: MacroConfig[]): string[] {
  const handlers = (macro.triggers ?? []).some(
    (t) => t.enabled && (t.type === "state_change" || t.type === "event" || t.type === "startup"),
  );
  if (!handlers) return [];
  return _macrosRun(macro.steps, macros, new Set([macro.id]))
    .filter((child) => _readsWhatRan(child.steps, macros, new Set([macro.id, child.id])))
    .map((child) =>
      `Run Macro "${child.name}" reads what ran the macro ($trigger values). The script runs it with macros.execute, which passes none, so it reads them as empty. To keep them, write that macro's steps into the script, where they read trigger.`,
    );
}

/** Whether `steps`, or a macro they run, read what ran the macro. */
function _readsWhatRan(steps: MacroStep[], macros: MacroConfig[], seen: Set<string>): boolean {
  if (_namesWhatRan(steps)) return true;
  return _macrosRun(steps, macros, seen).some((child) => _readsWhatRan(child.steps, macros, seen));
}

/** Whether `value` (steps, or anything in one) names what ran the macro: a
 * `$trigger.` reference in any text, or a condition on a `trigger.` key. A
 * macro run by Run Macro runs in the macro engine, which reads both in more
 * places than a script writes (a help request's message, a plugin action's
 * params), so every one counts. */
function _namesWhatRan(value: unknown): boolean {
  if (typeof value === "string") return value.includes("$" + TRIGGER_PREFIX);
  if (Array.isArray(value)) return value.some(_namesWhatRan);
  if (value === null || typeof value !== "object") return false;
  return Object.entries(value).some(
    ([field, v]) => (field === "key" && typeof v === "string" && v.startsWith(TRIGGER_PREFIX)) || _namesWhatRan(v),
  );
}

/** The macros `steps` run with Run Macro, their branches included, each once:
 * a macro already in `seen` is passed over, and each found is added to it. */
function _macrosRun(steps: MacroStep[], macros: MacroConfig[], seen: Set<string>): MacroConfig[] {
  const found: MacroConfig[] = [];
  for (const step of _everyStep(steps)) {
    const child = step.action === "macro" ? macros.find((m) => m.id === step.macro) : undefined;
    if (child && !seen.has(child.id)) {
      seen.add(child.id);
      found.push(child);
    }
  }
  return found;
}

/** `steps` and every step in their branches. */
function _everyStep(steps: MacroStep[]): MacroStep[] {
  return steps.flatMap((step) => [step, ..._everyStep(step.then_steps ?? []), ..._everyStep(step.else_steps ?? [])]);
}

/** What to do with the macro once Convert to Script has created its script,
 * or "" when nothing. The script runs as soon as it is created and the macro's
 * triggers still run the macro, so each enabled trigger would run the steps
 * twice. A Schedule trigger is the one to keep: the script's comment asks for it.
 * A Startup trigger becomes a `system.started` handler, and that event comes
 * once per server start, so the script's copy first runs at the next start. */
export function convertedMacroAdvice(macro: MacroConfig): string {
  const enabled = (macro.triggers ?? []).filter((t) => t.enabled);
  if (enabled.length === 0) return "";
  const advice = enabled.some((t) => t.type === "schedule")
    ? "The macro's triggers still run its steps. Disable all but its Schedule trigger, and replace its steps with the Emit Event step the script names."
    : "The macro's triggers still run its steps. Disable them or delete the macro, so the steps do not run twice.";
  if (!enabled.some((t) => t.type === "startup")) return advice;
  return `${advice} The script runs its Startup steps the next time the server starts.`;
}

let _nextId = 1;
export function generateId(prefix: string): string {
  return `${prefix}_${Date.now()}_${_nextId++}`;
}

// --- Step clipboard (cross-macro copy/paste) ---

let _clipboardStep: MacroStep | null = null;

export function copyStep(step: MacroStep): void {
  _clipboardStep = JSON.parse(JSON.stringify(step));
}

export function getClipboardStep(): MacroStep | null {
  return _clipboardStep ? JSON.parse(JSON.stringify(_clipboardStep)) : null;
}

export function hasClipboardStep(): boolean {
  return _clipboardStep !== null;
}

// --- Step templates (pre-built multi-step patterns) ---

export interface StepTemplate {
  id: string;
  label: string;
  description: string;
  steps: MacroStep[];
}

export const STEP_TEMPLATES: StepTemplate[] = [
  {
    id: "power_sequence",
    label: "Power Sequence",
    description: "Power on devices in order with delays between each",
    steps: [
      { action: "device.command", device: "", command: "power_on", description: "Power on first device" },
      { action: "delay", seconds: 3, description: "Wait for device to warm up" },
      { action: "device.command", device: "", command: "power_on", description: "Power on second device" },
      { action: "delay", seconds: 2, description: "Wait before switching input" },
      { action: "device.command", device: "", command: "", description: "Set input source" },
    ],
  },
  {
    id: "source_switch",
    label: "Source Switch",
    description: "Switch input source and update a room variable",
    steps: [
      { action: "device.command", device: "", command: "", description: "Switch input on display/switcher" },
      { action: "state.set", key: "var.current_source", value: "", description: "Track active source" },
      { action: "event.emit", event: "source.changed", description: "Notify other macros" },
    ],
  },
  {
    id: "volume_ramp",
    label: "Volume Ramp",
    description: "Gradually adjust volume in steps with short delays",
    steps: [
      { action: "device.command", device: "", command: "set_volume", params: { level: 20 }, description: "Set volume to 20%" },
      { action: "delay", seconds: 0.3 },
      { action: "device.command", device: "", command: "set_volume", params: { level: 40 }, description: "Set volume to 40%" },
      { action: "delay", seconds: 0.3 },
      { action: "device.command", device: "", command: "set_volume", params: { level: 60 }, description: "Set volume to 60%" },
    ],
  },
];

// --- Circular dependency detection ---

/** Collect all macro IDs referenced by steps (recursively into conditionals). */
function collectMacroRefs(steps: MacroStep[]): Set<string> {
  const refs = new Set<string>();
  for (const step of steps) {
    if (step.action === "macro" && step.macro) {
      refs.add(step.macro);
    }
    if (step.then_steps) collectMacroRefs(step.then_steps).forEach((r) => refs.add(r));
    if (step.else_steps) collectMacroRefs(step.else_steps).forEach((r) => refs.add(r));
  }
  return refs;
}

/** Build a dependency map: macro ID -> set of macro IDs it calls. */
export function buildDependencyMap(macros: MacroConfig[]): Map<string, Set<string>> {
  const map = new Map<string, Set<string>>();
  for (const m of macros) {
    map.set(m.id, collectMacroRefs(m.steps));
  }
  return map;
}

/** Find macros that directly call a given macro. */
export function getMacroCallers(macroId: string, macros: MacroConfig[]): MacroConfig[] {
  return macros.filter((m) => m.id !== macroId && collectMacroRefs(m.steps).has(macroId));
}

/** Find macros that a given macro directly calls. */
export function getMacroCallees(macroId: string, macros: MacroConfig[]): string[] {
  const macro = macros.find((m) => m.id === macroId);
  if (!macro) return [];
  return [...collectMacroRefs(macro.steps)];
}

/** Detect circular dependency starting from a given macro. Returns the cycle path or null. */
export function detectCircularDependency(
  macroId: string,
  macros: MacroConfig[]
): string[] | null {
  const depMap = buildDependencyMap(macros);

  function dfs(current: string, path: string[], visited: Set<string>): string[] | null {
    if (visited.has(current)) {
      const cycleStart = path.indexOf(current);
      return cycleStart >= 0 ? [...path.slice(cycleStart), current] : null;
    }
    visited.add(current);
    path.push(current);
    const refs = depMap.get(current);
    if (refs) {
      for (const ref of refs) {
        const cycle = dfs(ref, path, visited);
        if (cycle) return cycle;
      }
    }
    path.pop();
    visited.delete(current);
    return null;
  }

  return dfs(macroId, [], new Set());
}
