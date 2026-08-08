import type { MacroStep, MacroConfig, DeviceConfig, DeviceGroup } from "../../api/types";

export interface StepTypeInfo {
  action: string;
  label: string;
  description: string;
  color: string;
  summary: (step: MacroStep, devices: DeviceConfig[]) => string;
  defaults: () => Partial<MacroStep>;
}

export const STEP_TYPES: StepTypeInfo[] = [
  {
    action: "device.command",
    label: "Device Command",
    description: "Send a command to a device (power on, switch input, etc.)",
    color: "#3b82f6",
    summary: (step, devices) => {
      const dev = devices.find((d) => d.id === step.device);
      const name = dev ? dev.name : step.device ?? "?";
      return `${name} → ${step.command ?? "?"}`;
    },
    defaults: () => ({ action: "device.command", device: "", command: "" }),
  },
  {
    action: "group.command",
    label: "Group Command",
    description: "Send a command to all devices in a group at once",
    color: "#0ea5e9",
    summary: (step) => `${step.group ?? "?"} → ${step.command ?? "?"}`,
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
    label: "Set Variable",
    description: "Set a project variable or state value",
    color: "#10b981",
    summary: (step) => `${step.key ?? "?"} = ${JSON.stringify(step.value ?? "")}`,
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
    summary: (step) => step.macro ?? "?",
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
      const val = cond.value != null ? JSON.stringify(cond.value) : "?";
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
    action: "wait_until",
    label: "Wait Until",
    description: "Pause until a state value matches a condition (with optional timeout)",
    color: "#14b8a6",
    summary: (step) => {
      const cond = step.condition;
      if (!cond?.key) return "No condition set";
      const op = cond.operator ?? "eq";
      const val = cond.value != null ? JSON.stringify(cond.value) : "?";
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

export function macroToScript(
  macro: MacroConfig,
  groups?: DeviceGroup[],
): string {
  const triggers = macro.triggers ?? [];
  const hasTriggers = triggers.length > 0;
  const hasStateChange = triggers.some((t) => t.type === "state_change" && t.enabled);
  const hasEvent = triggers.some((t) => t.type === "event" && t.enabled);
  const hasSchedule = triggers.some((t) => t.type === "schedule" && t.enabled);

  // Generate the body first; the import list depends on what it uses.
  const body: string[] = [];

  // Generate decorator-based handlers for triggers
  if (hasTriggers) {
    for (const trigger of triggers) {
      if (!trigger.enabled) continue;
      body.push("");
      if (trigger.type === "state_change" && trigger.state_key) {
        body.push(`@on_state_change("${_pyEscape(trigger.state_key)}")`);
        body.push("async def on_trigger(key, old_value, new_value):");
        // State operator check (compare() mirrors the trigger engine's coercion)
        if (trigger.state_operator && trigger.state_operator !== "any") {
          const op = trigger.state_operator;
          if (op === "truthy") body.push("    if not new_value:");
          else if (op === "falsy") body.push("    if new_value:");
          else {
            body.push(
              `    if not compare(new_value, "${_pyEscape(op)}", ${_pyValue(trigger.state_value ?? "", false)}):`
            );
          }
          body.push("        return");
        }
        // Guard conditions
        for (const cond of trigger.conditions ?? []) {
          body.push(_conditionToGuard(cond, "    "));
          body.push("        return");
        }
        // Delay + re-check
        if ((trigger.delay_seconds ?? 0) > 0) {
          body.push(`    await asyncio.sleep(${trigger.delay_seconds})  # delay re-check`);
          if (trigger.state_operator && trigger.state_operator !== "any") {
            // Re-check using same operator (inverted for guard return)
            body.push(_conditionToGuard({
              key: trigger.state_key ?? "",
              operator: trigger.state_operator,
              value: trigger.state_value,
            }, "    "));
            body.push("        return");
          }
        }
        // Steps
        _generateStepLines(body, macro.steps, "    ", groups);
      } else if (trigger.type === "event" && trigger.event_pattern) {
        body.push(`@on_event("${_pyEscape(trigger.event_pattern)}")`);
        body.push("async def on_trigger(event, payload):");
        for (const cond of trigger.conditions ?? []) {
          body.push(_conditionToGuard(cond, "    "));
          body.push("        return");
        }
        _generateStepLines(body, macro.steps, "    ", groups);
      } else if (trigger.type === "schedule" && trigger.cron) {
        body.push(`# Schedule: ${_pyComment(trigger.cron)}`);
        body.push(`@on_event("schedule.macro_${_pyEscape(macro.id)}")`);
        body.push("async def on_trigger(event, payload):");
        _generateStepLines(body, macro.steps, "    ", groups);
      } else if (trigger.type === "startup") {
        body.push("@on_event(\"system.started\")");
        body.push("async def on_startup(event, payload):");
        if ((trigger.delay_seconds ?? 0) > 0) {
          body.push(`    await asyncio.sleep(${trigger.delay_seconds})`);
        }
        _generateStepLines(body, macro.steps, "    ", groups);
      }
    }
  }

  // Always include a manual run() function
  body.push("");
  body.push("");
  body.push("async def run():");
  if (macro.steps.length === 0) {
    body.push("    pass");
  } else {
    _generateStepLines(body, macro.steps, "    ", groups);
  }

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
  // Schedule triggers also use on_event
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
 * `state.get(...)` lookup — mirroring the macro engine, which resolves
 * `$state.key` references in command params and state.set values at runtime
 * (top level only, so nested values recurse with resolution off). */
function _pyValue(v: unknown, resolveDollar: boolean): string {
  if (v === null || v === undefined) return "None";
  if (typeof v === "boolean") return v ? "True" : "False";
  if (typeof v === "number") return Number.isFinite(v) ? String(v) : "None";
  if (typeof v === "string") {
    if (resolveDollar && v.startsWith("$")) {
      return `state.get("${_pyEscape(v.slice(1))}")`;
    }
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

/** Render a params dict as a Python dict literal, resolving top-level `$state.key`
 * string values to `state.get(...)` like the macro engine's _resolve_params. */
function _pyParamsDict(params: Record<string, unknown>): string {
  const entries = Object.entries(params).map(
    ([k, v]) => `"${_pyEscape(k)}": ${_pyValue(v, true)}`
  );
  return `{${entries.join(", ")}}`;
}

/** Positive-sense condition expression (true when the condition matches).
 * Comparison operators go through compare(), which applies the same alias
 * normalization and type coercion as the macro/trigger engines. */
function _conditionExpr(cond: { key?: string; operator?: string; value?: unknown }): string {
  const key = _pyEscape(cond.key ?? "");
  const op = cond.operator ?? "eq";
  if (op === "truthy") return `state.get("${key}")`;
  if (op === "falsy") return `not state.get("${key}")`;
  return `compare(state.get("${key}"), "${_pyEscape(op)}", ${_pyValue(cond.value ?? "", false)})`;
}

/** Generate a guard condition (if ... return) line for a trigger condition/operator. */
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
): void {
  if (steps.length === 0) {
    lines.push(`${indent}pass`);
    return;
  }
  for (const step of steps) {
    // skip_if guard — if the condition matches, skip the step (mirrors the
    // macro engine, including compare()'s operator aliases + type coercion)
    if (step.skip_if) {
      lines.push(`${indent}if ${_conditionExpr(step.skip_if)}:  # skip_if`);
      lines.push(`${indent}    pass  # skipped`);
      // Wrap the actual step in else
      lines.push(`${indent}else:`);
      // Re-enter with extra indent
      _generateStepLines(lines, [{ ...step, skip_if: undefined }], indent + "    ", groups);
      continue;
    }
    switch (step.action) {
      case "device.command": {
        const device = _pyEscape(step.device ?? "");
        const params = step.params ? `, ${_pyParamsDict(step.params)}` : "";
        const sendLine = `await devices.send("${device}", "${_pyEscape(step.command ?? "")}"${params})`;
        if (step.skip_if_offline) {
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
        lines.push(
          `${indent}    if not state.get(f"device.{device_id}.connected"):`
        );
        lines.push(
          `${indent}        continue  # offline devices are skipped, matching the macro engine`
        );
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
          `${indent}# (no script API for ui.navigate yet — call from a macro step instead)`
        );
        break;
      case "conditional": {
        const cond = step.condition;
        if (cond) {
          lines.push(`${indent}if ${_conditionExpr(cond)}:`);
          const thenSteps = (step as any).then_steps ?? [];
          const elseSteps = (step as any).else_steps ?? [];
          _generateStepLines(lines, thenSteps, indent + "    ", groups);
          if (elseSteps.length > 0) {
            lines.push(`${indent}else:`);
            _generateStepLines(lines, elseSteps, indent + "    ", groups);
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
        const checkExpr = _conditionExpr(cond);
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
            `${indent}# Plugin action '${_pyComment(step.action)}' — call the plugin's script API directly`
          );
          lines.push(`${indent}# Params: ${_pyComment(params)}`);
        } else {
          lines.push(`${indent}# Unsupported step type: ${_pyComment(step.action)}`);
        }
        break;
    }
  }
}

/** Analyze a macro for potential script conversion issues. */
export function getConversionWarnings(macro: MacroConfig, groups?: DeviceGroup[]): string[] {
  const warnings: string[] = [];
  const checkSteps = (steps: MacroStep[]) => {
    for (const step of steps) {
      if (step.action === "group.command") {
        const group = groups?.find((g) => g.id === step.group);
        if (!group) {
          warnings.push(`Group command references unknown group "${step.group}" — device list will be empty in the generated script.`);
        } else {
          warnings.push(`Group command "${step.group}" → "${step.command}" is converted to a loop over ${group.device_ids.length} device(s). If the group membership changes later, update the script manually.`);
        }
      }
      if (step.action === "wait_until" && step.timeout == null) {
        warnings.push(`"Wait Until" step with no timeout — the script will poll forever until the condition is met. Make sure something will eventually satisfy it.`);
      }
      if (step.then_steps) checkSteps(step.then_steps);
      if (step.else_steps) checkSteps(step.else_steps);
    }
  };
  checkSteps(macro.steps);

  const triggers = macro.triggers ?? [];
  if (triggers.some((t) => t.type === "schedule" && t.enabled)) {
    warnings.push("Schedule triggers are converted to event listeners. The schedule cron job still runs on the macro engine — disable the macro's schedule triggers after switching to the script.");
  }

  return warnings;
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
