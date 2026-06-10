import type { ProjectConfig } from "../../api/types";

export function findDeviceReferences(project: ProjectConfig, deviceId: string): string[] {
  const refs: string[] = [];
  const prefix = `device.${deviceId}`;

  // Check macro steps
  for (const macro of project.macros) {
    const stepRefs = macro.steps.filter((s) => s.device === deviceId);
    if (stepRefs.length > 0) {
      refs.push(`Macro "${macro.name}": ${stepRefs.length} step(s)`);
    }
    // Check trigger state_key references
    for (const t of macro.triggers ?? []) {
      if (t.state_key?.startsWith(prefix)) {
        refs.push(`Macro "${macro.name}" trigger: ${t.state_key}`);
      }
      for (const c of t.conditions ?? []) {
        if (c.key.startsWith(prefix)) {
          refs.push(`Macro "${macro.name}" trigger condition: ${c.key}`);
        }
      }
    }
  }

  // Check UI bindings (press/feedback bindings reference device state keys)
  for (const page of project.ui?.pages ?? []) {
    for (const el of page.elements) {
      const bindings = JSON.stringify(el.bindings);
      if (bindings.includes(deviceId)) {
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

  if (fieldType === "integer" || fieldType === "number") {
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
