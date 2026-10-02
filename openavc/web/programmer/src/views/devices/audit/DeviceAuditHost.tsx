import { lazy, Suspense, useEffect } from "react";
import { followedAudit, rememberFollowedAudit, useAuditStore } from "../../../store/auditStore";

const DeviceAuditWizard = lazy(() =>
  import("./DeviceAuditWizard").then((m) => ({ default: m.DeviceAuditWizard })),
);

/**
 * Mounts the Device Audit wizard when an entry point opens it
 * (`useAuditStore.getState().openWizard()`), from whichever screen, and
 * reopens it after a reload on the audit this tab was following, so the
 * reload does not cancel it.
 */
export function DeviceAuditHost() {
  const open = useAuditStore((s) => s.open);

  useEffect(() => {
    const followed = followedAudit();
    if (!followed) return;
    let alive = true;
    import("../../../api/auditClient")
      .then((audit) => audit.getCurrentAudit())
      .then(({ session }) => {
        if (!alive) return;
        if (session && session.session_id === followed && session.status === "active") {
          if (!useAuditStore.getState().open) useAuditStore.getState().openWizard();
        } else {
          rememberFollowedAudit(null);
        }
      })
      .catch(() => {
        /* the server is unreachable: the wizard stays closed */
      });
    return () => {
      alive = false;
    };
  }, []);

  if (!open) return null;
  return (
    <Suspense fallback={null}>
      <DeviceAuditWizard />
    </Suspense>
  );
}
