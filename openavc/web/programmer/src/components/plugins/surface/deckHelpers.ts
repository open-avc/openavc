/**
 * React-free helpers for the surface editors.
 *
 * Pure functions over a plugin's config — no hooks, no JSX — so the rules that
 * have to agree with the runtime (which pages exist, which zones the current
 * dials imply, where a navigate action points) can be read and tested on their
 * own. Companion to routingMatrixHelpers.ts next door.
 */
import type { ButtonAssignment, DialAssignment, TouchZone } from "./types";

export function addVirtualUnit(
  config: Record<string, unknown>,
  model: string
): { next: Record<string, unknown>; serial: string } {
  const entries =
    (config.virtual_decks as { model?: string; serial?: string }[] | undefined) ?? [];
  const serial = `VIRT-${Date.now().toString(36).toUpperCase()}`;
  return {
    next: { ...config, virtual_decks: [...entries, { model, serial }] },
    serial,
  };
}

interface NetworkDeckEntry {
  host?: string;
  port?: number;
  serial?: string;
  // The serial the unit advertises over mDNS (the dock's alias for the
  // deck). Same physical deck as `serial`; used only to suppress a
  // duplicate card and to follow the deck across address changes.
  mdns_sn?: string;
}

export function networkEntriesOf(config: Record<string, unknown>): NetworkDeckEntry[] {
  const raw = config.network_decks;
  if (!Array.isArray(raw)) return [];
  return raw.filter(
    (e): e is NetworkDeckEntry =>
      Boolean(e) && typeof e === "object" && typeof (e as NetworkDeckEntry).host === "string"
  );
}

export function networkEntryKey(e: NetworkDeckEntry): string {
  return `${e.host}:${e.port ?? 5343}`;
}

// Build the zones the runtime generates for the current dials — the
// "start from the current zones" seed when taking over the strip.
export function defaultZonesFromDials(
  dials: DialAssignment[],
  dialCount: number
): TouchZone[] {
  return Array.from({ length: dialCount }, (_, i) => {
    const dial = dials.find((d) => d.index === i);
    const adjust = dial?.adjust?.key ? { ...dial.adjust } : undefined;
    if (adjust && dial?.fader) adjust.fader = true;
    return {
      label: dial?.label || undefined,
      icon: dial?.icon || undefined,
      unit: dial?.unit || undefined,
      meter: dial?.meter,
      value_source: dial?.adjust?.key || undefined,
      touch: dial?.touch ?? dial?.press,
      long_touch: dial?.long_touch ?? dial?.long_press,
      drag_adjust: adjust,
    } as TouchZone;
  });
}

export const SURFACE_ACTIONS = ["macro", "device.command", "state.set", "navigate"];

// The per-unit config sections an own layout replaces (mirrors the runtime).
export const DECK_SECTION_KEYS = [
  "buttons", "global_buttons", "auto_page", "dials", "touchscreen",
  "info_strip", "auto_brightness", "idle_dim", "page_names",
];

function actionList(value: unknown): Record<string, unknown>[] {
  if (Array.isArray(value)) {
    return value.filter((a) => a && typeof a === "object") as Record<string, unknown>[];
  }
  if (value && typeof value === "object") return [value as Record<string, unknown>];
  return [];
}

export function forEachNavigateTarget(
  view: Record<string, unknown>,
  fn: (page: unknown) => void
) {
  const scan = (value: unknown) => {
    for (const action of actionList(value)) {
      if (action.action === "navigate") fn(action.page);
      for (const nested of ["off_action", "hold_action"]) {
        const sub = action[nested] as Record<string, unknown> | undefined;
        if (sub && typeof sub === "object" && sub.action === "navigate") {
          fn(sub.page);
        }
      }
    }
  };
  const buttons = (view.buttons as ButtonAssignment[] | undefined) ?? [];
  for (const b of buttons) scan(b?.bindings?.press);
  const globals = (view.global_buttons as ButtonAssignment[] | undefined) ?? [];
  for (const b of globals) scan(b?.bindings?.press);
  const dials = (view.dials as DialAssignment[] | undefined) ?? [];
  for (const d of dials) {
    scan(d?.cw);
    scan(d?.ccw);
    scan(d?.press);
  }
  const zones =
    ((view.touchscreen as { zones?: TouchZone[] } | undefined)?.zones) ?? [];
  for (const z of zones) {
    scan(z?.touch);
    scan(z?.long_touch);
  }
}

// Mirrors the runtime: pages exist by being used — 1 + the highest page
// index referenced by entries, names, paging rules, or navigate targets.
export function effectivePageCount(view: Record<string, unknown>): number {
  let highest = 0;
  const note = (value: unknown) => {
    if (typeof value !== "string" && typeof value !== "number") return;
    const n = Number(value);
    if (Number.isInteger(n) && n > highest) highest = n;
  };
  const buttons = (view.buttons as ButtonAssignment[] | undefined) ?? [];
  for (const b of buttons) note(b?.page ?? 0);
  const rules = (view.auto_page as { page?: unknown }[] | undefined) ?? [];
  for (const r of rules) if (r && typeof r === "object") note(r.page);
  const names = (view.page_names as Record<string, string> | undefined) ?? {};
  for (const k of Object.keys(names)) note(k);
  forEachNavigateTarget(view, note);
  return highest + 1;
}

export function hasAnyNavigate(view: Record<string, unknown>): boolean {
  let found = false;
  forEachNavigateTarget(view, () => {
    found = true;
  });
  return found;
}
