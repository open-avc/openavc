import { useEffect, useState } from "react";

/** A value only once it has stopped moving for `delayMs`.
 *
 * For work that should follow a live number but must not run once per step of
 * it: a controller registers its children in a burst, and re-fetching on each
 * registration is a fetch per child. */
export function useSettled<T>(value: T, delayMs: number): T {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    if (value === settled) return;
    const timer = setTimeout(() => setSettled(value), delayMs);
    return () => clearTimeout(timer);
  }, [value, settled, delayMs]);
  return settled;
}
