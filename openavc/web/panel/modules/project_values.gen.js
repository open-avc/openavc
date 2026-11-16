// GENERATED FILE - DO NOT EDIT.
// Rendered from the project models (openavc/core/project_loader.py).
// Regenerate with:  python -m openavc.core.project_values_gen
// A test compares this file against a fresh render, so hand edits fail CI.
//
// What a project value may hold and what a blank one is, by the model and the
// field the project schema declares it on: `default` is what the schema stores
// for a blank one, `min` and `max` the range it takes (both ends included),
// `integer` that it refuses a fraction. A value outside its range fails the
// whole project save. `blank_from` says what a blank one draws when another
// setting decides it: that setting's value names it (null: that value draws
// none), and a value not named, a blank one included, reads as the one
// `otherwise` names.

export const PROJECT_VALUES = {
  "MonitorConfig": {
    "display_decimals": {
      "default": null,
      "min": 0,
      "max": 20,
      "integer": true
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
};
