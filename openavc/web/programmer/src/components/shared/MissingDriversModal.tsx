import { useState } from "react";
import { Dialog } from "./Dialog";
import type { MissingDriver } from "../../api/deviceClient";
import { installMissingDrivers } from "../../api/deviceClient";
import { showSuccess, showError } from "../../store/toastStore";

interface MissingDriversModalProps {
  missing: MissingDriver[];
  onClose: () => void;
  onInstalled: () => void;
}

export function MissingDriversModal({ missing, onClose, onInstalled }: MissingDriversModalProps) {
  const installable = missing.filter((m) => m.community_match !== null);
  const uncatalogued = missing.filter((m) => m.community_match === null);

  const [selected, setSelected] = useState<Set<string>>(
    () => new Set(installable.map((m) => m.driver_id))
  );
  const [installing, setInstalling] = useState(false);
  const [result, setResult] = useState<{
    installed: string[];
    failed: { driver_id: string; error: string }[];
    activated: string[];
  } | null>(null);

  const toggle = (driverId: string) => {
    const next = new Set(selected);
    if (next.has(driverId)) next.delete(driverId);
    else next.add(driverId);
    setSelected(next);
  };

  const handleInstall = async () => {
    if (selected.size === 0) return;
    setInstalling(true);
    try {
      const res = await installMissingDrivers(Array.from(selected));
      setResult({
        installed: res.installed,
        failed: res.failed,
        activated: res.activated_devices,
      });
      if (res.installed.length > 0) {
        showSuccess(
          `Installed ${res.installed.length} driver${res.installed.length === 1 ? "" : "s"}` +
            (res.activated_devices.length > 0
              ? `, activated ${res.activated_devices.length} device${res.activated_devices.length === 1 ? "" : "s"}`
              : "")
        );
      }
      if (res.failed.length === 0) {
        onInstalled();
      }
    } catch (e) {
      showError(`Install failed: ${String(e)}`);
    } finally {
      setInstalling(false);
    }
  };

  // Result view (after install)
  if (result) {
    const allDone = result.failed.length === 0;
    return (
      <Dialog title={allDone ? "Drivers installed" : "Install complete (with errors)"} onClose={onClose}>
        {result.installed.length > 0 && (
          <div style={{ marginBottom: "var(--space-md)" }}>
            <div style={{ fontWeight: 600, marginBottom: "var(--space-xs)" }}>
              Installed ({result.installed.length})
            </div>
            <ul style={{ margin: 0, paddingLeft: 20, fontSize: "var(--font-size-sm)" }}>
              {result.installed.map((id) => (
                <li key={id}>
                  <code>{id}</code>
                </li>
              ))}
            </ul>
          </div>
        )}
        {result.activated.length > 0 && (
          <div style={{ marginBottom: "var(--space-md)" }}>
            <div style={{ fontWeight: 600, marginBottom: "var(--space-xs)", color: "var(--color-success)" }}>
              Activated devices ({result.activated.length})
            </div>
            <ul style={{ margin: 0, paddingLeft: 20, fontSize: "var(--font-size-sm)" }}>
              {result.activated.map((id) => (
                <li key={id}>
                  <code>{id}</code>
                </li>
              ))}
            </ul>
          </div>
        )}
        {result.failed.length > 0 && (
          <div style={{ marginBottom: "var(--space-md)" }}>
            <div style={{ fontWeight: 600, marginBottom: "var(--space-xs)", color: "var(--color-error)" }}>
              Failed ({result.failed.length})
            </div>
            <ul style={{ margin: 0, paddingLeft: 20, fontSize: "var(--font-size-sm)" }}>
              {result.failed.map((f) => (
                <li key={f.driver_id}>
                  <code>{f.driver_id}</code>: {f.error}
                </li>
              ))}
            </ul>
          </div>
        )}
        <div style={{ display: "flex", justifyContent: "flex-end", marginTop: "var(--space-lg)" }}>
          <button
            onClick={onClose}
            style={{
              padding: "var(--space-xs) var(--space-md)",
              borderRadius: "var(--border-radius)",
              background: "var(--bg-hover)",
              fontSize: "var(--font-size-sm)",
            }}
          >
            Close
          </button>
        </div>
      </Dialog>
    );
  }

  // Selection view
  const totalDevices = missing.reduce((sum, m) => sum + m.device_ids.length, 0);

  return (
    <Dialog title="Missing drivers" onClose={onClose}>
      <div style={{ fontSize: "var(--font-size-sm)", marginBottom: "var(--space-md)", color: "var(--text-secondary)" }}>
        This project uses {missing.length} driver{missing.length === 1 ? "" : "s"} that{" "}
        {missing.length === 1 ? "isn't" : "aren't"} installed
        {totalDevices > 0 && (
          <>
            , affecting {totalDevices} device{totalDevices === 1 ? "" : "s"}
          </>
        )}
        .
      </div>

      {installable.length > 0 && (
        <div style={{ marginBottom: "var(--space-md)" }}>
          <div style={{ fontWeight: 600, marginBottom: "var(--space-xs)", fontSize: "var(--font-size-sm)" }}>
            Available from community ({installable.length})
          </div>
          <div
            style={{
              maxHeight: 220,
              overflowY: "auto",
              border: "1px solid var(--border-color)",
              borderRadius: "var(--border-radius)",
              padding: "var(--space-xs)",
            }}
          >
            {installable.map((m) => {
              const cm = m.community_match!;
              return (
                <label
                  key={m.driver_id}
                  style={{
                    display: "flex",
                    alignItems: "flex-start",
                    gap: "var(--space-sm)",
                    padding: "var(--space-xs) var(--space-sm)",
                    borderRadius: "var(--border-radius)",
                    cursor: "pointer",
                  }}
                  onMouseEnter={(e) => (e.currentTarget.style.background = "var(--bg-hover)")}
                  onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
                >
                  <input
                    type="checkbox"
                    checked={selected.has(m.driver_id)}
                    onChange={() => toggle(m.driver_id)}
                    style={{ marginTop: 3 }}
                  />
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontSize: "var(--font-size-sm)", fontWeight: 500 }}>
                      {cm.name}
                      {cm.manufacturer && (
                        <span style={{ color: "var(--text-muted)", fontWeight: 400 }}> · {cm.manufacturer}</span>
                      )}
                    </div>
                    <div style={{ fontSize: 11, color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
                      {m.driver_id} · used by {m.device_ids.length} device{m.device_ids.length === 1 ? "" : "s"}
                    </div>
                  </div>
                </label>
              );
            })}
          </div>
        </div>
      )}

      {uncatalogued.length > 0 && (
        <div style={{ marginBottom: "var(--space-md)" }}>
          <div style={{ fontWeight: 600, marginBottom: "var(--space-xs)", fontSize: "var(--font-size-sm)", color: "var(--text-muted)" }}>
            Not in community catalog ({uncatalogued.length})
          </div>
          <div
            style={{
              border: "1px solid var(--border-color)",
              borderRadius: "var(--border-radius)",
              padding: "var(--space-sm)",
              opacity: 0.7,
            }}
          >
            {uncatalogued.map((m) => (
              <div key={m.driver_id} style={{ fontSize: "var(--font-size-sm)", marginBottom: "var(--space-xs)" }}>
                <code style={{ fontSize: 11, fontFamily: "var(--font-mono)" }}>{m.driver_id}</code>{" "}
                <span style={{ color: "var(--text-muted)" }}>
                  · used by {m.device_ids.length} device{m.device_ids.length === 1 ? "" : "s"}
                </span>
              </div>
            ))}
            <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: "var(--space-xs)" }}>
              Reassign these devices to a different driver, or upload the driver file manually from the Drivers tab.
            </div>
          </div>
        </div>
      )}

      <div style={{ display: "flex", justifyContent: "flex-end", gap: "var(--space-sm)", marginTop: "var(--space-lg)" }}>
        <button
          onClick={onClose}
          disabled={installing}
          style={{
            padding: "var(--space-xs) var(--space-md)",
            borderRadius: "var(--border-radius)",
            background: "var(--bg-hover)",
            fontSize: "var(--font-size-sm)",
          }}
        >
          Skip
        </button>
        <button
          onClick={handleInstall}
          disabled={installing || selected.size === 0}
          style={{
            padding: "var(--space-xs) var(--space-md)",
            borderRadius: "var(--border-radius)",
            background: selected.size === 0 ? "var(--bg-hover)" : "var(--color-accent, #4f8cff)",
            color: selected.size === 0 ? "var(--text-muted)" : "#fff",
            fontSize: "var(--font-size-sm)",
            fontWeight: 500,
            cursor: installing || selected.size === 0 ? "not-allowed" : "pointer",
          }}
        >
          {installing
            ? "Installing..."
            : `Install ${selected.size > 0 ? selected.size : ""} driver${selected.size === 1 ? "" : "s"}`}
        </button>
      </div>
    </Dialog>
  );
}
