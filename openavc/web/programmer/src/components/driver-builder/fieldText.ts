/** One OSC argument as the editors hold it: its type tag and its value as
 *  text. */
export interface OscArgText {
  type: string;
  value: string;
}

/** Each argument as the driver reads it when it sends: a missing type tag is
 *  a float (`f`), a missing value is empty, and any other value is its text
 *  (a driver file may write `value: 1` as well as `value: "1"`). */
export function oscArgsAsText(args: readonly unknown[] | undefined): OscArgText[] {
  return (args ?? []).map((arg) => {
    const held = typeof arg === "object" && arg !== null ? arg : {};
    const type = "type" in held && typeof held.type === "string" ? held.type : "f";
    const value = "value" in held && held.value != null ? String(held.value) : "";
    return { type, value };
  });
}

/** A table of named values (HTTP headers or query parameters, mDNS TXT
 *  pairs) as the editors hold it: each value as its text, which is how the
 *  driver sends or matches it (a driver file may write `X-Port: 8080`). */
export function valuesAsText(
  values: Readonly<Record<string, unknown>> | undefined,
): Record<string, string> {
  return Object.fromEntries(
    Object.entries(values ?? {}).map(([key, value]) => [key, value == null ? "" : String(value)]),
  );
}
