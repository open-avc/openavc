import { useEffect, useState } from "react";
import { useConnectionStore } from "../../store/connectionStore";
import { useProjectStore } from "../../store/projectStore";
import { listMissingDrivers, type MissingDriver } from "../../api/deviceClient";
import { MissingDriversModal } from "./MissingDriversModal";

/**
 * Detects orphaned devices (driver not installed) and prompts the user to
 * install matching community drivers in one click.
 *
 * Triggers a check whenever the project revision changes (initial load,
 * `project.reloaded` WebSocket event, or local force-reload).
 *
 * A dismissal is remembered against WHAT WAS MISSING, not against the project
 * revision it was dismissed at. Keying it on the revision meant every edit
 * re-opened the dialog, because every edit bumps the revision -- and since a
 * modal traps focus, it took the focus out of whatever field was being edited
 * at the time. Keying it on the driver list instead means "no thanks" sticks
 * for the rest of the session, while a driver that goes missing later still
 * asks.
 */
/** Which drivers are missing, order-independent. */
function missingKey(missing: MissingDriver[]): string {
  return missing.map((m) => m.driver_id).sort().join(",");
}

export function MissingDriversWatcher() {
  const devices = useProjectStore((s) => s.project?.devices);
  const revision = useProjectStore((s) => s.revision);
  const liveState = useConnectionStore((s) => s.liveState);

  const [missing, setMissing] = useState<MissingDriver[] | null>(null);
  const [open, setOpen] = useState(false);
  const [dismissedKey, setDismissedKey] = useState<string | null>(null);

  // Quick check from local state to avoid an API call when no orphans exist.
  const orphanCount = devices?.reduce((count, dev) => {
    return count + (liveState[`device.${dev.id}.orphaned`] ? 1 : 0);
  }, 0) ?? 0;

  useEffect(() => {
    if (orphanCount === 0) {
      setMissing(null);
      return;
    }
    let cancelled = false;
    listMissingDrivers()
      .then((data) => {
        if (cancelled) return;
        if (data.length > 0) {
          setMissing(data);
          if (missingKey(data) !== dismissedKey) setOpen(true);
        }
      })
      .catch((e) => {
        // Non-fatal — banner stays visible on the orphaned device cards
        console.warn("Failed to fetch missing drivers", e);
      });
    return () => {
      cancelled = true;
    };
  }, [orphanCount, revision, dismissedKey]);

  if (!open || !missing) return null;

  return (
    <MissingDriversModal
      missing={missing}
      onClose={() => {
        setOpen(false);
        setDismissedKey(missingKey(missing));
      }}
      onInstalled={() => {
        setOpen(false);
        setDismissedKey(missingKey(missing));
      }}
    />
  );
}
