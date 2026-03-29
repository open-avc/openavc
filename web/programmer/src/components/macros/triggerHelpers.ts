import type { TriggerConfig, DeviceConfig, MacroConfig, ScheduleConfig } from "../../api/types";

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

/** Basic cron expression validation (5-field format). */
export function isValidCron(cron: string): boolean {
  if (!cron.trim()) return false;
  const parts = cron.trim().split(/\s+/);
  if (parts.length !== 5) return false;
  // Each field should only contain digits, *, -, /, ,
  const fieldPattern = /^[\d*\-/,]+$/;
  return parts.every((p) => fieldPattern.test(p));
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
  options: (devices: DeviceConfig[], macros: MacroConfig[], schedules: ScheduleConfig[]) => EventOption[];
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
    label: "Schedule Events",
    options: (_, __, schedules) =>
      schedules.map((s) => ({
        label: s.description || s.id,
        pattern: s.event || `schedule.${s.id}`,
      })),
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
