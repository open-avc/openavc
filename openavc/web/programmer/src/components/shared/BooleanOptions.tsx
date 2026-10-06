import { BOOLEAN_OPTIONS } from "./booleanWords";

/**
 * The Yes and No rows of a boolean `<select>`, with the values "true" and
 * "false". A picker adds its own empty row ("(none)", "Select...") in front
 * when it has one.
 */
export function BooleanOptions() {
  return (
    <>
      {BOOLEAN_OPTIONS.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </>
  );
}
