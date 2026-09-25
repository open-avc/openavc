import { useEffect, useMemo, useState } from "react";
import { Loader2 } from "lucide-react";
import * as audit from "../../../../api/auditClient";
import * as driversApi from "../../../../api/driverClient";
import { parseApiError } from "../../../../api/errors";
import type { DriverInfo } from "../../../../api/types";
import { useAuditStore } from "../../../../store/auditStore";
import {
  ConfigFieldInputs,
  ConnectionModePicker,
  driverSerialCapable,
  hiddenRawConfigKeys,
} from "../../DeviceDialogs";
import { coerceConfigValue } from "../../deviceConfigCoerce";
import { currentRun, displayBytes, previewStageLabel } from "../auditHelpers";
import { ErrorLine } from "../auditParts";
import { buttonStyle, headingStyle, hintStyle, labelStyle, panelStyle, spinStyle } from "../auditStyles";

/** The config values a form starts from: the driver's defaults, then the
 *  audited address. Every value is a string, as the shared fields expect. */
function startingValues(driver: DriverInfo | undefined, address: string): Record<string, string> {
  const values: Record<string, string> = {};
  for (const [key, value] of Object.entries(driver?.default_config ?? {})) {
    if (value == null) continue;
    values[key] = typeof value === "object" ? JSON.stringify(value, null, 2) : String(value);
  }
  if (address) values.host = address;
  return values;
}

/** Step 4: the driver's connection settings, and what connecting sends. */
export function ConnectionStep() {
  const session = useAuditStore((s) => s.session);
  const run = currentRun(session);
  const driverId = run?.choice.driver_id ?? "";
  const address = session?.target.address ?? "";
  const [drivers, setDrivers] = useState<DriverInfo[]>([]);
  const [saved, setSaved] = useState<audit.AuditSavedSettings[]>([]);
  const [useSaved, setUseSaved] = useState<string>("");
  const [values, setValues] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    if (!session) return;
    let alive = true;
    Promise.all([
      driversApi.listDrivers(),
      audit.getAuditSavedSettings(session.session_id).catch(() => ({ devices: [] })),
    ])
      .then(([list, savedList]) => {
        if (!alive) return;
        setDrivers(list);
        setSaved(savedList.devices);
        const info = list.find((d) => d.id === driverId);
        setValues(startingValues(info, address));
        // A device the audit paused, on this driver: its settings are the
        // ones production dials this device with, so start from them.
        if (savedList.devices.length > 0) applySaved(savedList.devices[0], info);
        setLoaded(true);
      })
      .catch((e) => alive && setError(parseApiError(e)));
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session?.session_id, driverId]);

  const driverInfo = useMemo(() => drivers.find((d) => d.id === driverId), [drivers, driverId]);
  const schema = (driverInfo?.config_schema ?? {}) as Record<string, Record<string, unknown>>;
  const serialCapable = driverSerialCapable(driverInfo);
  const fieldKeys = Object.keys(schema).filter((k) => !hiddenRawConfigKeys(driverInfo).has(k));
  const savedDevice = saved.find((s) => s.device_id === useSaved);

  function applySaved(device: audit.AuditSavedSettings, info: DriverInfo | undefined) {
    const next = startingValues(info, address);
    for (const [key, value] of Object.entries(device.config)) {
      if (value == null) continue;
      next[key] = typeof value === "object" ? JSON.stringify(value, null, 2) : String(value);
    }
    setValues(next);
    setUseSaved(device.device_id);
  }

  if (!session || !run) return null;
  const connection = run.connection;

  const save = async () => {
    setError("");
    setBusy(true);
    try {
      const config: Record<string, unknown> = {};
      for (const [key, raw] of Object.entries(values)) {
        if (raw === "" && key !== "usb_serial") continue;
        const spec = schema[key] ?? {};
        const result = coerceConfigValue(raw, String(spec.type || ""), spec.secret === true);
        if (!result.ok) {
          setError(`${String(spec.label || key)}: ${result.error}`);
          return;
        }
        config[key] = result.value;
      }
      const { session: next } = await audit.setAuditConnection(
        session.session_id,
        config,
        useSaved || null,
      );
      useAuditStore.getState().setSession(next);
    } catch (e) {
      setError(parseApiError(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ maxWidth: 720 }}>
      <h2 style={headingStyle}>Connection</h2>
      <p style={{ ...hintStyle, fontSize: "var(--font-size-sm)", margin: "0 0 var(--space-md)" }}>
        {run.choice.identity.name}
        {run.choice.identity.version ? ` ${run.choice.identity.version}` : ""} connects to{" "}
        {address} with these settings.
      </p>

      {saved.length > 0 && (
        <div style={{ ...panelStyle, marginBottom: "var(--space-md)", fontSize: "var(--font-size-sm)" }}>
          <label style={{ display: "flex", gap: "var(--space-sm)", alignItems: "center" }}>
            <input
              type="checkbox"
              checked={!!useSaved}
              onChange={(e) => {
                if (e.target.checked) applySaved(saved[0], driverInfo);
                else {
                  setUseSaved("");
                  setValues(startingValues(driverInfo, address));
                }
              }}
            />
            Use {saved[0].name}'s saved settings
          </label>
          {savedDevice && savedDevice.secrets_set.length > 0 && (
            <div style={hintStyle}>
              Its saved {savedDevice.secrets_set.join(" and ")} {savedDevice.secrets_set.length === 1 ? "is" : "are"} used
              unless you type a new one here.
            </div>
          )}
        </div>
      )}

      {!loaded ? (
        <div style={{ display: "flex", gap: "var(--space-sm)", alignItems: "center" }}>
          <Loader2 size={14} style={spinStyle} /> Loading the driver's settings
        </div>
      ) : (
        <div style={{ fontSize: "var(--font-size-sm)" }}>
          {serialCapable && (
            <ConnectionModePicker
              driverInfo={driverInfo}
              configValues={values}
              setConfigValues={setValues}
              devices={[]}
              drivers={drivers}
              allowBridge={false}
            />
          )}
          <ConfigFieldInputs
            configKeys={fieldKeys}
            driverInfo={driverInfo}
            configValues={values}
            setConfigValues={setValues}
          />
        </div>
      )}

      {error && (
        <div style={{ marginTop: "var(--space-md)" }}>
          <ErrorLine text={error} />
        </div>
      )}

      <div style={{ marginTop: "var(--space-md)", display: "flex", gap: "var(--space-sm)" }}>
        <button
          type="button"
          onClick={() => void save()}
          disabled={busy || !loaded}
          style={buttonStyle(connection ? "muted" : "primary", busy || !loaded)}
        >
          {busy && <Loader2 size={14} style={spinStyle} />}
          {connection ? "Update the preview" : "Show what connecting sends"}
        </button>
      </div>

      {connection && <PreviewPanel connection={connection} />}

      {connection && (
        <div style={{ marginTop: "var(--space-lg)", display: "flex", gap: "var(--space-sm)" }}>
          <button
            type="button"
            onClick={() => useAuditStore.getState().setStep("listen")}
            style={buttonStyle("primary")}
          >
            Continue
          </button>
        </div>
      )}
    </div>
  );
}

function PreviewPanel({ connection }: { connection: audit.AuditConnection }) {
  const preview = connection.preview;
  const stages = ["sign_in", "start_up", "poll", "keep_alive"] as const;
  return (
    <div style={{ ...panelStyle, marginTop: "var(--space-lg)" }}>
      <div style={{ ...labelStyle, marginBottom: "var(--space-sm)" }}>What connecting sends</div>
      {connection.saved_from && (
        <div style={{ ...hintStyle, marginTop: 0, marginBottom: "var(--space-sm)" }}>
          Using {connection.saved_from}'s saved settings.
        </div>
      )}
      {!preview.available ? (
        <div style={{ fontSize: "var(--font-size-sm)" }}>
          {preview.reason} The report records everything it sends.
        </div>
      ) : preview.steps.length === 0 ? (
        <div style={{ fontSize: "var(--font-size-sm)" }}>
          This driver sends nothing when it connects, and does not poll.
        </div>
      ) : (
        stages.map((stage) => {
          const steps = preview.steps.filter((s) => s.stage === stage);
          if (steps.length === 0) return null;
          return (
            <div key={stage} style={{ marginBottom: "var(--space-sm)" }}>
              <div style={{ fontSize: "var(--font-size-sm)", fontWeight: 600 }}>
                {previewStageLabel(stage, preview.poll_interval, preview.keep_alive_interval)}
              </div>
              <ol style={{ margin: "var(--space-xs) 0 0", paddingLeft: "var(--space-lg)" }}>
                {steps.map((s, i) => (
                  <li
                    key={i}
                    style={{
                      fontSize: "var(--font-size-sm)",
                      fontFamily: s.kind === "wait" ? undefined : "var(--font-mono, monospace)",
                      overflowWrap: "anywhere",
                    }}
                  >
                    {s.kind === "wait"
                      ? `Waits for the device to show ${s.pattern}`
                      : s.kind === "send"
                        ? displayBytes(s.text, s.hex)
                        : `${s.method} ${s.target}${s.body ? ` ${s.body}` : ""}`}
                  </li>
                ))}
              </ol>
            </div>
          );
        })
      )}
      <div style={{ ...hintStyle, marginTop: "var(--space-sm)" }}>
        Connecting is what adding the device to a space does. It sends none of the driver's
        commands, though a start-up step can change a setting on the device.
      </div>
    </div>
  );
}
