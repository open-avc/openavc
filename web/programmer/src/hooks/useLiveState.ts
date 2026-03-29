import { useMemo } from "react";
import { useConnectionStore } from "../store/connectionStore";

/**
 * Get a single live state value by key.
 */
export function useLiveState(key: string): unknown {
  return useConnectionStore((s) => s.liveState[key]);
}

/**
 * Get all live state values matching a key prefix.
 * e.g., useLiveStateNamespace("device.projector1") returns
 * { power: "on", input: "hdmi1", ... }
 *
 * Uses a snapshot read + useMemo to avoid returning new objects from
 * a Zustand selector (which causes infinite re-renders in React 19).
 */
export function useLiveStateNamespace(
  prefix: string
): Record<string, unknown> {
  // Subscribe to the full liveState (primitive reference — same object if unchanged)
  const liveState = useConnectionStore((s) => s.liveState);
  return useMemo(() => {
    const result: Record<string, unknown> = {};
    const p = prefix.endsWith(".") ? prefix : prefix + ".";
    for (const [key, value] of Object.entries(liveState)) {
      if (key.startsWith(p)) {
        result[key.slice(p.length)] = value;
      }
    }
    return result;
  }, [liveState, prefix]);
}
