import type { MacroStep, ProjectConfig } from "../../api/types";

/**
 * True when `value` is a state key that targets device `deviceId` — i.e. it is
 * exactly `device.<deviceId>` or begins `device.<deviceId>.`. Anchored on both
 * ends so `device.proj1` never matches a sibling `device.proj10` (a substring
 * or bare `startsWith` would over-report). A leading `$` — a dynamic macro
 * param/value reference like `$device.<id>.volume` — is tolerated.
 */
function keyTargetsDevice(value: unknown, deviceId: string): boolean {
  if (typeof value !== "string") return false;
  const s = value.startsWith("$") ? value.slice(1) : value;
  const prefix = `device.${deviceId}`;
  return s === prefix || s.startsWith(`${prefix}.`);
}

/**
 * True when a device group `groupId` exists and lists `deviceId` as a member —
 * used to resolve a `group.command` step/action back to the devices it drives.
 */
function groupContainsDevice(project: ProjectConfig, groupId: unknown, deviceId: string): boolean {
  if (typeof groupId !== "string") return false;
  const group = (project.device_groups ?? []).find((g) => g.id === groupId);
  return !!group?.device_ids?.includes(deviceId);
}

/**
 * Recursively test whether any string in a value tree references the device
 * as a state key, or whether any object node is a command action/step that
 * targets the device directly (`device: "<id>"`) or via a `group.command`
 * whose group contains it. Covers macro-step params, `state.set` values, and
 * the show/do binding trees on UI elements — all of which nest device
 * references at arbitrary depth.
 */
function treeReferencesDevice(value: unknown, deviceId: string, project: ProjectConfig): boolean {
  if (typeof value === "string") return keyTargetsDevice(value, deviceId);
  if (Array.isArray(value)) return value.some((v) => treeReferencesDevice(v, deviceId, project));
  if (value && typeof value === "object") {
    const obj = value as Record<string, unknown>;
    if (obj.device === deviceId) return true;
    if (groupContainsDevice(project, obj.group, deviceId)) return true;
    return Object.values(obj).some((v) => treeReferencesDevice(v, deviceId, project));
  }
  return false;
}

/**
 * True when a single macro step targets the device: a direct `device.command`,
 * a `group.command` on a group containing it, a `state.set`/guard key, or a
 * `$device.<id>` reference buried in params or the set value. Does NOT descend
 * into `then_steps`/`else_steps` — the caller walks those so each nested step
 * is counted on its own.
 */
function stepTargetsDevice(step: MacroStep, deviceId: string, project: ProjectConfig): boolean {
  if (step.device === deviceId) return true;
  if (groupContainsDevice(project, step.group, deviceId)) return true;
  if (keyTargetsDevice(step.key, deviceId)) return true;
  if (keyTargetsDevice(step.condition?.key, deviceId)) return true;
  if (keyTargetsDevice(step.skip_if?.key, deviceId)) return true;
  if (treeReferencesDevice(step.params, deviceId, project)) return true;
  if (treeReferencesDevice(step.value, deviceId, project)) return true;
  return false;
}

/** Count steps referencing the device, recursing into conditional branches. */
function countStepReferences(steps: MacroStep[], deviceId: string, project: ProjectConfig): number {
  let count = 0;
  for (const step of steps) {
    if (stepTargetsDevice(step, deviceId, project)) count++;
    if (step.then_steps) count += countStepReferences(step.then_steps, deviceId, project);
    if (step.else_steps) count += countStepReferences(step.else_steps, deviceId, project);
  }
  return count;
}

/**
 * True when an event trigger pattern targets the device. Device lifecycle
 * events are `device.<event>.<deviceId>` (connected, disconnected, error, …),
 * so match the id only as a whole dotted segment (never `proj10` for `proj1`),
 * and ignore wildcard globs that fan out to every device.
 */
function eventPatternTargetsDevice(pattern: unknown, deviceId: string): boolean {
  if (typeof pattern !== "string" || !pattern.startsWith("device.")) return false;
  return pattern.split(".").includes(deviceId);
}

export function findDeviceReferences(project: ProjectConfig, deviceId: string): string[] {
  const refs: string[] = [];

  // Device group membership — a group.command acts on this device, and a
  // dangling id left behind after delete is itself a broken reference.
  for (const group of project.device_groups ?? []) {
    if (group.device_ids?.includes(deviceId)) {
      refs.push(`Device group "${group.name}"`);
    }
  }

  // Macro steps (recursing into conditional branches) + triggers.
  for (const macro of project.macros) {
    const stepHits = countStepReferences(macro.steps, deviceId, project);
    if (stepHits > 0) {
      refs.push(`Macro "${macro.name}": ${stepHits} step(s)`);
    }
    for (const t of macro.triggers ?? []) {
      if (keyTargetsDevice(t.state_key, deviceId)) {
        refs.push(`Macro "${macro.name}" trigger: ${t.state_key}`);
      }
      if (eventPatternTargetsDevice(t.event_pattern, deviceId)) {
        refs.push(`Macro "${macro.name}" trigger: ${t.event_pattern}`);
      }
      for (const c of t.conditions ?? []) {
        if (keyTargetsDevice(c.key, deviceId)) {
          refs.push(`Macro "${macro.name}" trigger condition: ${c.key}`);
        }
      }
    }
  }

  // UI element bindings — show state keys and do-action device/group targets,
  // matched with anchored keys instead of a stringified substring test.
  for (const page of project.ui?.pages ?? []) {
    for (const el of page.elements) {
      if (treeReferencesDevice(el.bindings, deviceId, project)) {
        refs.push(`UI page "${page.name}" element "${el.label || el.id}"`);
      }
    }
  }

  return refs;
}

/**
 * Validate and coerce a device-setting edit before it is written to the
 * hardware. The old inline coercion turned a blank or non-numeric entry
 * into 0 (`parseInt(v) || 0`) and ignored the definition's min/max/regex —
 * silently saving a wrong control value (input level, fan threshold, ID)
 * to a real device. Returns the coerced value or an actionable error.
 */
export function validateSettingValue(
  def: { type?: string; min?: number; max?: number; regex?: string } | undefined,
  raw: string,
): { ok: true; value: unknown } | { ok: false; error: string } {
  const fieldType = String(def?.type ?? "string");

  if (fieldType === "boolean") {
    return { ok: true, value: raw === "true" };
  }

  if (fieldType === "integer" || fieldType === "number" || fieldType === "float") {
    const trimmed = raw.trim();
    if (trimmed === "") {
      return { ok: false, error: "Enter a number — the setting was not saved." };
    }
    const n = Number(trimmed);
    if (!Number.isFinite(n)) {
      return { ok: false, error: `"${raw}" is not a number — the setting was not saved.` };
    }
    if (fieldType === "integer" && !Number.isInteger(n)) {
      return { ok: false, error: "Enter a whole number — the setting was not saved." };
    }
    if (def?.min !== undefined && n < def.min) {
      return { ok: false, error: `Must be at least ${def.min}.` };
    }
    if (def?.max !== undefined && n > def.max) {
      return { ok: false, error: `Must be at most ${def.max}.` };
    }
    return { ok: true, value: n };
  }

  // String-ish settings: honor the definition's regex when present.
  if (def?.regex) {
    try {
      if (!new RegExp(def.regex).test(raw)) {
        return {
          ok: false,
          error: `Doesn't match the required format (${def.regex}).`,
        };
      }
    } catch {
      // A driver shipped an invalid regex — don't block the save on it.
    }
  }
  return { ok: true, value: raw };
}
