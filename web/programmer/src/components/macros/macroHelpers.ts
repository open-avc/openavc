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

  // Build import list based on what the script needs
  const imports = ["devices", "state", "events", "macros", "log"];
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

  const escapedName = macro.name.replace(/\\/g, "\\\\").replace(/'/g, "\\'");
  const needsTimeImport = _macroUsesWaitUntilWithTimeout(macro.steps);
  const lines: string[] = [
    `"""Auto-generated from macro '${escapedName}'."""`,
    `from openavc import ${imports.join(", ")}`,
    "import asyncio",
    ...(needsTimeImport ? ["import time as _t"] : []),
    "",
  ];

  // Generate decorator-based handlers for triggers
  if (hasTriggers) {
    for (const trigger of triggers) {
      if (!trigger.enabled) continue;
      lines.push("");
      if (trigger.type === "state_change" && trigger.state_key) {
        lines.push(`@on_state_change("${trigger.state_key}")`);
        lines.push("async def on_trigger(key, old_value, new_value):");
        // State operator check
        if (trigger.state_operator && trigger.state_operator !== "any") {
          const op = trigger.state_operator;
          const val = JSON.stringify(trigger.state_value ?? "");
          if (op === "eq") lines.push(`    if new_value != ${val}:`);
          else if (op === "ne") lines.push(`    if new_value == ${val}:`);
          else if (op === "truthy") lines.push("    if not new_value:");
          else if (op === "falsy") lines.push("    if new_value:");
          else if (op === "gt") lines.push(`    if new_value is None or new_value <= ${val}:`);
          else if (op === "lt") lines.push(`    if new_value is None or new_value >= ${val}:`);
          else if (op === "gte") lines.push(`    if new_value is None or new_value < ${val}:`);
          else if (op === "lte") lines.push(`    if new_value is None or new_value > ${val}:`);
          lines.push("        return");
        }
        // Guard conditions
        for (const cond of trigger.conditions ?? []) {
          lines.push(_conditionToGuard(cond, "    "));
          lines.push("        return");
        }
        // Delay + re-check
        if ((trigger.delay_seconds ?? 0) > 0) {
          lines.push(`    await asyncio.sleep(${trigger.delay_seconds})  # delay re-check`);
          if (trigger.state_operator && trigger.state_operator !== "any") {
            // Re-check using same operator (inverted for guard return)
            lines.push(_conditionToGuard({
              key: trigger.state_key ?? "",
              operator: trigger.state_operator,
              value: trigger.state_value,
            }, "    "));
            lines.push("        return");
          }
        }
        // Steps
        _generateStepLines(lines, macro.steps, "    ", groups);
      } else if (trigger.type === "event" && trigger.event_pattern) {
        lines.push(`@on_event("${trigger.event_pattern}")`);
        lines.push("async def on_trigger(event, payload):");
        for (const cond of trigger.conditions ?? []) {
          lines.push(_conditionToGuard(cond, "    "));
          lines.push("        return");
        }
        _generateStepLines(lines, macro.steps, "    ", groups);
      } else if (trigger.type === "schedule" && trigger.cron) {
        lines.push(`# Schedule: ${trigger.cron}`);
        lines.push(`@on_event("schedule.macro_${macro.id}")`);
        lines.push("async def on_trigger(event, payload):");
        _generateStepLines(lines, macro.steps, "    ", groups);
      } else if (trigger.type === "startup") {
        lines.push("@on_event(\"system.started\")");
        lines.push("async def on_startup(event, payload):");
        if ((trigger.delay_seconds ?? 0) > 0) {
          lines.push(`    await asyncio.sleep(${trigger.delay_seconds})`);
        }
        _generateStepLines(lines, macro.steps, "    ", groups);
      }
    }
  }

  // Always include a manual run() function
  lines.push("");
  lines.push("");
  lines.push("async def run():");
  if (macro.steps.length === 0) {
    lines.push("    pass");
  } else {
    _generateStepLines(lines, macro.steps, "    ");
  }

  return lines.join("\n") + "\n";
}

/** Escape a string for use inside a Python string literal. */
function _pyEscape(s: string): string {
  return s.replace(/\\/g, "\\\\").replace(/"/g, '\\"').replace(/'/g, "\\'");
}

/** Generate a guard condition (if ... return) line for a trigger condition/operator. */
function _conditionToGuard(
  cond: { key?: string; operator?: string; value?: unknown },
  indent: string
): string {
  const key = _pyEscape(cond.key ?? "");
  const val = JSON.stringify(cond.value ?? "");
  const op = cond.operator ?? "eq";

  switch (op) {
    case "eq":
    case "equals":
    case "==":
      return `${indent}if state.get("${key}") != ${val}:`;
    case "ne":
    case "not_equals":
    case "!=":
      return `${indent}if state.get("${key}") == ${val}:`;
    case "gt":
    case "greater_than":
    case ">":
      return `${indent}if state.get("${key}") is None or state.get("${key}") <= ${val}:`;
    case "lt":
    case "less_than":
    case "<":
      return `${indent}if state.get("${key}") is None or state.get("${key}") >= ${val}:`;
    case "gte":
    case "greater_or_equal":
    case ">=":
      return `${indent}if state.get("${key}") is None or state.get("${key}") < ${val}:`;
    case "lte":
    case "less_or_equal":
    case "<=":
      return `${indent}if state.get("${key}") is None or state.get("${key}") > ${val}:`;
    case "truthy":
      return `${indent}if not state.get("${key}"):`;
    case "falsy":
      return `${indent}if state.get("${key}"):`;
    default:
      return `${indent}if state.get("${key}") != ${val}:`;
  }
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
    // skip_if guard
    if (step.skip_if) {
      const guardLine = _conditionToGuard(step.skip_if, indent);
      if (guardLine) {
        // _conditionToGuard generates "if <inverse-condition>:" — we want skip logic
        // so: if condition matches, skip (continue)
        const key = _pyEscape(step.skip_if.key ?? "");
        const val = JSON.stringify(step.skip_if.value ?? "");
        const op = step.skip_if.operator ?? "eq";
        const opMap: Record<string, string> = { eq: "==", ne: "!=", gt: ">", lt: "<", gte: ">=", lte: "<=" };
        const pyOp = opMap[op] ?? "==";
        if (op === "truthy") {
          lines.push(`${indent}if state.get("${key}"):  # skip_if`);
        } else if (op === "falsy") {
          lines.push(`${indent}if not state.get("${key}"):  # skip_if`);
        } else {
          lines.push(`${indent}if state.get("${key}") ${pyOp} ${val}:  # skip_if`);
        }
        lines.push(`${indent}    pass  # skipped`);
        // Wrap the actual step in else
        lines.push(`${indent}else:`);
        // Re-enter with extra indent
        _generateStepLines(lines, [{ ...step, skip_if: undefined }], indent + "    ", groups);
        continue;
      }
    }
    switch (step.action) {
      case "device.command": {
        const params = step.params ? `, ${JSON.stringify(step.params)}` : "";
        lines.push(
          `${indent}await devices.send("${_pyEscape(step.device ?? "")}", "${_pyEscape(step.command ?? "")}"${params})`
        );
        break;
      }
      case "group.command": {
        const params = step.params ? `, ${JSON.stringify(step.params)}` : "";
        const group = groups?.find((g) => g.id === step.group);
        const deviceIds = group?.device_ids ?? [];
        lines.push(
          `${indent}# Group command: ${_pyEscape(step.group ?? "")} -> ${_pyEscape(step.command ?? "")}`
        );
        lines.push(
          `${indent}for device_id in ${JSON.stringify(deviceIds)}:`
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
          `${indent}state.set("${_pyEscape(step.key ?? "")}", ${JSON.stringify(step.value ?? "")})`
        );
        break;
      case "event.emit": {
        const payload = step.payload ? `, ${JSON.stringify(step.payload)}` : "";
        lines.push(`${indent}await events.emit("${_pyEscape(step.event ?? "")}"${payload})`);
        break;
      }
      case "macro":
        lines.push(`${indent}await macros.execute("${_pyEscape(step.macro ?? "")}")`);
        break;
      case "conditional": {
        const cond = step.condition;
        if (cond) {
          const key = _pyEscape(cond.key ?? "");
          const val = JSON.stringify(cond.value ?? "");
          const op = cond.operator ?? "eq";
          const opMap: Record<string, string> = { eq: "==", ne: "!=", gt: ">", lt: "<", gte: ">=", lte: "<=" };
          const pyOp = opMap[op] ?? "==";
          if (op === "truthy") {
            lines.push(`${indent}if state.get("${key}"):`);
          } else if (op === "falsy") {
            lines.push(`${indent}if not state.get("${key}"):`);
          } else {
            lines.push(`${indent}if state.get("${key}") ${pyOp} ${val}:`);
          }
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
        const val = JSON.stringify(cond.value ?? "");
        const op = cond.operator ?? "eq";
        const opMap: Record<string, string> = { eq: "==", ne: "!=", gt: ">", lt: "<", gte: ">=", lte: "<=" };
        const pyOp = opMap[op] ?? "==";
        let checkExpr: string;
        if (op === "truthy") checkExpr = `state.get("${key}")`;
        else if (op === "falsy") checkExpr = `not state.get("${key}")`;
        else checkExpr = `state.get("${key}") ${pyOp} ${val}`;
        const timeout = step.timeout;
        const onTimeout = step.on_timeout ?? "fail";
        if (timeout == null) {
          // Never time out — poll until satisfied
          lines.push(`${indent}while not (${checkExpr}):`);
          lines.push(`${indent}    await asyncio.sleep(0.5)`);
        } else {
          lines.push(`${indent}# wait_until: ${cond.key} ${op} ${val} (timeout ${timeout}s, ${onTimeout})`);
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
        lines.push(`${indent}# Unsupported step type: ${step.action}`);
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
