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
 * `project.reloaded` WebSocket event, or local force-reload). Dismissals
 * are remembered per project-revision so the modal doesn't reappear on
 * every state-snapshot diff.
 */
export function MissingDriversWatcher() {
  const devices = useProjectStore((s) => s.project?.devices);
  const revision = useProjectStore((s) => s.revision);
  const liveState = useConnectionStore((s) => s.liveState);

  const [missing, setMissing] = useState<MissingDriver[] | null>(null);
  const [open, setOpen] = useState(false);
  const [dismissedRevision, setDismissedRevision] = useState<number | null>(null);

  // Quick check from local state to avoid an API call when no orphans exist.
  const orphanCount = devices?.reduce((count, dev) => {
    return count + (liveState[`device.${dev.id}.orphaned`] ? 1 : 0);
  }, 0) ?? 0;

  useEffect(() => {
    if (orphanCount === 0) {
      setMissing(null);
      return;
    }
    if (revision !== null && dismissedRevision === revision) {
      return;
    }
    let cancelled = false;
    listMissingDrivers()
      .then((data) => {
        if (cancelled) return;
        if (data.length > 0) {
          setMissing(data);
          setOpen(true);
        }
      })
      .catch((e) => {
        // Non-fatal — banner stays visible on the orphaned device cards
        console.warn("Failed to fetch missing drivers", e);
      });
    return () => {
      cancelled = true;
    };
  }, [orphanCount, revision, dismissedRevision]);

  if (!open || !missing) return null;

  return (
    <MissingDriversModal
      missing={missing}
      onClose={() => {
        setOpen(false);
        setDismissedRevision(revision);
      }}
      onInstalled={() => {
        setOpen(false);
        setDismissedRevision(revision);
      }}
    />
  );
}
