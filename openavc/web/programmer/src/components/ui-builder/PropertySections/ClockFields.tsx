/**
 * A clock's Basic fields: what it shows, in what format, what a countdown or
 * an elapsed timer counts from, and the time zone.
 *
 * A countdown's state key is stored as the element's `show.value` binding
 * (the same slot the Bindings tab edits), not a field of its own; an older
 * project's `start_key` is still read for it until a key is picked.
 */
import type { UIElement } from "../../../api/types";
import { VariableKeyPicker } from "../../shared/VariableKeyPicker";
import { FieldHint, FieldRow, type FieldProps } from "./FieldRow";
import { numOrUndefined } from "./numericField";

/** The format each mode uses while the field is empty. */
const DEFAULT_FORMATS: Record<string, string> = {
  time: "h:mm A",
  date: "MMM D, YYYY",
  datetime: "MMM D, YYYY h:mm A",
  countdown: "HH:mm:ss",
  elapsed: "HH:mm:ss",
  meeting: "mm:ss",
};

export function ClockFields({ element, onChange }: FieldProps) {
  return (
    <>
      <FieldRow label="Mode">
        <select value={element.clock_mode || "time"} onChange={(e) => onChange({ clock_mode: e.target.value, format: "" })} style={{ flex: 1 }}>
          <option value="time">Time</option>
          <option value="date">Date</option>
          <option value="datetime">Date + Time</option>
          <option value="countdown">Countdown</option>
          <option value="elapsed">Elapsed</option>
          <option value="meeting">Meeting Timer</option>
        </select>
      </FieldRow>
      <FieldRow label="Format">
        <input value={element.format || ""} onChange={(e) => onChange({ format: e.target.value })} placeholder={DEFAULT_FORMATS[element.clock_mode || "time"] || "h:mm A"} style={{ flex: 1 }} />
      </FieldRow>
      <div style={{ fontSize: 10, color: "var(--text-muted)", lineHeight: 1.5, padding: "0 4px", marginTop: -4 }}>
        <strong>Tokens:</strong>{" "}
        h/hh (12hr) &middot; H/HH (24hr) &middot; mm (min) &middot; ss (sec) &middot; A/a (AM/pm)
        &middot; D/DD (day) &middot; M/MM (month #) &middot; MMM (Jan) &middot; MMMM (January)
        &middot; YYYY (year) &middot; ddd (Mon) &middot; dddd (Monday)
      </div>
      <ModeFields element={element} onChange={onChange} />
      <FieldRow label="Timezone">
        <input
          value={element.timezone || ""}
          onChange={(e) => onChange({ timezone: e.target.value || undefined })}
          placeholder="America/New_York"
          style={{ flex: 1, fontSize: 11 }}
        />
      </FieldRow>
      <FieldHint>
        Leave blank for local time. Uses IANA timezone names.
      </FieldHint>
    </>
  );
}

/** What the chosen mode counts to, from or for; nothing for a clock that just tells the time. */
function ModeFields({ element, onChange }: FieldProps) {
  switch (element.clock_mode) {
    case "countdown":
      return <CountdownFields element={element} onChange={onChange} />;
    case "elapsed":
      return <ElapsedFields element={element} onChange={onChange} />;
    case "meeting":
      return (
        <FieldRow label="Duration">
          <input type="number" value={element.duration_minutes ?? ""} onChange={(e) => onChange({ duration_minutes: numOrUndefined(e.target.value) })} placeholder="60" min={1} style={{ flex: 1 }} />
          <span style={{ fontSize: 10, color: "var(--text-muted)" }}>min</span>
        </FieldRow>
      );
    default:
      return null;
  }
}

function CountdownFields({ element, onChange }: FieldProps) {
  return (
    <>
      <FieldRow label="Target Time">
        <input
          type="datetime-local"
          value={element.target_time || ""}
          onChange={(e) => onChange({ target_time: e.target.value || undefined })}
          style={{ flex: 1, fontSize: 11 }}
        />
      </FieldRow>
      <FieldRow label="State Key">
        <VariableKeyPicker
          value={countdownKey(element)}
          onChange={(key) => onChange(withCountdownKey(element, key))}
          showDeviceState
          placeholder="Select a state key (ISO datetime)..."
          style={{ flex: 1 }}
        />
      </FieldRow>
      <FieldHint>
        Set a fixed target time, or bind to a state key containing an ISO datetime. State key takes priority.
      </FieldHint>
    </>
  );
}

/** The key a countdown counts down to: its value binding's, else an older start key. */
function countdownKey(element: UIElement): string {
  return ((element.bindings as { show?: { value?: { key?: string } } })?.show?.value?.key)
    || (element.start_key || "");
}

/**
 * The patch that makes `key` the countdown's target. A key replaces the fixed
 * target time; no key drops the value binding (and `show` with it once empty)
 * and the older start key.
 */
function withCountdownKey(element: UIElement, key: string): Partial<UIElement> {
  const bindings = (element.bindings || {}) as Record<string, unknown>;
  const show = (bindings.show || {}) as Record<string, unknown>;
  if (key) {
    return { bindings: { ...bindings, show: { ...show, value: { source: "state", key } } }, target_time: undefined };
  }
  const { value: _v, ...restShow } = show;
  const nextBindings: Record<string, unknown> = { ...bindings };
  if (Object.keys(restShow).length > 0) nextBindings.show = restShow;
  else delete nextBindings.show;
  return { bindings: nextBindings, start_key: undefined };
}

function ElapsedFields({ element, onChange }: FieldProps) {
  return (
    <>
      <FieldRow label="Start Key">
        <VariableKeyPicker
          value={element.start_key || ""}
          onChange={(key) => onChange({ start_key: key || undefined })}
          showDeviceState
          placeholder="Select a state key (ISO datetime)..."
          style={{ flex: 1 }}
        />
      </FieldRow>
      <FieldHint>
        State key containing the start time (ISO datetime). Timer shows time elapsed since that value.
      </FieldHint>
    </>
  );
}
