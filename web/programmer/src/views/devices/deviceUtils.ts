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
