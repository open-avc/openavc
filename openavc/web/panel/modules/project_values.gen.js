// GENERATED FILE - DO NOT EDIT.
// Rendered from the project models (openavc/core/project_loader.py).
// Regenerate with:  python -m openavc.core.project_values_gen
// A test compares this file against a fresh render, so hand edits fail CI.
//
// What a project value may hold and what a blank one is, by the model and the
// field the project schema declares it on: `default` is what the schema stores
// for a blank one, `min` and `max` the range it takes (both ends included),
// `integer` that it refuses a fraction, `finite` that it refuses a value that
// is no finite number (NaN or infinity). For text, `max_length` is the most
// characters it takes and `digits` that it takes the digits 0 to 9 and nothing
// else. A value outside its range or shape fails the whole project save.
// `blank_from` says what a blank one draws when another setting decides it:
// that setting's value names it (null: that value draws none), and a value not
// named, a blank one included, reads as the one `otherwise` names. A key inside a field (`style.cell_size`) with
// `zero_is_blank` reads a 0 as a blank one, the control drawing its own size,
// never as a size of 0. A fixed-choice field lists its `words`, and any other
// word, a blank one included, reads as the one `otherwise` names (null: as
// nothing). Such a word is kept as written, never refused.

export const PROJECT_VALUES = {
  "DriverDependency": {
    "source": {
      "default": "",
      "words": [
        "builtin",
        "community",
        "unknown",
        ""
      ],
      "otherwise": "unknown"
    }
  },
  "Layout": {
    "orientation": {
      "default": "landscape",
      "words": [
        "landscape",
        "portrait"
      ],
      "otherwise": null
    }
  },
  "MacroConfig": {
    "overlap": {
      "default": "allow",
      "words": [
        "skip",
        "queue",
        "allow"
      ],
      "otherwise": "allow"
    },
    "cooldown_seconds": {
      "default": 0,
      "min": 0,
      "finite": true,
      "integer": false
    }
  },
  "MacroStep": {
    "on_timeout": {
      "default": null,
      "words": [
        "fail",
        "continue"
      ],
      "otherwise": "fail"
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
  "PageBackground": {
    "image_size": {
      "default": "cover",
      "words": [
        "cover",
        "contain",
        "stretch"
      ],
      "otherwise": "cover"
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
  "PluginDependency": {
    "source": {
      "default": "",
      "words": [
        "community",
        "unknown",
        ""
      ],
      "otherwise": "unknown"
    }
  },
  "TriggerConfig": {
    "type": {
      "words": [
        "schedule",
        "state_change",
        "event",
        "startup"
      ],
      "otherwise": null
    },
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
    },
    "overlap": {
      "default": "skip",
      "words": [
        "skip",
        "queue",
        "allow"
      ],
      "otherwise": "skip"
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
  "UIPage": {
    "render_mode": {
      "default": "elements",
      "words": [
        "elements",
        "custom"
      ],
      "otherwise": "elements"
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
      "default": "",
      "max_length": 6,
      "digits": true
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
