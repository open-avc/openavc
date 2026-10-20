// "Driver update available: v1.5.0 (installed v1.4.3)" under a device's driver
// name, with the same Update the Installed Drivers list and Browse Community
// offer. Renders nothing while the catalog is unknown (offline, still loading)
// or when the driver is current.
import { useEffect, useState } from "react";
import { useDriverBuilderStore } from "../../store/driverBuilderStore";
import { useConnectionStore } from "../../store/connectionStore";
import { parseApiError } from "../../api/errors";
import { InlineError } from "../../components/shared/InlineError";
import { driverUpdate } from "../../components/shared/catalogUpdates";
import { UpdateAvailableLine } from "../../components/shared/CatalogUpdateControls";

export function DriverUpdateLine({
  driverId,
  onUpdated,
}: {
  driverId: string;
  /** The device was rebuilt on the new driver: its commands and info may differ. */
  onUpdated: () => void;
}) {
  const communityDrivers = useDriverBuilderStore((s) => s.communityDrivers);
  const installedDrivers = useDriverBuilderStore((s) => s.installedDrivers);
  const updating = useDriverBuilderStore((s) => s.updatingDriverIds.has(driverId));
  const runningVersion = useConnectionStore(
    (s) => String(s.liveState["system.version"] ?? ""),
  );
  const [error, setError] = useState<string | null>(null);
  const [updatedTo, setUpdatedTo] = useState<string | null>(null);

  useEffect(() => {
    // The catalog once per session from here, not once per device opened: on a
    // network with no route to GitHub every attempt waits out a timeout.
    // Installed Drivers and Browse Community fetch it whenever they open.
    const store = useDriverBuilderStore.getState();
    if (!store.communityLoaded && !store.communityLoading) store.loadCommunityDrivers();
    store.loadInstalledDrivers();
  }, []);

  useEffect(() => {
    setError(null);
    setUpdatedTo(null);
  }, [driverId]);

  const update = driverUpdate(
    installedDrivers.find((d) => d.id === driverId),
    communityDrivers.find((d) => d.id === driverId),
    runningVersion,
  );

  const handleUpdate = async () => {
    if (!update) return;
    setError(null);
    setUpdatedTo(null);
    try {
      await useDriverBuilderStore
        .getState()
        .updateDriver(update.id, update.fileUrl, update.minPlatformVersion);
      setUpdatedTo(update.version);
      onUpdated();
    } catch (e) {
      setError(parseApiError(e));
    }
  };

  if (!update && !error && !updatedTo) return null;
  return (
    <div style={{ marginLeft: 22, marginTop: "var(--space-xs)" }}>
      {update ? (
        <UpdateAvailableLine
          update={update}
          prefix="Driver update available"
          updating={updating}
          onUpdate={handleUpdate}
        />
      ) : updatedTo ? (
        <div style={{ fontSize: "var(--font-size-sm)", color: "var(--color-success)" }}>
          Driver updated to v{updatedTo}.
        </div>
      ) : null}
      <InlineError
        message={error ? `Update failed: ${error}` : null}
        onDismiss={() => setError(null)}
        style={{ marginTop: "var(--space-xs)" }}
      />
    </div>
  );
}
