// GENERATED FILE - DO NOT EDIT.
// Rendered from the project models (openavc/core/project_loader.py).
// Regenerate with:  python -m openavc.core.project_values_gen
// A test compares this file against a fresh render, so hand edits fail CI.
//
// What a project value may hold and what a blank one is, by the model and the
// field the project schema declares it on: `default` is what the schema stores
// for a blank one, `min` and `max` the range it takes (both ends included),
// `integer` that it refuses a fraction, `finite` that it refuses a value that
// is no finite number (NaN or infinity). A value outside its range fails the
// whole project save. `blank_from` says what a blank one draws when another
// setting decides it: that setting's value names it (null: that value draws
// none), and a value not named, a blank one included, reads as the one
// `otherwise` names. A key inside a field (`style.cell_size`) with
// `zero_is_blank` reads a 0 as a blank one, the control drawing its own size,
// never as a size of 0.

/** One project value: what a blank one is, and the range it may hold. */
export interface ProjectValue {
  readonly default?: string | number | boolean | null;
  readonly min?: number;
  readonly max?: number;
  readonly integer?: boolean;
  readonly finite?: boolean;
  readonly blank_from?: Readonly<Record<string, Readonly<Record<string, string | null>>>>;
  readonly otherwise?: string;
  readonly zero_is_blank?: boolean;
}

export const PROJECT_VALUES = {
  "MacroConfig": {
    "cooldown_seconds": {
      "default": 0,
      "min": 0,
      "finite": true,
      "integer": false
    }
  },
  "MonitorConfig": {
    "display_decimals": {
      "default": null,
      "min": 0,
      "max": 20,
      "integer": true
    }
  },
  "OverlayConfig": {
    "width": {
      "default": null,
      "min": 5,
      "max": 100,
      "integer": false
    },
    "height": {
      "default": null,
      "min": 5,
      "max": 100,
      "integer": false
    }
  },
  "Placement": {
    "x": {
      "default": 0.0,
      "finite": true,
      "integer": false
    },
    "y": {
      "default": 0.0,
      "finite": true,
      "integer": false
    },
    "w": {
      "default": 100.0,
      "finite": true,
      "integer": false
    },
    "h": {
      "default": 100.0,
      "finite": true,
      "integer": false
    }
  },
  "TriggerConfig": {
    "delay_seconds": {
      "default": 0,
      "min": 0,
      "finite": true,
      "integer": false
    },
    "debounce_seconds": {
      "default": 0,
      "min": 0,
      "finite": true,
      "integer": false
    },
    "cooldown_seconds": {
      "default": 0,
      "min": 0,
      "finite": true,
      "integer": false
    }
  },
  "UIElement": {
    "display_decimals": {
      "default": null,
      "min": 0,
      "max": 20,
      "integer": true
    },
    "format": {
      "default": null,
      "blank_from": {
        "clock_mode": {
          "time": "h:mm A",
          "date": "MMM D, YYYY",
          "datetime": "MMM D, YYYY h:mm A",
          "countdown": null,
          "elapsed": null,
          "meeting": null
        }
      },
      "otherwise": "time"
    },
    "style.cell_size": {
      "zero_is_blank": true
    }
  },
  "UISettings": {
    "theme": {
      "default": "dark"
    },
    "theme_id": {
      "default": "",
      "blank_from": {
        "theme": {
          "light": "light-modern",
          "dark": "dark-default"
        }
      },
      "otherwise": "dark"
    },
    "accent_color": {
      "default": ""
    },
    "font_family": {
      "default": ""
    },
    "lock_code": {
      "default": ""
    },
    "idle_timeout_seconds": {
      "default": 0,
      "min": 0,
      "max": 86400,
      "integer": true
    },
    "idle_page": {
      "default": "main"
    },
    "page_transition": {
      "default": "none"
    },
    "page_transition_duration": {
      "default": 200,
      "min": 50,
      "max": 1000,
      "integer": true
    },
    "element_entry": {
      "default": "none"
    },
    "element_stagger_ms": {
      "default": 30,
      "min": 10,
      "max": 200,
      "integer": true
    },
    "element_stagger_style": {
      "default": "fade-up"
    },
    "show_error_messages": {
      "default": true
    }
  }
} as const satisfies Record<string, Record<string, ProjectValue>>;
