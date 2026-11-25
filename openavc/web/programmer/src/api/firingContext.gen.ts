// GENERATED FILE - DO NOT EDIT.
// Rendered from the firing context's field table
// (openavc/core/value_resolver.py FIRING_CONTEXTS).
// Regenerate with:  python -m openavc.core.firing_context_gen
// A test compares this file against a fresh render, so hand edits fail CI.
//
// What each producer of a macro run puts in the run's firing context, which a
// step reads as `$trigger.<field>` and a condition as the key
// `trigger.<field>`. Keyed by the producer: a trigger's own type, `press` for
// a control that runs the macro, `fire_now` for Fire now in the trigger list.
// `name` is what ran the macro, `fields` what the macro can read, and
// `carries_payload` that the context also holds every field of the event's
// own payload, under the names its sender gave them. A macro run by a Run
// Macro step reads the context of the macro that ran it; a run started any
// other way has none.

/** One field of a firing context, read as `trigger.<name>`. */
export interface ContextField {
  readonly name: string;
  readonly description: string;
}

/** What one producer of a macro run puts in the run's firing context. */
export interface FiringContext {
  readonly name: string;
  readonly fields: readonly ContextField[];
  readonly carries_payload: boolean;
}

export const FIRING_CONTEXTS = {
  "state_change": {
    "name": "A state change trigger",
    "fields": [
      {
        "name": "key",
        "description": "The state key that changed"
      },
      {
        "name": "old_value",
        "description": "Its value before the change"
      },
      {
        "name": "new_value",
        "description": "Its value after the change"
      }
    ],
    "carries_payload": false
  },
  "event": {
    "name": "An event trigger",
    "fields": [
      {
        "name": "event",
        "description": "The name of the event that fired"
      }
    ],
    "carries_payload": true
  },
  "schedule": {
    "name": "A schedule trigger",
    "fields": [
      {
        "name": "trigger_type",
        "description": "The kind of trigger, \"schedule\" or \"startup\""
      },
      {
        "name": "cron",
        "description": "The trigger's schedule, as a cron expression"
      }
    ],
    "carries_payload": false
  },
  "startup": {
    "name": "A startup trigger",
    "fields": [
      {
        "name": "trigger_type",
        "description": "The kind of trigger, \"schedule\" or \"startup\""
      }
    ],
    "carries_payload": false
  },
  "press": {
    "name": "A control that runs the macro",
    "fields": [
      {
        "name": "value",
        "description": "The value the user just set: a slider's position, a select's choice, the digits entered"
      },
      {
        "name": "input",
        "description": "The routed input number, on a matrix route"
      },
      {
        "name": "output",
        "description": "The routed or muted output number, on a matrix route or mute"
      },
      {
        "name": "mute",
        "description": "true when muting, false when unmuting, on a matrix mute"
      },
      {
        "name": "tag",
        "description": "The Tag you gave the control in the UI Builder"
      },
      {
        "name": "element",
        "description": "The control's ID"
      }
    ],
    "carries_payload": false
  },
  "fire_now": {
    "name": "Fire now in the trigger list",
    "fields": [],
    "carries_payload": false
  }
} as const satisfies Readonly<Record<string, FiringContext>>;
