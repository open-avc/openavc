import type { TriggerConfig, DeviceConfig, MacroConfig } from "../../api/types";

export interface TriggerTypeInfo {
  type: string;
  label: string;
  description: string;
  color: string;
  icon: string; // lucide icon name
  summary: (trigger: TriggerConfig, devices: DeviceConfig[], macros: MacroConfig[]) => string;
  defaults: () => Partial<TriggerConfig>;
}

export const TRIGGER_TYPES: TriggerTypeInfo[] = [
  {
    type: "schedule",
    label: "Schedule",
    description: "Run this macro on a time schedule",
    color: "#6366f1",
    icon: "Clock",
    summary: (t) => {
      if (t.cron) return describeCron(t.cron);
      return "No schedule set";
    },
    defaults: () => ({
      type: "schedule",
      enabled: true,
      cron: "",
      delay_seconds: 0,
      debounce_seconds: 0,
      cooldown_seconds: 0,
      overlap: "skip",
      conditions: [],
    }),
  },
  {
    type: "state_change",
    label: "State Change",
    description: "Run when a variable or device state changes",
    color: "#10b981",
    icon: "Activity",
    summary: (t) => {
      const key = t.state_key ?? "?";
      const op = t.state_operator ?? "any";
      if (op === "any") return `${key} changes`;
      if (op === "truthy") return `${key} becomes truthy`;
      if (op === "falsy") return `${key} becomes falsy`;
      return `${key} ${OPERATOR_LABELS[op] ?? op} ${JSON.stringify(t.state_value ?? "")}`;
    },
    defaults: () => ({
      type: "state_change",
      enabled: true,
      state_key: "",
      state_operator: "any",
      delay_seconds: 0,
      debounce_seconds: 0,
      cooldown_seconds: 0,
      overlap: "skip",
      conditions: [],
    }),
  },
  {
    type: "event",
    label: "Event",
    description: "Run when a system event fires",
    color: "#f59e0b",
    icon: "Zap",
    summary: (t) => t.event_pattern ?? "No event set",
    defaults: () => ({
      type: "event",
      enabled: true,
      event_pattern: "",
      delay_seconds: 0,
      debounce_seconds: 0,
      cooldown_seconds: 0,
      overlap: "skip",
      conditions: [],
    }),
  },
  {
    type: "startup",
    label: "Startup",
    description: "Run when the system starts up",
    color: "#8b5cf6",
    icon: "Power",
    summary: (t) => {
      const delay = t.delay_seconds ?? 0;
      if (delay > 0) return `After ${delay}s startup delay`;
      return "On system start";
    },
    defaults: () => ({
      type: "startup",
      enabled: true,
      delay_seconds: 5,
      cooldown_seconds: 0,
      overlap: "skip",
      conditions: [],
    }),
  },
];

export function getTriggerType(type: string): TriggerTypeInfo | undefined {
  return TRIGGER_TYPES.find((t) => t.type === type);
}

export const OPERATOR_LABELS: Record<string, string> = {
  any: "changes (any)",
  eq: "=",
  ne: "!=",
  gt: ">",
  lt: "<",
  gte: ">=",
  lte: "<=",
  truthy: "is truthy",
  falsy: "is falsy",
};

export const STATE_OPERATORS = [
  { value: "any", label: "Changes (any value)" },
  { value: "eq", label: "Equals" },
  { value: "ne", label: "Not Equals" },
  { value: "gt", label: "Greater Than" },
  { value: "lt", label: "Less Than" },
  { value: "gte", label: "Greater or Equal" },
  { value: "lte", label: "Less or Equal" },
  { value: "truthy", label: "Becomes Truthy" },
  { value: "falsy", label: "Becomes Falsy" },
];

export const CONDITION_OPERATORS = [
  { value: "eq", label: "Equals" },
  { value: "ne", label: "Not Equals" },
  { value: "gt", label: "Greater Than" },
  { value: "lt", label: "Less Than" },
  { value: "gte", label: "Greater or Equal" },
  { value: "lte", label: "Less or Equal" },
  { value: "truthy", label: "Is Truthy" },
  { value: "falsy", label: "Is Falsy" },
];

// --- Cron presets ---

export interface CronPreset {
  label: string;
  make: (hour: number, minute: number) => string;
}

export const CRON_PRESETS: CronPreset[] = [
  { label: "Every day at...", make: (h, m) => `${m} ${h} * * *` },
  { label: "Weekdays at...", make: (h, m) => `${m} ${h} * * 1-5` },
  { label: "Weekends at...", make: (h, m) => `${m} ${h} * * 0,6` },
  { label: "Every hour", make: () => "0 * * * *" },
  { label: "Custom", make: () => "" },
];

// Read a cron field as a plain integer, falling back when the field is
// anything else (`*`, a step like */15, a range like 8-17, a list).
// parseInt alone is wrong here: parseInt('*/15') is NaN and parseInt('8-17')
// silently truncates to 8, and both used to flow into rebuilt cron strings.
export function cronFieldInt(field: string | undefined, fallback: number): number {
  if (field === undefined || !/^\d+$/.test(field)) return fallback;
  return parseInt(field, 10);
}

/** Days of week (cron numbers, Sun=0) selected by a cron's dow field. */
export function getCronActiveDays(cron: string): Set<number> {
  const parts = cron.split(/\s+/);
  if (parts.length !== 5) return new Set();
  const dow = parts[4];
  if (dow === "*") return new Set([0, 1, 2, 3, 4, 5, 6]);
  const days = new Set<number>();
  for (const part of dow.split(",")) {
    if (part.includes("-")) {
      const [start, end] = part.split("-").map(Number);
      for (let i = start; i <= end; i++) days.add(i);
    } else {
      days.add(Number(part));
    }
  }
  return days;
}

// Rebuild a cron with a new day-of-week list, preserving the minute, hour,
// day, and month fields VERBATIM — a stepped/range schedule like
// */15 8-17 * * 1-5 keeps its cadence when the user toggles a weekday.
// A cron without 5 fields falls back to a daily-at-18:00 base.
export function cronWithDays(cron: string, days: number[]): string {
  const parts = cron.split(/\s+/);
  const base = parts.length === 5 ? parts : ["0", "18", "*", "*", "*"];
  const dowStr = [...days].sort((a, b) => a - b).join(",");
  return `${base[0]} ${base[1]} ${base[2]} ${base[3]} ${dowStr}`;
}

/**
 * Which EVENT_CATEGORIES index lists this saved event pattern, so the editor
 * opens showing the category the trigger actually uses. Unknown patterns
 * (script events, wildcards, events of a since-deleted device/macro) land on
 * Custom, which displays the raw pattern; no pattern means a fresh trigger,
 * which keeps the Device Events default.
 */
export function detectEventCategory(
  pattern: string | undefined,
  devices: DeviceConfig[],
  macros: MacroConfig[],
): number {
  if (!pattern) return 0;
  for (let i = 0; i < EVENT_CATEGORIES.length; i++) {
    const cat = EVENT_CATEGORIES[i];
    if (cat.label === "Custom") continue;
    if (cat.options(devices, macros).some((o) => o.pattern === pattern)) return i;
  }
  return EVENT_CATEGORIES.findIndex((c) => c.label === "Custom");
}

// croniter's non-standard aliases — the runtime accepts these verbatim, so the
// author-time validator must too. It only warns (the raw value saves either
// way), and flagging a working alias as "invalid" invites deleting a live
// schedule. @reboot is intentionally absent: croniter can't schedule it.
const CRON_ALIASES = new Set([
  "@yearly", "@annually", "@monthly", "@weekly", "@daily", "@midnight", "@hourly",
]);

// Three-letter names croniter accepts (case-insensitive) in the month and
// day-of-week fields, as whole values or range/list endpoints.
const CRON_MONTH_NAMES = [
  "jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec",
];
const CRON_DOW_NAMES = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"];

/** Cron expression validation matching the runtime (croniter): 5-field format
 *  with range checking, plus @-aliases and month / day-of-week names. */
export function isValidCron(cron: string): boolean {
  const trimmed = cron.trim();
  if (!trimmed) return false;
  if (CRON_ALIASES.has(trimmed.toLowerCase())) return true;
  const parts = trimmed.split(/\s+/);
  if (parts.length !== 5) return false;
  // Field ranges: minute(0-59), hour(0-23), day(1-31), month(1-12), dow(0-7)
  const maxValues = [59, 23, 31, 12, 7];
  const minValues = [0, 0, 1, 1, 0];
  // Names are valid only in the month (index 3) and day-of-week (index 4) fields.
  const nameFields: Record<number, string[]> = { 3: CRON_MONTH_NAMES, 4: CRON_DOW_NAMES };
  const fieldPattern = /^[\d*\-/,a-z]+$/i;
  for (let i = 0; i < 5; i++) {
    const p = parts[i];
    if (!fieldPattern.test(p)) return false;
    if (p === "*") continue;
    const names = nameFields[i];
    // Validate each comma-separated segment
    for (const seg of p.split(",")) {
      // Handle step values like */5 or 1-10/2
      const [rangePart, stepStr] = seg.split("/");
      if (stepStr !== undefined) {
        const step = parseInt(stepStr, 10);
        if (isNaN(step) || step <= 0) return false;
      }
      if (rangePart === "*") continue;
      // Handle ranges like 1-5 (or mon-fri / jan-mar in a name field)
      const [startStr, endStr] = rangePart.split("-");
      if (names && /[a-z]/i.test(rangePart)) {
        if (!names.includes(startStr.toLowerCase())) return false;
        if (endStr !== undefined && !names.includes(endStr.toLowerCase())) return false;
        continue;
      }
      const start = parseInt(startStr, 10);
      if (isNaN(start) || start < minValues[i] || start > maxValues[i]) return false;
      if (endStr !== undefined) {
        const end = parseInt(endStr, 10);
        if (isNaN(end) || end < minValues[i] || end > maxValues[i]) return false;
      }
    }
  }
  return true;
}

/** Human-readable summary of a cron expression. */
export function describeCron(cron: string): string {
  if (!cron) return "No schedule";
  const parts = cron.split(/\s+/);
  if (parts.length !== 5) return cron;

  const [min, hour, , , dow] = parts;

  const timeStr = hour !== "*" && min !== "*"
    ? `${hour.padStart(2, "0")}:${min.padStart(2, "0")}`
    : null;

  if (cron === "0 * * * *") return "Every hour";
  if (dow === "*" && timeStr) return `Every day at ${timeStr}`;
  if (dow === "1-5" && timeStr) return `Weekdays at ${timeStr}`;
  if ((dow === "0,6" || dow === "6,0") && timeStr) return `Weekends at ${timeStr}`;

  // Decode days
  const dayMap: Record<string, string> = {
    "0": "Sun", "1": "Mon", "2": "Tue", "3": "Wed",
    "4": "Thu", "5": "Fri", "6": "Sat",
  };
  if (timeStr && dow !== "*") {
    const days = dow.split(",").map((d) => dayMap[d] ?? d).join(", ");
    return `${days} at ${timeStr}`;
  }
  return cron;
}

// --- Event categories ---

export interface EventOption {
  label: string;
  pattern: string;
}

export interface EventCategory {
  label: string;
  options: (devices: DeviceConfig[], macros: MacroConfig[]) => EventOption[];
}

export const EVENT_CATEGORIES: EventCategory[] = [
  {
    label: "Device Events",
    options: (devices) =>
      devices.flatMap((d) => [
        { label: `${d.name} Connected`, pattern: `device.connected.${d.id}` },
        { label: `${d.name} Disconnected`, pattern: `device.disconnected.${d.id}` },
        { label: `${d.name} Error`, pattern: `device.error.${d.id}` },
      ]),
  },
  {
    label: "Macro Events",
    options: (_, macros) =>
      macros.flatMap((m) => [
        { label: `${m.name} Completed`, pattern: `macro.completed.${m.id}` },
        { label: `${m.name} Error`, pattern: `macro.error.${m.id}` },
      ]),
  },
  {
    label: "System Events",
    options: () => [
      { label: "System Started", pattern: "system.started" },
      { label: "System Stopping", pattern: "system.stopping" },
      { label: "Project Reloaded", pattern: "system.project.reloaded" },
    ],
  },
  {
    label: "Custom",
    options: () => [],
  },
];

let _nextTrigId = 1;
export function generateTriggerId(): string {
  return `trg_${Date.now()}_${_nextTrigId++}`;
}
