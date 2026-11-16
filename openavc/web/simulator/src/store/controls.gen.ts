// GENERATED FILE - DO NOT EDIT.
// Rendered from the driver-contract registry (openavc/drivers/spec.py).
// Regenerate with:  python -m openavc.drivers.contract_gen
// A test compares this file against a fresh render, so hand edits fail CI.

// The controls a simulated device's page can declare (a simulator: section's
// controls, a Python simulator's SIMULATOR_INFO["controls"]), one interface per
// type. DynamicControls switches on `type` with a case for each.

/** A power button with a light, for an on/off state key. */
export interface PowerControlDef {
  type: "power";
  /** The state key the control shows and sets. */
  key: string;
  /** The name shown on the control. */
  label?: string;
}

/** A row of buttons, one per option; the one lit is the key's value. */
export interface SelectControlDef {
  type: "select";
  /** The state key the control shows and sets. */
  key: string;
  /** The values the buttons set, each as the state key holds it. */
  options: (string | number | boolean)[];
  /**
   * The text on an option's button, keyed by the option; the option itself
   * when left out.
   */
  labels?: Record<string, string>;
  /** The name shown on the control. */
  label?: string;
}

/** A slider with its value beside it. */
export interface SliderControlDef {
  type: "slider";
  /** The state key the control shows and sets. */
  key: string;
  /** The value at the left end. */
  min: number;
  /** The value at the right end. */
  max: number;
  /** How far one notch moves it. */
  step?: number;
  /** Shown after the value. */
  unit?: string;
  /** The name shown on the control. */
  label?: string;
}

/** An on/off button that shows its label with ON or OFF. */
export interface ToggleControlDef {
  type: "toggle";
  /** The state key the control shows and sets. */
  key: string;
  /** The name shown on the control. */
  label?: string;
}

/** An input by output routing grid. */
export interface MatrixControlDef {
  type: "matrix";
  /** How many inputs, numbered from 1. */
  inputs: number;
  /** How many outputs, numbered from 1. */
  outputs: number;
  /** The state key holding an output's input, with {output} for its number. */
  state_pattern: string;
  /** The name shown on the control. */
  label?: string;
  /** The name of each input, in order. */
  input_labels?: string[];
  /** The name of each output, in order. */
  output_labels?: string[];
}

/** Level meter bars, one per channel. */
export interface MetersControlDef {
  type: "meters";
  /** How many channels, numbered from 1. */
  channels: number;
  /** The state key holding a channel's level, with {ch} for its number. */
  key_pattern: string;
  /** The state key holding a channel's mute, with {ch} for its number. */
  mute_pattern?: string;
  /** The name shown on the control. */
  label?: string;
}

/** Preset buttons, numbered (count) or named (names). */
export interface PresetsControlDef {
  type: "presets";
  /** The state key the control shows and sets. */
  key: string;
  /** How many numbered presets. */
  count?: number;
  /** The name of each preset, in order. */
  names?: string[];
  /** The name shown on the control. */
  label?: string;
}

/** Other controls under one label. */
export interface GroupControlDef {
  type: "group";
  /** The name shown on the control. */
  label?: string;
  controls: ControlDef[];
}

/** A read-only display of the key's value. */
export interface IndicatorControlDef {
  type: "indicator";
  /** The state key the control shows. */
  key: string;
  /** The name shown on the control. */
  label?: string;
  /** The color shown for a value, keyed by the value. */
  color_map?: Record<string, string>;
}

export type ControlDef =
  | PowerControlDef
  | SelectControlDef
  | SliderControlDef
  | ToggleControlDef
  | MatrixControlDef
  | MetersControlDef
  | PresetsControlDef
  | GroupControlDef
  | IndicatorControlDef;
