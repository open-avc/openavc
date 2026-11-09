/**
 * The device is fine; some of what it manages is not. Shown on the device's
 * page while the device itself is up (when it is offline its own banner is the
 * story, and every port under it reads down for that one reason), from
 * `troubleSummary` in childPresence.ts.
 *
 * Quieter than the Offline banner on purpose (one border, no icon column of its
 * own): the room still works, and this says "go and look at that endpoint",
 * not "nothing responds". It is on the page rather than only in the Child
 * Entities panel because a wedged endpoint shown as one word in a collapsed
 * panel is easy to miss while hunting a fault somewhere else.
 */
import { AlertTriangle } from "lucide-react";

export function ChildTroubleBanner({ headline, names }: { headline: string; names: string }) {
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
        <div style={{ fontSize: "var(--font-size-sm)", fontWeight: 600 }}>{headline}</div>
        <div style={{ fontSize: "var(--font-size-sm)", color: "var(--text-secondary)", marginTop: 2 }}>
          {names}
        </div>
      </div>
    </div>
  );
}
