import type { MacroStep, MacroConfig, DeviceConfig, DeviceGroup, TriggerConfig } from "../../api/types";

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
];

export function getStepType(action: string): StepTypeInfo | undefined {
  return STEP_TYPES.find((t) => t.action === action);
}

export function macroToScript(
  macro: MacroConfig,
  devices: DeviceConfig[]
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
  const lines: string[] = [
    `"""Auto-generated from macro '${escapedName}'."""`,
    `from openavc import ${imports.join(", ")}`,
    "import asyncio",
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
        _generateStepLines(lines, macro.steps, "    ");
      } else if (trigger.type === "event" && trigger.event_pattern) {
        lines.push(`@on_event("${trigger.event_pattern}")`);
        lines.push("async def on_trigger(event, payload):");
        for (const cond of trigger.conditions ?? []) {
          lines.push(_conditionToGuard(cond, "    "));
          lines.push("        return");
        }
        _generateStepLines(lines, macro.steps, "    ");
      } else if (trigger.type === "schedule" && trigger.cron) {
        lines.push(`# Schedule: ${trigger.cron}`);
        lines.push(`@on_event("schedule.macro_${macro.id}")`);
        lines.push("async def on_trigger(event, payload):");
        _generateStepLines(lines, macro.steps, "    ");
      } else if (trigger.type === "startup") {
        lines.push("@on_event(\"system.started\")");
        lines.push("async def on_startup(event, payload):");
        if ((trigger.delay_seconds ?? 0) > 0) {
          lines.push(`    await asyncio.sleep(${trigger.delay_seconds})`);
        }
        _generateStepLines(lines, macro.steps, "    ");
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
  indent: string
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
        _generateStepLines(lines, [{ ...step, skip_if: undefined }], indent + "    ");
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
        lines.push(
          `${indent}# Group command: ${_pyEscape(step.group ?? "")} -> ${_pyEscape(step.command ?? "")}`
        );
        lines.push(
          `${indent}for device_id in device_groups.get("${_pyEscape(step.group ?? "")}", []):`
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
          _generateStepLines(lines, thenSteps, indent + "    ");
          if (elseSteps.length > 0) {
            lines.push(`${indent}else:`);
            _generateStepLines(lines, elseSteps, indent + "    ");
          }
        } else {
          lines.push(`${indent}# Conditional step with no condition set`);
          lines.push(`${indent}pass`);
        }
        break;
      }
      default:
        lines.push(`${indent}# Unsupported step type: ${step.action}`);
        break;
    }
  }
}

let _nextId = 1;
export function generateId(prefix: string): string {
  return `${prefix}_${Date.now()}_${_nextId++}`;
}
