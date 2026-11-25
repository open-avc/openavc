import { create } from "zustand";

export interface LogEntry {
  /** Client-assigned monotonic id — stable React key on the sliding window */
  id: number;
  timestamp: number;
  level: string;
  source: string;
  category: string;
  message: string;
  /** Device id for device-category entries (server-extracted), "" otherwise */
  device: string;
}

export type StepPathSegment = number | "then" | "else";

export interface MacroProgress {
  macroId: string | null;
  stepIndex: number | null;
  totalSteps: number | null;
  status: "idle" | "running" | "completed" | "error" | "cancelled";
  /** Tracks execution path through conditional branches, e.g. [2, "then", 0] */
  activeStepPath: StepPathSegment[];
}

export interface StepError {
  stepIndex: number;
  action: string;
  device: string;
  group: string;
  command: string;
  /** What the exception said. Kept for the hover, where the raw text helps. */
  error: string;
  /** The same failure written for a person, as the panel shows it. */
  message: string;
  description: string;
}

export interface ConditionalResult {
  stepIndex: number;
  conditionResult: boolean;
  branch: "then" | "else";
  conditionKey: string;
  conditionOperator: string;
  actualValue: unknown;
}

export interface GroupCommandResult {
  stepIndex: number;
  group: string;
  command: string;
  deviceResults: Array<{
    device_id: string;
    name: string;
    success: boolean;
    error?: string;
    message?: string;
    /** On a member that did not take it: whether the command reached it. */
    sent?: boolean;
    /** Why it was not a plain success, when that is known: not_sent, partial,
     *  uncertain, pending, failed, rejected. */
    outcome?: string;
  }>;
}

export interface MacroLastRun {
  macroId: string;
  startedAt: number;
  completedAt: number;
  duration: number;
  status: "completed" | "error" | "cancelled";
  stepErrors: StepError[];
  conditionalResults: ConditionalResult[];
  groupResults: GroupCommandResult[];
  stepSkips: StepSkip[];
  error?: string;
}

export interface TriggerPending {
  reason: "debounce" | "delay" | "queued";
  waitSeconds?: number;
  queuePosition?: number;
  timestamp: number;
}

/** How a trigger's last automatic fire ended.
 *
 *  Server-side truth, not a session flash: seeded from `GET /api/triggers` so
 *  a trigger that failed overnight still says so when the IDE is opened in the
 *  morning, then kept current by the `trigger.completed` message. `firedAt` is
 *  the server's own `last_fired`, which moves BEFORE the macro runs -- on its
 *  own it made a trigger that errors every time look freshly successful. */
export interface TriggerRun {
  outcome: "completed" | "failed" | "cancelled" | "skipped" | "error";
  error?: string;
  firedAt?: number;
}

/** Why a trigger's fire or a macro's run did not go ahead, as the server's
 *  `trigger.skipped` or `macro.skipped` said it: `code` from its reason table
 *  and `message`, that code's sentence. `at` is when it was heard (ms).
 *
 *  Heard on this connection only: the next fire or start ends it, and a
 *  dropped connection forgets it, since a fire may have happened unheard. */
export interface SkipNote {
  code: string;
  message: string;
  at: number;
}

/** Which step of which macro a run's result belongs to: the macro's id and the
 *  step's place in its tree (`[2, "then", 0]`), from the `running` frame that
 *  named the step. `components/macros/stepResults.ts` reads it. */
export interface PlacedResult {
  macroId: string;
  stepPath: StepPathSegment[];
}

/** A step its own setting held back in a run that went on (an Ask for Help
 *  inside its Send at most every), from a `macro.progress` whose status is
 *  `skipped`: its `code` and `message` from the same reason table. It belongs
 *  to the step last named as running, as a condition's answer does. */
export interface StepSkip extends SkipNote, PlacedResult {
  action: string;
}

interface LogStore {
  logEntries: LogEntry[];
  logPaused: boolean;
  logSubscribed: boolean;

  macroProgress: MacroProgress;
  runningMacros: Record<string, number>;
  // Step-level data accumulated during a macro run
  stepErrors: StepError[];
  conditionalResults: ConditionalResult[];
  groupResults: GroupCommandResult[];
  stepSkips: StepSkip[];
  macroStartedAt: number;
  lastRun: MacroLastRun | null;

  // Trigger pending states (trigger_id -> pending info)
  triggerPending: Record<string, TriggerPending>;
  // Recently-fired triggers (trigger_id -> fire timestamp), driving the brief
  // "just fired" highlight. Set from the trigger.fired WS message and auto-cleared
  // by useWebSocket after the flash.
  recentlyFired: Record<string, number>;
  // How each trigger's last automatic fire ended (trigger_id -> run). Unlike
  // the two above this is not a flash and is NOT cleared on disconnect: it is
  // the server's record, seeded from GET /api/triggers and updated live.
  triggerRuns: Record<string, TriggerRun>;
  // Why each trigger's latest fire, and each macro's latest start, did not go
  // ahead (id -> note). Many arrive a second from a busy trigger, so the
  // trigger list reads this on its one-second tick rather than subscribing.
  triggerSkips: Record<string, SkipNote>;
  macroSkips: Record<string, SkipNote>;

  addLogEntry: (entry: Omit<LogEntry, "id">) => void;
  addLogBatch: (entries: Omit<LogEntry, "id">[]) => void;
  setLogPaused: (v: boolean) => void;
  clearLogEntries: () => void;
  setLogSubscribed: (v: boolean) => void;

  setMacroProgress: (p: Partial<MacroProgress>) => void;
  resetMacroProgress: () => void;
  clearMacroRuns: () => void;
  addStepError: (e: StepError) => void;
  addConditionalResult: (r: ConditionalResult) => void;
  addGroupResult: (r: GroupCommandResult) => void;
  addStepSkip: (k: StepSkip) => void;
  startMacroRun: (macroId: string) => void;
  finishMacroRun: (macroId: string, status: MacroLastRun["status"], error?: string) => void;
  setTriggerPending: (triggerId: string, pending: TriggerPending | null) => void;
  setTriggerFired: (triggerId: string, fired: boolean) => void;
  setTriggerRun: (triggerId: string, run: TriggerRun) => void;
  setTriggerRuns: (runs: Record<string, TriggerRun>) => void;
  setTriggerSkip: (triggerId: string, note: SkipNote | null) => void;
  setMacroSkip: (macroId: string, note: SkipNote | null) => void;
  clearSkips: () => void;
}

let nextLogEntryId = 1;

const INITIAL_MACRO: MacroProgress = {
  macroId: null,
  stepIndex: null,
  totalSteps: null,
  status: "idle",
  activeStepPath: [],
};

export const useLogStore = create<LogStore>((set, get) => ({
  logEntries: [],
  logPaused: false,
  logSubscribed: false,

  macroProgress: { ...INITIAL_MACRO },
  runningMacros: {},
  stepErrors: [],
  conditionalResults: [],
  groupResults: [],
  stepSkips: [],
  macroStartedAt: 0,
  lastRun: null,
  triggerPending: {},
  recentlyFired: {},
  triggerRuns: {},
  triggerSkips: {},
  macroSkips: {},

  addLogEntry: (entry) =>
    set((s) => {
      if (s.logPaused) return s;
      const next = [...s.logEntries, { ...entry, id: nextLogEntryId++ }];
      return { logEntries: next.length > 500 ? next.slice(-500) : next };
    }),

  addLogBatch: (entries) =>
    set((s) => {
      if (s.logPaused) return s;
      const stamped = entries.map((e) => ({ ...e, id: nextLogEntryId++ }));
      const next = [...s.logEntries, ...stamped];
      return { logEntries: next.length > 500 ? next.slice(-500) : next };
    }),

  setLogPaused: (logPaused) => set({ logPaused }),

  clearLogEntries: () => set({ logEntries: [] }),

  setLogSubscribed: (logSubscribed) => set({ logSubscribed }),

  setMacroProgress: (p) =>
    set((s) => ({
      macroProgress: { ...s.macroProgress, ...p },
    })),

  resetMacroProgress: () => set({
    macroProgress: { ...INITIAL_MACRO },
    stepErrors: [],
    conditionalResults: [],
    groupResults: [],
    stepSkips: [],
  }),

  clearMacroRuns: () => set({
    runningMacros: {},
    macroProgress: { ...INITIAL_MACRO },
  }),

  addStepError: (e) => set((s) => ({ stepErrors: [...s.stepErrors, e] })),

  addConditionalResult: (r) => set((s) => ({ conditionalResults: [...s.conditionalResults, r] })),

  addGroupResult: (r) => set((s) => ({ groupResults: [...s.groupResults, r] })),

  addStepSkip: (k) => set((s) => ({ stepSkips: [...s.stepSkips, k] })),

  startMacroRun: (macroId) => {
    const s = get();
    set({ runningMacros: { ...s.runningMacros, [macroId]: (s.runningMacros[macroId] ?? 0) + 1 } });
    if (Object.keys(s.runningMacros).length > 0) {
      return;
    }
    set({
      stepErrors: [],
      conditionalResults: [],
      groupResults: [],
      stepSkips: [],
      macroStartedAt: Date.now(),
      macroProgress: { macroId, stepIndex: null, totalSteps: null, status: "running", activeStepPath: [] },
    });
  },

  finishMacroRun: (macroId, status, error) => {
    const s = get();
    const runningMacros = { ...s.runningMacros };
    const remaining = (runningMacros[macroId] ?? 0) - 1;
    if (remaining > 0) runningMacros[macroId] = remaining;
    else delete runningMacros[macroId];
    const lastRun: MacroLastRun = {
      macroId,
      startedAt: s.macroStartedAt,
      completedAt: Date.now(),
      duration: s.macroStartedAt ? Date.now() - s.macroStartedAt : 0,
      status,
      stepErrors: [...s.stepErrors],
      conditionalResults: [...s.conditionalResults],
      groupResults: [...s.groupResults],
      stepSkips: [...s.stepSkips],
      error,
    };
    set({ lastRun, runningMacros });
  },

  setTriggerPending: (triggerId, pending) =>
    set((s) => {
      const next = { ...s.triggerPending };
      if (pending) {
        next[triggerId] = pending;
      } else {
        delete next[triggerId];
      }
      return { triggerPending: next };
    }),

  setTriggerFired: (triggerId, fired) =>
    set((s) => {
      const next = { ...s.recentlyFired };
      if (fired) {
        next[triggerId] = Date.now();
      } else {
        delete next[triggerId];
      }
      return { recentlyFired: next };
    }),

  setTriggerRun: (triggerId, run) =>
    set((s) => ({ triggerRuns: { ...s.triggerRuns, [triggerId]: run } })),

  // Replaces the map wholesale rather than merging: this is a re-read of the
  // server's own list, so a trigger missing from it no longer exists and its
  // record must go with it.
  setTriggerRuns: (runs) => set({ triggerRuns: runs }),

  setTriggerSkip: (triggerId, note) =>
    set((s) => ({ triggerSkips: withNote(s.triggerSkips, triggerId, note) })),

  setMacroSkip: (macroId, note) =>
    set((s) => ({ macroSkips: withNote(s.macroSkips, macroId, note) })),

  clearSkips: () => set({ triggerSkips: {}, macroSkips: {} }),
}));

/** `notes` with `id`'s note set, or removed for null; the same object when
 *  there was nothing to remove, so a subscriber is not told of a change. */
function withNote(
  notes: Record<string, SkipNote>, id: string, note: SkipNote | null,
): Record<string, SkipNote> {
  if (note) return { ...notes, [id]: note };
  if (!(id in notes)) return notes;
  const next = { ...notes };
  delete next[id];
  return next;
}
