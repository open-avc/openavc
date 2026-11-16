/**
 * The banner on the page of a device whose driver is not installed: it looks
 * the driver up in the community catalog so it can be installed from here, says
 * so when the catalog does not have it, and offers Reassign Driver. Product
 * hardware that is unavailable on this server gets its own wording instead, and
 * a driver whose file is installed but did not load says so in the server's
 * words (`orphan_reason`), with why.
 *
 * A device that is installed this way comes back without a reconnect: the
 * server retries devices waiting for their driver, and `onActivated` reads the
 * device again.
 */
import { useEffect, useState, type ReactNode } from "react";
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

/** What the banner is about: product hardware from another model, a driver
 *  whose file is installed but did not load, or a driver not installed. */
type OrphanKind = "builtIn" | "notLoaded" | "missing";

function orphanKind(builtIn: boolean, loadError?: string | null): OrphanKind {
  if (builtIn) return "builtIn";
  return loadError ? "notLoaded" : "missing";
}

const TITLES: Record<OrphanKind, string> = {
  builtIn: "Product hardware unavailable",
  notLoaded: "Driver Did Not Load",
  missing: "Driver Not Installed",
};

/** The install button's words: a file that did not load is installed again. */
const INSTALL_LABELS: Record<OrphanKind, string> = {
  builtIn: "Install from Community",
  notLoaded: "Install Again from Community",
  missing: "Install from Community",
};

/** What is missing (or installed but not loaded, in the server's sentence). */
const WHAT_IS_MISSING: Record<OrphanKind, (driverId: string, reason?: string) => ReactNode> = {
  builtIn: () => <BuiltInEquipmentNotice />,
  notLoaded: (_driverId, reason) => <span style={{ whiteSpace: "pre-wrap" }}>{reason}</span>,
  missing: (driverId) => <>This device needs the driver "{driverId}" which is not installed.</>,
};

const buttonStyle = {
  padding: "var(--space-xs) var(--space-md)",
  borderRadius: "var(--border-radius)",
  background: "var(--bg-hover)",
  fontSize: "var(--font-size-sm)",
} as const;

export function OrphanBanner({
  driverId,
  reason,
  loadError,
  onReassign,
  onBrowseDrivers,
  onActivated,
}: {
  driverId: string;
  /** The server's sentence for why the device waits (`orphan_reason`). */
  reason?: string;
  /** Set when the driver's file is installed but did not load. */
  loadError?: string | null;
  onReassign: () => void;
  onBrowseDrivers?: () => void;
  onActivated: () => void;
}) {
  const { match, lookupDone, builtIn } = useMissingDriverLookup(driverId);
  const { installing, error, install } = useCatalogInstall(driverId, match, onActivated);
  const notInCatalog = lookupDone && !match && !builtIn;
  const kind = orphanKind(builtIn, loadError);

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
        {TITLES[kind]}
      </div>
      <OrphanMessage kind={kind} driverId={driverId} reason={reason} notInCatalog={notInCatalog} error={error} />
      <div style={{ display: "flex", gap: "var(--space-sm)" }}>
        {match && <InstallButton installing={installing} label={INSTALL_LABELS[kind]} onClick={install} />}
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
  kind,
  driverId,
  reason,
  notInCatalog,
  error,
}: {
  kind: OrphanKind;
  driverId: string;
  reason?: string;
  notInCatalog: boolean;
  error: string | null;
}) {
  return (
    <div style={{ fontSize: "var(--font-size-sm)", marginBottom: "var(--space-md)" }}>
      {WHAT_IS_MISSING[kind](driverId, reason)}
      {notInCatalog && (
        <div style={{ marginTop: "var(--space-xs)", color: "var(--text-muted)", fontSize: "var(--font-size-sm)" }}>
          This driver isn't in the community catalog. Reassign the device to a different
          driver, or upload the driver file from the Drivers tab.
        </div>
      )}
      {error && (
        <div style={{ marginTop: "var(--space-xs)", color: ERROR_RED, fontSize: "var(--font-size-sm)" }}>
          <span style={{ whiteSpace: "pre-wrap" }}>Install failed: {error}</span>
        </div>
      )}
    </div>
  );
}

function InstallButton({
  installing,
  label,
  onClick,
}: {
  installing: boolean;
  label: string;
  onClick: () => void;
}) {
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
      {installing ? "Installing..." : label}
    </button>
  );
}
