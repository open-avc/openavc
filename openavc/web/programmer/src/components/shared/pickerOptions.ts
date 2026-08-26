/**
 * Option lists for the things this product picks over and over: a driver's
 * commands, a project's devices, macros, pages.
 *
 * Built here rather than at each call site because the three command dropdowns
 * had already drifted apart — the device page drew the friendly label AND the
 * id, the macro step editor drew the label only, the UI Builder binding drew
 * the id only. Which of a command's three names you see should not depend on
 * which screen you are on.
 */
import type { SelectOption, SelectGroup } from "./SearchableSelect";

/** One entry in a driver's declared command map, as `GET /api/devices/{id}`
 *  returns it. Everything is optional: a command may declare nothing but its
 *  name. */
interface CommandDef {
  label?: unknown;
  help?: unknown;
}

function text(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value : undefined;
}

/**
 * A driver's commands, friendly name first and the id underneath.
 *
 * The id is carried as the hint rather than folded into the label because it
 * is what the project file stores and what a person reads back off a driver
 * definition — but it is also noise once the label says the same thing, which
 * is why `SearchableSelect` drops a hint that repeats its label. `help` is
 * searchable without being drawn: "what is the command for muting" should find
 * `set_audio_mute` even when nothing in its name says mute.
 */
export function commandOptions(
  commands: Record<string, unknown> | undefined | null,
): SelectOption[] {
  return Object.entries(commands ?? {}).map(([id, raw]) => {
    const def = (raw ?? {}) as CommandDef;
    return {
      value: id,
      label: text(def.label) ?? id,
      hint: id,
      keywords: text(def.help),
    };
  });
}

/** A project's devices. `driver` rides along as searchable right-aligned text,
 *  so "pjlink" finds every projector without anybody having named them well. */
export function deviceOptions(
  devices: { id: string; name?: string; driver?: string }[],
  opts: { prefix?: (deviceId: string) => SelectOption["prefix"] } = {},
): SelectOption[] {
  return devices.map((d) => ({
    value: d.id,
    label: d.name || d.id,
    hint: d.id,
    meta: d.driver,
    keywords: d.driver,
    prefix: opts.prefix?.(d.id),
  }));
}

/** A project's macros. `steps` is optional because not every caller holds the
 *  full macro — the ones that do show the count, as their `<select>` did. */
export function macroOptions(
  macros: { id: string; name?: string; steps?: unknown[] }[],
  opts: { showStepCount?: boolean } = {},
): SelectOption[] {
  return macros.map((m) => ({
    value: m.id,
    label: m.name || m.id,
    hint: m.id,
    badge:
      opts.showStepCount && Array.isArray(m.steps)
        ? `${m.steps.length} ${m.steps.length === 1 ? "step" : "steps"}`
        : undefined,
  }));
}

/**
 * A project's panel pages, split the way every page dropdown already split
 * them: ordinary pages first, then overlays and sidebars.
 *
 * Returns groups rather than a flat list, and omits an empty one — a heading
 * over nothing reads as a page that failed to load.
 */
export function pageGroups(
  pages: { id: string; name?: string; page_type?: string }[],
): SelectGroup[] {
  const option = (p: { id: string; name?: string }): SelectOption => ({
    value: p.id,
    label: p.name || p.id,
    hint: p.id,
  });
  const regular = pages.filter((p) => (p.page_type ?? "page") === "page");
  const overlay = pages.filter((p) => {
    const t = p.page_type ?? "page";
    return t === "overlay" || t === "sidebar";
  });
  const groups: SelectGroup[] = [];
  if (regular.length > 0) groups.push({ label: "Pages", options: regular.map(option) });
  if (overlay.length > 0) {
    groups.push({ label: "Overlays / Sidebars", options: overlay.map(option) });
  }
  return groups;
}

/**
 * The two targets that are not pages: go back, and close the overlay on top.
 *
 * A Cancel button pointed at one of these works from wherever the dialog was
 * opened, so the dialog stays reusable instead of always landing the operator
 * on one page.
 */
export const NAVIGATE_SPECIAL_GROUP: SelectGroup = {
  label: "Go back",
  options: [
    {
      value: "$back",
      label: "Back (close overlay, or previous page)",
      hint: "$back",
    },
    { value: "$dismiss", label: "Close this overlay", hint: "$dismiss" },
  ],
};
