/**
 * The shapes a surface config is made of.
 *
 * Only the ones more than one editor in this directory needs: the layout as
 * the plugin declares it, and the records the config file stores for a key, a
 * dial and a touch zone. A shape a single editor owns stays in that editor's
 * file.
 */
import type { ButtonBindings } from "../../shared/ButtonBindingEditor";

export interface SurfaceLayout {
  type: "grid" | "strip" | "custom" | "matrix";
  rows?: number;
  columns?: number;
  key_size_px?: number;
  key_spacing_px?: number;
  width_px?: number;
  height_px?: number;
  controls?: ControlDef[];
  supports_pages?: boolean;
  max_pages?: number;
  // Device-backed surfaces that can also be reached over the network
  // (plugin serves ext/network/scan + ext/network/test and reads a
  // top-level network_decks config array).
  network?: boolean;
  rows_label?: string;
  columns_label?: string;
  rows_state_pattern?: string;
  columns_state_pattern?: string;
  cell_type?: string;
  cell_state_pattern?: string;
  presets?: boolean;
  // Device-backed surfaces (declared by the plugin): the editor renders only
  // real units (connected hardware or virtual units). With none present, a
  // connect / add-virtual empty state shows instead of the static layout.
  requires_device?: boolean;
  device_label?: string;
  virtual_models?: string[];
}

interface ControlDef {
  id?: string;
  type: "button" | "fader" | "encoder" | "indicator" | "route";
  position?: [number, number]; // [row, col] for grid
  x?: number;
  y?: number;
  width?: number;
  height?: number;
  has_display?: boolean;
  min?: number;
  max?: number;
  label?: string;
  detents?: number;
}

export interface MeterConfig {
  min?: number;
  max?: number;
  color?: string;
  thresholds?: { above?: number; color?: string }[];
}

// Conditional styling shared by zones and info items (same schema as key
// feedback; the runtime resolves all of them through one path).
export interface DisplayFeedback {
  key?: string;
  condition?: { equals?: string };
  style_active?: { bg_color?: string; text_color?: string };
  style_inactive?: { bg_color?: string; text_color?: string };
}

export interface ButtonAssignment {
  index?: number;
  page?: number;
  label?: string;
  icon?: string;
  bg_color?: string;
  text_color?: string;
  // Live display: label/value from state, optional meter bar
  label_source?: string;
  value_source?: string;
  unit?: string;
  meter?: MeterConfig | boolean;
  // Same binding format as web UI buttons
  bindings?: ButtonBindings;
}

export interface DialAdjust {
  key?: string;
  step?: number;
  min?: number;
  max?: number;
  fader?: boolean;
}

export interface DialAssignment {
  index?: number;
  label?: string;
  icon?: string;
  unit?: string;
  meter?: MeterConfig | boolean;
  adjust?: DialAdjust;
  cw?: Record<string, unknown>[];
  ccw?: Record<string, unknown>[];
  press?: Record<string, unknown>[];
  long_press?: Record<string, unknown>[];
  hold_threshold_ms?: number;
  pressed_adjust?: DialAdjust;
  pressed_cw?: Record<string, unknown>[];
  pressed_ccw?: Record<string, unknown>[];
  // The dial's strip zone is its touch surface
  touch?: Record<string, unknown>[];
  long_touch?: Record<string, unknown>[];
  fader?: boolean;
}

export interface TouchZone {
  label?: string;
  label_source?: string;
  value_source?: string;
  unit?: string;
  icon?: string;
  meter?: MeterConfig | boolean;
  feedback?: DisplayFeedback;
  bg_color?: string;
  text_color?: string;
  x?: number;
  w?: number;
  touch?: Record<string, unknown>[];
  long_touch?: Record<string, unknown>[];
  drag_adjust?: DialAdjust;
}

export type WorkbenchSelection =
  | { kind: "deck" }
  | { kind: "key"; index: number }
  | { kind: "dial"; index: number }
  | { kind: "strip"; zone: number | null }
  | { kind: "screen" };
