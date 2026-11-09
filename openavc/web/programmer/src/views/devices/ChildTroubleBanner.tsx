/**
 * The device is fine; some of what it manages is not. Shown on the device's
 * page while the device itself is up (when it is offline its own banner is the
 * story, and every port under it reads down for that one reason), from
 * `scanChildTrouble` and `troubleSummary` in childPresence.ts, which read the
 * live state the way the Child Entities panel's rows and tab counts do.
 *
 * Quieter than the Offline banner on purpose (one border, no icon column of its
 * own): the room still works, and this says "go and look at that endpoint",
 * not "nothing responds". It is on the page rather than only in the Child
 * Entities panel because a wedged endpoint shown as one word in a collapsed
 * panel is easy to miss while hunting a fault somewhere else.
 */
import { useMemo } from "react";
import { AlertTriangle } from "lucide-react";
import { scanChildTrouble, troubleSummary, type ChildTypeInfo } from "./childPresence";

export function ChildTroubleBanner({
  deviceId,
  connected,
  childTypes,
  liveState,
}: {
  deviceId: string;
  connected: boolean;
  /** The driver's declared child types (DRIVER_INFO.child_entity_types). */
  childTypes: unknown;
  liveState: Record<string, unknown>;
}) {
  const trouble = useMemo(
    () => troubleSummary(scanChildTrouble(
      liveState, deviceId, (childTypes ?? {}) as Record<string, ChildTypeInfo>,
    )),
    [liveState, deviceId, childTypes],
  );
  if (!connected || !trouble) return null;
  return (
    <div
      data-testid="child-trouble-banner"
      style={{
        padding: "var(--space-sm) var(--space-md)",
        borderRadius: "var(--border-radius)",
        marginBottom: "var(--space-md)",
        background: "rgba(245, 158, 11, 0.08)",
        border: "1px solid rgba(245, 158, 11, 0.35)",
        display: "flex",
        gap: "var(--space-sm)",
        alignItems: "flex-start",
      }}
    >
      <AlertTriangle size={16} style={{ color: "var(--color-warning, #f59e0b)", flexShrink: 0, marginTop: 2 }} />
      <div>
        <div style={{ fontSize: "var(--font-size-sm)", fontWeight: 600 }}>{trouble.headline}</div>
        <div style={{ fontSize: "var(--font-size-sm)", color: "var(--text-secondary)", marginTop: 2 }}>
          {trouble.names}
        </div>
      </div>
    </div>
  );
}
