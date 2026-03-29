import { useState } from "react";
import * as api from "../../api/restClient";
import type { DriverInfo } from "../../api/types";

/** Generate a non-clashing default value for a unique device setting. */
function generateUniqueDefault(
  baseDefault: string,
  _key: string,
  _existingDeviceIds: string[],
  deviceId: string,
): string {
  // For unique settings like NDI names, append the device ID suffix
  // to avoid clashes when multiple devices of the same type are added.
  if (!baseDefault) return deviceId;
  return `${baseDefault}-${deviceId}`;
}

export function DeviceSettingsSetupDialog({
  deviceId,
  driverInfo,
  existingDeviceIds,
  onClose,
}: {
  deviceId: string;
  driverInfo: DriverInfo;
  existingDeviceIds: string[];
  onClose: () => void;
}) {
  const deviceSettings = driverInfo.device_settings ?? {};
  const setupKeys = Object.keys(deviceSettings).filter(
    (k) => deviceSettings[k]?.setup === true
  );

  const [values, setValues] = useState<Record<string, string>>(() => {
    const initial: Record<string, string> = {};
    for (const key of setupKeys) {
      const def = deviceSettings[key];
      let defaultVal = String(def?.default ?? "");

      // Generate unique value if needed
      if (def?.unique) {
        defaultVal = generateUniqueDefault(defaultVal, key, existingDeviceIds, deviceId);
      }

      initial[key] = defaultVal;
    }
    return initial;
  });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [results, setResults] = useState<Record<string, { success: boolean; error?: string; pending?: boolean }>>({});

  if (setupKeys.length === 0) {
    // No setup settings — shouldn't be shown
    return null;
  }

  const handleApply = async () => {
    setSaving(true);
    setError("");
    const newResults: Record<string, { success: boolean; error?: string; pending?: boolean }> = {};

    // Coerce all values first
    const coercedValues: Record<string, unknown> = {};
    for (const key of setupKeys) {
      const def = deviceSettings[key];
      const fieldType = String(def?.type ?? "string");
      let coerced: unknown = values[key];
      if (fieldType === "integer") coerced = parseInt(values[key], 10) || 0;
      else if (fieldType === "number") coerced = parseFloat(values[key]) || 0;
      else if (fieldType === "boolean") coerced = values[key] === "true";
      coercedValues[key] = coerced;
    }

    // Try to push each setting immediately
    const failedSettings: Record<string, unknown> = {};
    for (const key of setupKeys) {
      try {
        await api.setDeviceSetting(deviceId, key, coercedValues[key]);
        newResults[key] = { success: true };
      } catch (e) {
        const errStr = String(e);
        const isConnectionError = errStr.includes("503") || errStr.includes("not connected");
        if (isConnectionError) {
          failedSettings[key] = coercedValues[key];
          newResults[key] = { success: true, pending: true };
        } else {
          newResults[key] = { success: false, error: errStr };
        }
      }
    }

    // Store any connection-failed settings as pending
    if (Object.keys(failedSettings).length > 0) {
      try {
        await api.storePendingSettings(deviceId, failedSettings);
      } catch (e) {
        setError(`Failed to queue settings: ${e}`);
        setSaving(false);
        return;
      }
    }

    setResults(newResults);
    const anyHardFailed = Object.values(newResults).some((r) => !r.success && !r.pending);
    const anyPending = Object.values(newResults).some((r) => r.pending);

    if (anyHardFailed) {
      setError("Some settings failed to save. You can retry or skip.");
    } else if (anyPending) {
      // All settings either applied or queued — close with a brief delay so user sees the message
      setError("");
      setTimeout(onClose, 1500);
    } else {
      onClose();
    }
    setSaving(false);
  };

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.6)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 1001,
      }}
      onClick={onClose}
    >
      <div
        style={{
          background: "var(--bg-elevated)",
          borderRadius: "var(--border-radius)",
          padding: "var(--space-xl)",
          width: 480,
          maxHeight: "80vh",
          overflow: "auto",
          boxShadow: "var(--shadow-lg)",
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <h3 style={{ fontSize: "var(--font-size-lg)", marginBottom: "var(--space-sm)" }}>
          Device Setup
        </h3>
        <p style={{ fontSize: "var(--font-size-sm)", color: "var(--text-muted)", marginBottom: "var(--space-lg)" }}>
          Configure initial device settings. These will be pushed to the device.
        </p>

        {error && (
          <div
            style={{
              background: "rgba(244,67,54,0.15)",
              color: "var(--color-error)",
              padding: "var(--space-sm) var(--space-md)",
              borderRadius: "var(--border-radius)",
              marginBottom: "var(--space-md)",
              fontSize: "var(--font-size-sm)",
            }}
          >
            {error}
          </div>
        )}

        {setupKeys.map((key) => {
          const def = deviceSettings[key];
          const label = String(def?.label ?? key);
          const help = String(def?.help ?? "");
          const fieldType = String(def?.type ?? "string");
          const enumValues = def?.values as string[] | undefined;
          const result = results[key];

          return (
            <div key={key} style={{ marginBottom: "var(--space-md)" }}>
              <label
                style={{
                  display: "block",
                  fontSize: "var(--font-size-sm)",
                  color: "var(--text-secondary)",
                  marginBottom: "var(--space-xs)",
                }}
              >
                {label}
              </label>
              {fieldType === "boolean" ? (
                <select
                  value={values[key] ?? "false"}
                  onChange={(e) => setValues((v) => ({ ...v, [key]: e.target.value }))}
                  style={{ width: "100%" }}
                >
                  <option value="true">Yes</option>
                  <option value="false">No</option>
                </select>
              ) : fieldType === "enum" && enumValues ? (
                <select
                  value={values[key] ?? ""}
                  onChange={(e) => setValues((v) => ({ ...v, [key]: e.target.value }))}
                  style={{ width: "100%" }}
                >
                  {enumValues.map((ev) => (
                    <option key={ev} value={ev}>{ev}</option>
                  ))}
                </select>
              ) : (
                <input
                  value={values[key] ?? ""}
                  onChange={(e) => setValues((v) => ({ ...v, [key]: e.target.value }))}
                  type={fieldType === "integer" || fieldType === "number" ? "number" : "text"}
                  style={{ width: "100%" }}
                />
              )}
              {help && (
                <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>{help}</div>
              )}
              {result && (
                <div
                  style={{
                    fontSize: 11,
                    marginTop: 2,
                    color: result.success ? (result.pending ? "var(--accent)" : "var(--color-success)") : "var(--color-error)",
                  }}
                >
                  {result.success
                    ? result.pending
                      ? "Queued — will be applied when device connects"
                      : "Saved"
                    : result.error}
                </div>
              )}
            </div>
          );
        })}

        <div
          style={{
            display: "flex",
            justifyContent: "flex-end",
            gap: "var(--space-sm)",
            marginTop: "var(--space-lg)",
          }}
        >
          <button
            onClick={onClose}
            style={{
              padding: "var(--space-sm) var(--space-lg)",
              borderRadius: "var(--border-radius)",
              background: "var(--bg-hover)",
            }}
          >
            Skip
          </button>
          <button
            onClick={handleApply}
            disabled={saving}
            style={{
              padding: "var(--space-sm) var(--space-lg)",
              borderRadius: "var(--border-radius)",
              background: "var(--accent)",
              color: "var(--text-on-accent)",
              opacity: saving ? 0.6 : 1,
            }}
          >
            {saving ? "Applying..." : "Apply Settings"}
          </button>
        </div>
      </div>
    </div>
  );
}

/** Check if a driver has any setup settings. */
export function hasDriverSetupSettings(driverInfo: DriverInfo | undefined): boolean {
  if (!driverInfo?.device_settings) return false;
  return Object.values(driverInfo.device_settings).some((s) => s.setup === true);
}
