import { lazy, Suspense } from "react";
import { useAuditStore } from "../../../store/auditStore";

const DeviceAuditWizard = lazy(() =>
  import("./DeviceAuditWizard").then((m) => ({ default: m.DeviceAuditWizard })),
);

/**
 * Mounts the Device Audit wizard when an entry point opens it
 * (`useAuditStore.getState().openWizard()`), from whichever screen.
 */
export function DeviceAuditHost() {
  const open = useAuditStore((s) => s.open);
  if (!open) return null;
  return (
    <Suspense fallback={null}>
      <DeviceAuditWizard />
    </Suspense>
  );
}
