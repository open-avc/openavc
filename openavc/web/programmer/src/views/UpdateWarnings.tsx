import type { ReactNode } from "react";
import { AlertTriangle } from "lucide-react";
import type { UpdateStatus } from "../api/restClient";
import { cardStyle } from "./updatesStyles";

/** True when a card below takes the place of "You're up to date". */
export function hasUpdateWarning(status: UpdateStatus | null): boolean {
  return !!status?.deferred_version || !!status?.last_update_version;
}

function WarningCard({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div style={{ ...cardStyle, marginBottom: "var(--space-xl)", borderColor: "var(--color-warning)", display: "flex", alignItems: "center", gap: "var(--space-md)" }}>
      <AlertTriangle size={20} style={{ color: "var(--color-warning)", flexShrink: 0 }} />
      <div>
        <div style={{ fontWeight: 500, fontSize: "var(--font-size-sm)" }}>{title}</div>
        <div style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 2 }}>{children}</div>
      </div>
    </div>
  );
}

/**
 * The cards that say this system is not running what was meant to be
 * installed. Everything else on the Updates page reads healthy in both cases.
 */
export function UpdateWarnings({ status, currentVersion }: { status: UpdateStatus | null; currentVersion: string }) {
  // An update the pre-start helper could not apply. The install still works,
  // so without this the box looks fine while running the version somebody
  // was trying to leave.
  const deferredVersion = status?.deferred_version ?? "";
  const deferredAttempts = status?.deferred_attempts ?? 0;
  const deferredReason = status?.deferred_reason ?? "";
  // Something other than an update put an older version on this system.
  const lastUpdateVersion = status?.last_update_version ?? "";
  return (
    <>
      {!!deferredVersion && (
        <WarningCard title={"Update to v" + deferredVersion + " has not been installed"}>
          {status?.deferred_final
            ? "It has stopped " + deferredAttempts + " times, most recently because it " + deferredReason + ". Nothing will try again on its own, so install it again once that is sorted out."
            : "The last attempt stopped because it " + deferredReason + ". It will try again the next time this system restarts."}
        </WarningCard>
      )}
      {!!lastUpdateVersion && (
        <WarningCard title={"This system is running v" + currentVersion + ", but its last update installed v" + lastUpdateVersion}>
          Something other than an update installed the older version, such as an older installer run by hand. If that was not intended, check for updates and install the latest. This notice clears after the next update.
        </WarningCard>
      )}
    </>
  );
}
