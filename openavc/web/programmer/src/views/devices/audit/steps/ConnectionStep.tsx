import { useEffect, useMemo, useState } from "react";
import { Loader2 } from "lucide-react";
import * as audit from "../../../../api/auditClient";
import * as driversApi from "../../../../api/driverClient";
import { parseApiError } from "../../../../api/errors";
import type { DriverInfo } from "../../../../api/types";
import { useAuditStore } from "../../../../store/auditStore";
import {
  ConfigFieldInputs,
  driverSerialCapable,
  primaryNetworkTransport as networkTransport,
} from "../../DeviceDialogs";
import { coerceConfigValue } from "../../deviceConfigCoerce";
import {
  buildTableValue,
  existingRows,
  TableRowsEditor,
  type ColumnDef,
  type TableRow,
} from "../../ConfigTableEditor";
import { currentRun, displayBytes, previewStageLabel } from "../auditHelpers";
import { BackButton, ErrorLine } from "../auditParts";
import { buttonStyle, headingStyle, hintStyle, labelStyle, panelStyle, spinStyle } from "../auditStyles";

/** The connection fields the audit sets itself: the driver connects to the
 *  audited address over the network, never a serial port or a bridge. */
const NOT_ASKED = new Set([
  "host", "transport", "bridge", "bridge_port", "usb_serial", "baudrate", "bytesize", "parity",
  "stopbits", "flow_control", "ir_codes",
]);

/** The config values a form starts from: the driver's defaults, then the
 *  audited address, on the driver's network transport. Every value is a
 *  string, as the shared fields expect. */
function startingValues(driver: DriverInfo | undefined, address: string): Record<string, string> {
  const values: Record<string, string> = {};
  for (const [key, value] of Object.entries(driver?.default_config ?? {})) {
    if (value == null) continue;
    values[key] = typeof value === "object" ? JSON.stringify(value, null, 2) : String(value);
  }
  return networkValues(values, driver, address);
}

function networkValues(
  values: Record<string, string>,
  driver: DriverInfo | undefined,
  address: string,
): Record<string, string> {
  const next: Record<string, string> = { ...values, host: address };
  if (driverSerialCapable(driver)) {
    next.transport = networkTransport(driver);
    // A serial port name is no network port.
    if (next.port && /[A-Za-z]/.test(next.port)) next.port = "";
  }
  return next;
}

/** The driver's table fields (a list of objects, a chain of units), each with
 *  its columns and what a row is called. */
function tableFields(driver: DriverInfo | undefined): Record<string, { columns: Record<string, ColumnDef>; rowLabel: string; label: string; help: string }> {
  const out: Record<string, { columns: Record<string, ColumnDef>; rowLabel: string; label: string; help: string }> = {};
  for (const [key, spec] of Object.entries((driver?.config_schema ?? {}) as Record<string, Record<string, unknown>>)) {
    if (spec?.type !== "table") continue;
    out[key] = {
      columns: (spec.columns ?? {}) as Record<string, ColumnDef>,
      rowLabel: String(spec.row_label || "row"),
      label: String(spec.label || key),
      help: spec.help ? String(spec.help) : "",
    };
  }
  return out;
}

/** The rows each table field starts from, read out of the form's values
 *  (a table's default or saved value arrives there as JSON). */
function startingTables(driver: DriverInfo | undefined, values: Record<string, string>): Record<string, TableRow[]> {
  const out: Record<string, TableRow[]> = {};
  for (const [key, field] of Object.entries(tableFields(driver))) {
    let value: unknown = [];
    try {
      value = values[key] ? JSON.parse(values[key]) : [];
    } catch {
      value = [];
    }
    out[key] = existingRows(value, Object.keys(field.columns));
  }
  return out;
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
  const [tables, setTables] = useState<Record<string, TableRow[]>>({});
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
        const start = startingValues(info, address);
        setValues(start);
        setTables(startingTables(info, start));
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
  const tableSpecs = tableFields(driverInfo);
  const fieldKeys = Object.keys(schema).filter((k) => !NOT_ASKED.has(k) && !(k in tableSpecs));
  const savedDevice = saved.find((s) => s.device_id === useSaved);

  function applySaved(device: audit.AuditSavedSettings, info: DriverInfo | undefined) {
    let next = startingValues(info, address);
    for (const [key, value] of Object.entries(device.config)) {
      if (value == null) continue;
      next[key] = typeof value === "object" ? JSON.stringify(value, null, 2) : String(value);
    }
    // Its saved host may be a name for this address; the audit's is the one used.
    next = networkValues(next, info, address);
    setValues(next);
    setTables(startingTables(info, next));
    setUseSaved(device.device_id);
  }

  if (!session || !run) return null;
  const connection = run.connection;

  /** Save the settings (which also builds the preview). True when saved. */
  const save = async (): Promise<boolean> => {
    setError("");
    setBusy(true);
    try {
      const config: Record<string, unknown> = {};
      for (const [key, field] of Object.entries(tableSpecs)) {
        const built = buildTableValue(tables[key] ?? [], field.columns, field.rowLabel);
        if ("error" in built) {
          setError(`${field.label}: ${built.error}`);
          return false;
        }
        config[key] = built.rows;
      }
      for (const [key, raw] of Object.entries(values)) {
        if (key in tableSpecs) continue;
        if (raw === "" && key !== "usb_serial") continue;
        const spec = schema[key] ?? {};
        const result = coerceConfigValue(raw, String(spec.type || ""), spec.secret === true);
        if (!result.ok) {
          setError(`${String(spec.label || key)}: ${result.error}`);
          return false;
        }
        config[key] = result.value;
      }
      const { session: next } = await audit.setAuditConnection(
        session.session_id,
        config,
        useSaved || null,
      );
      useAuditStore.getState().setSession(next);
      return true;
    } catch (e) {
      setError(parseApiError(e));
      return false;
    } finally {
      setBusy(false);
    }
  };

  /** Continue with the settings on the screen, saved first. */
  const proceed = async () => {
    if (await save()) useAuditStore.getState().setStep("listen");
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
                  const start = startingValues(driverInfo, address);
                  setValues(start);
                  setTables(startingTables(driverInfo, start));
                }
              }}
            />
            Use {saved[0].name}'s saved settings
          </label>
          {savedDevice && savedDevice.secrets_set.length > 0 && (
            <div style={hintStyle}>
              {savedSecretsText(savedDevice.secrets_set.map((k) => String(schema[k]?.label || k)))}
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
          <ConfigFieldInputs
            configKeys={fieldKeys}
            driverInfo={driverInfo}
            configValues={values}
            setConfigValues={setValues}
          />
          {Object.entries(tableSpecs).map(([key, field]) => (
            <div key={key} style={{ ...panelStyle, marginTop: "var(--space-md)" }}>
              <div style={labelStyle}>{field.label}</div>
              {field.help && <div style={{ ...hintStyle, marginTop: 0 }}>{field.help}</div>}
              <div style={{ ...hintStyle, marginTop: 0, marginBottom: "var(--space-sm)" }}>
                List what this device has. The driver uses only the rows listed here.
              </div>
              <TableRowsEditor
                columns={field.columns}
                rowLabel={field.rowLabel}
                rows={tables[key] ?? []}
                onChange={(rows) => setTables((prev) => ({ ...prev, [key]: rows }))}
              />
            </div>
          ))}
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
          style={buttonStyle("muted", busy || !loaded)}
        >
          {busy && <Loader2 size={14} style={spinStyle} />}
          Save and show what connecting sends
        </button>
      </div>

      {connection && <PreviewPanel connection={connection} />}

      <div style={{ marginTop: "var(--space-lg)", display: "flex", gap: "var(--space-sm)" }}>
        {/* Another driver can be chosen until this one has connected; after
            that, Test another driver on the report is the way. */}
        <BackButton to={run?.started_at ? "listen" : "driver"} disabled={busy} />
        <button
          type="button"
          onClick={() => void proceed()}
          disabled={busy || !loaded}
          style={buttonStyle("primary", busy || !loaded)}
        >
          Continue
        </button>
      </div>
    </div>
  );
}

/** The saved secrets a paused device's settings keep, named by their labels. */
function savedSecretsText(labels: string[]): string {
  const named =
    labels.length <= 1 ? labels.join("") : `${labels.slice(0, -1).join(", ")} and ${labels[labels.length - 1]}`;
  return labels.length === 1
    ? `Its saved ${named} is used unless you type a new one here.`
    : `Its saved ${named} are used unless you type new ones here.`;
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
