/**
 * The banner on the page of a device whose driver is not installed: it looks
 * the driver up in the community catalog so it can be installed from here, says
 * so when the catalog does not have it, and offers Reassign Driver. Product
 * hardware that is unavailable on this server gets its own wording instead.
 *
 * A device that is installed this way comes back without a reconnect: the
 * server retries devices waiting for their driver, and `onActivated` reads the
 * device again.
 */
import { useEffect, useState } from "react";
import * as api from "../../api/restClient";
import { parseApiError } from "../../api/errors";
import { BuiltInEquipmentNotice } from "../../components/shared/BuiltInEquipmentNotice";

const ERROR_RED = "#ef4444";

interface CatalogMatch {
  file_url: string;
  min_platform_version: string | null;
}

/** What the catalog says about the missing driver once it has answered. A
 *  failed lookup reads as no match. */
function useMissingDriverLookup(driverId: string) {
  const [match, setMatch] = useState<CatalogMatch | null>(null);
  const [lookupDone, setLookupDone] = useState(false);
  const [builtIn, setBuiltIn] = useState(false);

  useEffect(() => {
    setMatch(null);
    setBuiltIn(false);
    setLookupDone(false);
    if (!driverId) return;
    let cancelled = false;
    api
      .listMissingDrivers()
      .then((items) => {
        if (cancelled) return;
        const entry = items.find((m) => m.driver_id === driverId);
        setBuiltIn(Boolean(entry?.built_in_equipment));
        if (entry?.community_match) {
          setMatch({
            file_url: entry.community_match.file_url,
            min_platform_version: entry.community_match.min_platform_version,
          });
        }
        setLookupDone(true);
      })
      .catch(() => setLookupDone(true));
    return () => {
      cancelled = true;
    };
  }, [driverId]);

  return { match, lookupDone, builtIn };
}

/** Installing the catalog's copy of the driver, and why it failed. */
function useCatalogInstall(driverId: string, match: CatalogMatch | null, onActivated: () => void) {
  const [installing, setInstalling] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const install = async () => {
    if (!match) return;
    setInstalling(true);
    setError(null);
    try {
      await api.installCommunityDriver(driverId, match.file_url, match.min_platform_version || undefined);
      onActivated();
    } catch (e) {
      setError(parseApiError(e));
    } finally {
      setInstalling(false);
    }
  };

  return { installing, error, install };
}

const buttonStyle = {
  padding: "var(--space-xs) var(--space-md)",
  borderRadius: "var(--border-radius)",
  background: "var(--bg-hover)",
  fontSize: "var(--font-size-sm)",
} as const;

export function OrphanBanner({
  driverId,
  onReassign,
  onBrowseDrivers,
  onActivated,
}: {
  driverId: string;
  onReassign: () => void;
  onBrowseDrivers?: () => void;
  onActivated: () => void;
}) {
  const { match, lookupDone, builtIn } = useMissingDriverLookup(driverId);
  const { installing, error, install } = useCatalogInstall(driverId, match, onActivated);
  const notInCatalog = lookupDone && !match && !builtIn;

  return (
    <div
      style={{
        padding: "var(--space-md)",
        borderRadius: "var(--border-radius)",
        marginBottom: "var(--space-md)",
        background: "rgba(239, 68, 68, 0.1)",
        border: "2px solid rgba(239, 68, 68, 0.4)",
      }}
    >
      <div style={{ fontWeight: 600, marginBottom: "var(--space-sm)", color: ERROR_RED, fontSize: "var(--font-size-md)" }}>
        {builtIn ? "Product hardware unavailable" : "Driver Not Installed"}
      </div>
      <OrphanMessage driverId={driverId} builtIn={builtIn} notInCatalog={notInCatalog} error={error} />
      <div style={{ display: "flex", gap: "var(--space-sm)" }}>
        {match && <InstallButton installing={installing} onClick={install} />}
        {notInCatalog && onBrowseDrivers && (
          <button onClick={onBrowseDrivers} style={buttonStyle}>
            Browse Drivers
          </button>
        )}
        <button onClick={onReassign} style={buttonStyle}>
          Reassign Driver
        </button>
      </div>
    </div>
  );
}

/** What is missing, whether the catalog has it, and why an install failed. */
function OrphanMessage({
  driverId,
  builtIn,
  notInCatalog,
  error,
}: {
  driverId: string;
  builtIn: boolean;
  notInCatalog: boolean;
  error: string | null;
}) {
  return (
    <div style={{ fontSize: "var(--font-size-sm)", marginBottom: "var(--space-md)" }}>
      {builtIn ? <BuiltInEquipmentNotice /> : <>This device needs the driver "{driverId}" which is not installed.</>}
      {notInCatalog && (
        <div style={{ marginTop: "var(--space-xs)", color: "var(--text-muted)", fontSize: "var(--font-size-sm)" }}>
          This driver isn't in the community catalog. Reassign the device to a different
          driver, or upload the driver file from the Drivers tab.
        </div>
      )}
      {error && (
        <div style={{ marginTop: "var(--space-xs)", color: ERROR_RED, fontSize: "var(--font-size-sm)" }}>
          Install failed: {error}
        </div>
      )}
    </div>
  );
}

function InstallButton({ installing, onClick }: { installing: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      disabled={installing}
      style={{
        padding: "var(--space-xs) var(--space-md)",
        borderRadius: "var(--border-radius)",
        background: "var(--color-warning, #f59e0b)",
        color: "#000",
        fontSize: "var(--font-size-sm)",
        fontWeight: 500,
        cursor: installing ? "not-allowed" : "pointer",
        opacity: installing ? 0.7 : 1,
      }}
    >
      {installing ? "Installing..." : "Install from Community"}
    </button>
  );
}
