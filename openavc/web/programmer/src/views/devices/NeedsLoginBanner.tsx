import { useState } from "react";
import { KeyRound } from "lucide-react";
import type { DriverParamDef } from "../../api/types";
import { parseApiError } from "../../api/errors";
import { paramLabel } from "../../components/shared/paramLabel";
import { driverLoginDefaults, saveDeviceLogins } from "./deviceLogins";

/**
 * The device page in place of the Offline banner when the device waits for a
 * login this system does not have (offline reason `login_missing`): a box for
 * each setting that waits, and the driver's default where it ships one. A
 * save adds the device again with the login, so there is no Reconnect step.
 *
 * `fields` comes from the server (the device info's `needs_login`), which is
 * the only side that knows which marked settings have nothing saved.
 */
export function NeedsLoginBanner({
  deviceId,
  fields,
  driverInfo,
  onSaved,
}: {
  deviceId: string;
  fields: string[];
  driverInfo: Record<string, unknown> | undefined;
  onSaved: () => void;
}) {
  const [values, setValues] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const schema = (driverInfo?.config_schema ?? {}) as Record<string, Partial<DriverParamDef>>;
  const defaults = driverLoginDefaults(driverInfo, fields);
  const typed: Record<string, string> = {};
  for (const field of fields) {
    if (values[field]) typed[field] = values[field];
  }
  const accent = "var(--color-warning, #f59e0b)";

  const save = async (logins: Record<string, string>) => {
    if (Object.keys(logins).length === 0) return;
    setSaving(true);
    setError("");
    try {
      await saveDeviceLogins(deviceId, logins);
      setValues({});
      onSaved();
    } catch (e) {
      setError(parseApiError(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div
      data-testid="needs-login-banner"
      style={{
        padding: "var(--space-md)",
        borderRadius: "var(--border-radius)",
        marginBottom: "var(--space-md)",
        background: "rgba(245, 158, 11, 0.1)",
        border: "2px solid rgba(245, 158, 11, 0.4)",
        display: "flex",
        gap: "var(--space-sm)",
        alignItems: "flex-start",
      }}
    >
      <KeyRound size={18} style={{ color: accent, flexShrink: 0, marginTop: 2 }} />
      <form
        style={{ flex: 1, minWidth: 0 }}
        onSubmit={(e) => {
          e.preventDefault();
          void save(typed);
        }}
      >
        <div style={{ fontWeight: 600, color: accent, fontSize: "var(--font-size-md)" }}>
          Needs login
        </div>
        <div style={{ fontSize: "var(--font-size-sm)", marginTop: 2 }}>
          No login for this device is saved on this system. Saving it here connects the device.
        </div>
        <div
          style={{
            display: "flex",
            flexWrap: "wrap",
            gap: "var(--space-md)",
            marginTop: "var(--space-sm)",
            alignItems: "flex-end",
          }}
        >
          {fields.map((field) => {
            const label = paramLabel(field, schema[field]);
            return (
              <label
                key={field}
                style={{ display: "flex", flexDirection: "column", gap: 2, fontSize: "var(--font-size-sm)", minWidth: 180 }}
              >
                <span style={{ color: "var(--text-secondary)" }}>{label}</span>
                <input
                  type="password"
                  autoComplete="new-password"
                  data-1p-ignore
                  data-lpignore="true"
                  data-bwignore
                  aria-label={label}
                  value={values[field] ?? ""}
                  disabled={saving}
                  onChange={(e) => setValues((v) => ({ ...v, [field]: e.target.value }))}
                  style={{ width: "100%" }}
                />
              </label>
            );
          })}
          <button
            type="submit"
            disabled={saving || Object.keys(typed).length === 0}
            style={{
              padding: "var(--space-xs) var(--space-md)",
              borderRadius: "var(--border-radius)",
              background: "var(--accent-bg)",
              color: "var(--text-on-accent)",
              fontSize: "var(--font-size-sm)",
              opacity: saving || Object.keys(typed).length === 0 ? 0.6 : 1,
            }}
          >
            {saving ? "Saving..." : "Save login"}
          </button>
          {Object.keys(defaults).length > 0 && (
            <button
              type="button"
              disabled={saving}
              onClick={() => void save(defaults)}
              style={{
                padding: "var(--space-xs) var(--space-md)",
                borderRadius: "var(--border-radius)",
                background: "var(--bg-hover)",
                fontSize: "var(--font-size-sm)",
                opacity: saving ? 0.6 : 1,
              }}
            >
              Use the driver's default
            </button>
          )}
        </div>
        {error && (
          <div role="alert" style={{ fontSize: "var(--font-size-sm)", color: "var(--color-error)", marginTop: "var(--space-xs)" }}>
            {error}
          </div>
        )}
      </form>
    </div>
  );
}
