import { useState, useEffect, useRef, useMemo } from "react";
import { useProjectStore } from "../../store/projectStore";
import * as api from "../../api/restClient";
import type { DeviceConfig, DriverInfo } from "../../api/types";
import { DeviceSettingsSetupDialog, hasDriverSetupSettings } from "../../components/shared/DeviceSettingsSetupDialog";

// --- Typed Config Fields ---

function ConfigFieldInputs({
  configKeys,
  driverInfo,
  configValues,
  setConfigValues,
}: {
  configKeys: string[];
  driverInfo: DriverInfo | undefined;
  configValues: Record<string, string>;
  setConfigValues: React.Dispatch<React.SetStateAction<Record<string, string>>>;
}) {
  return (
    <>
      {configKeys.map((key) => {
        const schema =
          (driverInfo?.config_schema as Record<string, Record<string, unknown>>)?.[key] ?? {};
        const label = String(schema.label || key);
        const description = schema.description ? String(schema.description) : "";
        const fieldType = String(schema.type || "string");
        const values = schema.values as string[] | undefined;
        const isRequired = schema.required === true;
        const defaultVal = schema.default;
        // Build helpful placeholder from key name conventions
        const placeholder = key === "host" ? "192.168.1.100"
          : key === "port" ? "1-65535"
          : key === "username" ? "admin"
          : key === "password" ? "password"
          : key === "community" ? "public"
          : key === "baud_rate" || key === "baudrate" ? "9600"
          : defaultVal != null && defaultVal !== "" ? String(defaultVal)
          : label;

        return (
          <div key={key} style={{ marginBottom: "var(--space-sm)" }}>
            <label
              style={{
                display: "block",
                fontSize: "var(--font-size-sm)",
                color: "var(--text-secondary)",
                marginBottom: "var(--space-xs)",
              }}
            >
              {label}
              {isRequired && (
                <span style={{ color: "var(--error, #f44336)", marginLeft: 2 }}>*</span>
              )}
            </label>
            {fieldType === "boolean" ? (
              <button
                onClick={() =>
                  setConfigValues((v) => ({
                    ...v,
                    [key]: v[key] === "true" ? "false" : "true",
                  }))
                }
                style={{
                  padding: "var(--space-xs) var(--space-md)",
                  borderRadius: "var(--border-radius)",
                  background:
                    configValues[key] === "true"
                      ? "var(--color-success-bg)"
                      : "var(--bg-hover)",
                  color:
                    configValues[key] === "true" ? "var(--color-success)" : "var(--text-secondary)",
                  fontSize: "var(--font-size-sm)",
                }}
              >
                {configValues[key] === "true" ? "Yes" : "No"}
              </button>
            ) : values && values.length > 0 ? (
              <select
                value={configValues[key] ?? ""}
                onChange={(e) =>
                  setConfigValues((v) => ({ ...v, [key]: e.target.value }))
                }
                style={{ width: "100%" }}
              >
                <option value="">Select...</option>
                {values.map((v) => (
                  <option key={v} value={v}>
                    {v}
                  </option>
                ))}
              </select>
            ) : fieldType === "integer" || fieldType === "number" ? (
              <input
                type="number"
                value={configValues[key] ?? ""}
                onChange={(e) =>
                  setConfigValues((v) => ({ ...v, [key]: e.target.value }))
                }
                placeholder={placeholder}
                style={{ width: "100%" }}
              />
            ) : fieldType === "password" ? (
              <input
                type="password"
                value={configValues[key] ?? ""}
                onChange={(e) =>
                  setConfigValues((v) => ({ ...v, [key]: e.target.value }))
                }
                placeholder={placeholder}
                style={{ width: "100%" }}
              />
            ) : (
              <input
                value={configValues[key] ?? ""}
                onChange={(e) =>
                  setConfigValues((v) => ({ ...v, [key]: e.target.value }))
                }
                placeholder={placeholder}
                style={{ width: "100%" }}
              />
            )}
            {description && (
              <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>
                {description}
              </div>
            )}
          </div>
        );
      })}
    </>
  );
}

// --- Searchable Driver Dropdown ---

const CATEGORY_ORDER = ["projector", "display", "audio", "switcher", "camera", "lighting", "control", "utility", "other"];

function DriverSearchSelect({
  drivers,
  value,
  onChange,
}: {
  drivers: DriverInfo[];
  value: string;
  onChange: (driverId: string) => void;
}) {
  const [search, setSearch] = useState("");
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handleClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, []);

  const filtered = useMemo(() => {
    const q = search.toLowerCase();
    return drivers.filter(
      (d) =>
        !q ||
        (d.name || d.id).toLowerCase().includes(q) ||
        (d.manufacturer || "").toLowerCase().includes(q) ||
        (d.category || "").toLowerCase().includes(q)
    );
  }, [drivers, search]);

  const grouped = useMemo(() => {
    const map = new Map<string, DriverInfo[]>();
    for (const d of filtered) {
      const cat = d.category || "other";
      if (!map.has(cat)) map.set(cat, []);
      map.get(cat)!.push(d);
    }
    const sorted = [...map.entries()].sort(
      (a, b) => (CATEGORY_ORDER.indexOf(a[0]) === -1 ? 99 : CATEGORY_ORDER.indexOf(a[0]))
        - (CATEGORY_ORDER.indexOf(b[0]) === -1 ? 99 : CATEGORY_ORDER.indexOf(b[0]))
    );
    return sorted;
  }, [filtered]);

  const selected = drivers.find((d) => d.id === value);

  return (
    <div ref={ref} style={{ position: "relative" }}>
      <input
        value={open ? search : (selected ? (selected.name || selected.id) : "")}
        onChange={(e) => { setSearch(e.target.value); if (!open) setOpen(true); }}
        onFocus={() => { setOpen(true); setSearch(""); }}
        placeholder="Search drivers..."
        style={{ width: "100%" }}
      />
      {open && (
        <div
          style={{
            position: "absolute",
            top: "100%",
            left: 0,
            right: 0,
            maxHeight: 260,
            overflow: "auto",
            background: "var(--bg-elevated)",
            border: "1px solid var(--border-color)",
            borderRadius: "var(--border-radius)",
            zIndex: 10,
            boxShadow: "var(--shadow-md)",
          }}
        >
          {grouped.length === 0 && (
            <div style={{ padding: "var(--space-sm) var(--space-md)", color: "var(--text-muted)", fontSize: "var(--font-size-sm)" }}>
              No drivers found
            </div>
          )}
          {grouped.map(([cat, items]) => (
            <div key={cat}>
              <div
                style={{
                  padding: "var(--space-xs) var(--space-md)",
                  fontSize: 11,
                  color: "var(--text-muted)",
                  textTransform: "uppercase",
                  letterSpacing: "0.5px",
                  background: "var(--bg-surface)",
                  position: "sticky",
                  top: 0,
                }}
              >
                {cat}
              </div>
              {items.map((d) => (
                <div
                  key={d.id}
                  onClick={() => { onChange(d.id); setOpen(false); setSearch(""); }}
                  style={{
                    padding: "var(--space-xs) var(--space-md)",
                    cursor: "pointer",
                    fontSize: "var(--font-size-sm)",
                    background: d.id === value ? "var(--accent-dim)" : "transparent",
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                  }}
                  onMouseEnter={(e) => (e.currentTarget.style.background = d.id === value ? "var(--accent-dim)" : "var(--bg-hover)")}
                  onMouseLeave={(e) => (e.currentTarget.style.background = d.id === value ? "var(--accent-dim)" : "transparent")}
                >
                  <span>{d.name || d.id}</span>
                  {d.manufacturer && (
                    <span style={{ color: "var(--text-muted)", fontSize: 11 }}>{d.manufacturer}</span>
                  )}
                </div>
              ))}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// --- Add Device Dialog ---

export function AddDeviceDialog({
  onClose,
  prefill,
}: {
  onClose: () => void;
  prefill?: DeviceConfig;
}) {
  const project = useProjectStore((s) => s.project);
  const update = useProjectStore((s) => s.update);
  const save = useProjectStore((s) => s.save);

  const [drivers, setDrivers] = useState<DriverInfo[]>([]);
  const [deviceId, setDeviceId] = useState(prefill ? "" : "");
  const [deviceName, setDeviceName] = useState(prefill?.name ? `${prefill.name} (Copy)` : "");
  const [selectedDriver, setSelectedDriver] = useState(prefill?.driver ?? "");
  const [configValues, setConfigValues] = useState<Record<string, string>>(() => {
    if (!prefill) return {};
    // Merge device.config with connection table overrides (host, port, etc.)
    const conn = useProjectStore.getState().project?.connections?.[prefill.id] ?? {};
    const merged = { ...prefill.config, ...conn };
    const vals: Record<string, string> = {};
    for (const [k, v] of Object.entries(merged)) {
      if (v != null && typeof v === "object") {
        vals[k] = JSON.stringify(v);
      } else {
        vals[k] = String(v ?? "");
      }
    }
    return vals;
  });
  const [error, setError] = useState("");
  const [isAdding, setIsAdding] = useState(false);
  const [setupDeviceId, setSetupDeviceId] = useState<string | null>(null);

  useEffect(() => {
    api.listDrivers().then(setDrivers).catch(console.error);
  }, []);

  const driverInfo = drivers.find((d) => d.id === selectedDriver);
  const configKeys = Object.keys((driverInfo?.config_schema ?? {}) as Record<string, unknown>);

  // Check if driver has setup settings
  const hasSetupSettings = useMemo(() => hasDriverSetupSettings(driverInfo), [driverInfo]);

  const handleAdd = async () => {
    if (!deviceId || !selectedDriver) {
      setError("Device ID and driver are required");
      return;
    }
    if (project?.devices.some((d) => d.id === deviceId)) {
      setError("A device with this ID already exists");
      return;
    }

    const config: Record<string, unknown> = {};
    for (const [key, val] of Object.entries(configValues)) {
      if (val === "") continue;
      // Only coerce simple decimal numbers (not hex 0x1A, scientific 1e5, etc.)
      const isSimpleNumber = /^-?\d+(\.\d+)?$/.test(val);
      config[key] = isSimpleNumber ? Number(val) : val;
    }

    const newDevice: DeviceConfig = {
      id: deviceId,
      driver: selectedDriver,
      name: deviceName || deviceId,
      config,
    };

    update({
      devices: [...(project?.devices ?? []), newDevice],
    });

    save();

    // Show setup dialog if driver has setup settings
    if (hasSetupSettings) {
      setIsAdding(true);
      setSetupDeviceId(deviceId);
    } else {
      onClose();
    }
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Add Device"
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.6)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 1000,
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
        <h3 style={{ fontSize: "var(--font-size-lg)", marginBottom: "var(--space-lg)" }}>
          {prefill ? "Duplicate Device" : "Add Device"}
        </h3>

        {error && (
          <div
            style={{
              background: "var(--color-error-bg)",
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

        <div style={{ marginBottom: "var(--space-md)" }}>
          <label
            style={{
              display: "block",
              fontSize: "var(--font-size-sm)",
              color: "var(--text-secondary)",
              marginBottom: "var(--space-xs)",
            }}
          >
            Driver
          </label>
          <DriverSearchSelect
            drivers={drivers}
            value={selectedDriver}
            onChange={(newDriverId) => {
              setSelectedDriver(newDriverId);
              const newDriver = drivers.find((d) => d.id === newDriverId);
              const defaults = newDriver?.default_config ?? {};
              const prefilled: Record<string, string> = {};
              for (const [k, v] of Object.entries(defaults)) {
                if (v !== "" && v != null) prefilled[k] = String(v);
              }
              setConfigValues(prefilled);
            }}
          />
          {driverInfo?.help?.overview && (
            <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4 }}>
              {driverInfo.help.overview}
            </div>
          )}
          {driverInfo?.help?.setup && (
            <div style={{
              fontSize: 11,
              color: "var(--text-secondary)",
              marginTop: 4,
              padding: "var(--space-sm)",
              background: "var(--bg-base)",
              borderRadius: "var(--border-radius)",
              whiteSpace: "pre-line",
            }}>
              {driverInfo.help.setup}
            </div>
          )}
        </div>

        <div style={{ marginBottom: "var(--space-md)" }}>
          <label
            style={{
              display: "block",
              fontSize: "var(--font-size-sm)",
              color: "var(--text-secondary)",
              marginBottom: "var(--space-xs)",
            }}
          >
            Device ID
          </label>
          <input
            value={deviceId}
            onChange={(e) =>
              setDeviceId(e.target.value.replace(/[^a-z0-9_]/gi, "").toLowerCase())
            }
            placeholder="e.g., projector_room_1"
            style={{
              width: "100%",
              borderColor: deviceId && !isAdding && project?.devices.some((d) => d.id === deviceId)
                ? "var(--color-error, #ef4444)" : undefined,
            }}
          />
          <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 3 }}>
            Lowercase letters, numbers, and underscores only.
            {deviceId && (
              <span style={{ marginLeft: 6 }}>
                Your ID: <code style={{ fontFamily: "var(--font-mono)", color: "var(--text-primary)" }}>{deviceId}</code>
                {!isAdding && project?.devices.some((d) => d.id === deviceId) && (
                  <span style={{ color: "var(--color-error, #ef4444)", marginLeft: 6 }}>Already exists</span>
                )}
              </span>
            )}
          </div>
        </div>

        <div style={{ marginBottom: "var(--space-md)" }}>
          <label
            style={{
              display: "block",
              fontSize: "var(--font-size-sm)",
              color: "var(--text-secondary)",
              marginBottom: "var(--space-xs)",
            }}
          >
            Display Name
          </label>
          <input
            value={deviceName}
            onChange={(e) => setDeviceName(e.target.value)}
            placeholder="e.g., Main Projector"
            maxLength={128}
            style={{ width: "100%" }}
          />
        </div>


        {configKeys.length > 0 && (
          <div style={{ marginBottom: "var(--space-md)" }}>
            <div
              style={{
                fontSize: "var(--font-size-sm)",
                color: "var(--text-secondary)",
                marginBottom: "var(--space-sm)",
                textTransform: "uppercase",
                letterSpacing: "0.5px",
              }}
            >
              Connection Settings
            </div>
            <ConfigFieldInputs
              configKeys={configKeys}
              driverInfo={driverInfo}
              configValues={configValues}
              setConfigValues={setConfigValues}
            />
          </div>
        )}

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
            Cancel
          </button>
          <button
            onClick={handleAdd}
            style={{
              padding: "var(--space-sm) var(--space-lg)",
              borderRadius: "var(--border-radius)",
              background: "var(--accent)",
              color: "var(--text-on-accent)",
            }}
          >
            {prefill ? "Duplicate Device" : "Add Device"}
          </button>
        </div>
      </div>

      {setupDeviceId && driverInfo && (
        <DeviceSettingsSetupDialog
          deviceId={setupDeviceId}
          driverInfo={driverInfo}
          existingDeviceIds={(project?.devices ?? []).map((d) => d.id)}
          onClose={onClose}
        />
      )}
    </div>
  );
}

// --- Edit Device Dialog ---

export function EditDeviceDialog({
  device,
  onClose,
  onSaved,
}: {
  device: DeviceConfig;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [drivers, setDrivers] = useState<DriverInfo[]>([]);
  const [deviceName, setDeviceName] = useState(device.name);
  const [selectedDriver, setSelectedDriver] = useState(device.driver);
  const [configValues, setConfigValues] = useState<Record<string, string>>(() => {
    // Merge device.config with connection table overrides (host, port, etc.)
    const conn = useProjectStore.getState().project?.connections?.[device.id] ?? {};
    const merged = { ...device.config, ...conn };
    const vals: Record<string, string> = {};
    for (const [k, v] of Object.entries(merged)) {
      if (v != null && typeof v === "object") {
        vals[k] = JSON.stringify(v);
      } else {
        vals[k] = String(v ?? "");
      }
    }
    return vals;
  });
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    api.listDrivers().then(setDrivers).catch(console.error);
  }, []);

  const driverInfo = drivers.find((d) => d.id === selectedDriver);
  // Show config fields from driver schema if available, otherwise from the device's existing config
  const schemaKeys = Object.keys((driverInfo?.config_schema ?? {}) as Record<string, unknown>);
  const existingKeys = Object.keys(configValues);
  const configKeys = schemaKeys.length > 0 ? schemaKeys : existingKeys;

  // When driver changes, pre-fill config from driver's default_config
  const handleDriverChange = (newDriver: string) => {
    setSelectedDriver(newDriver);
    if (newDriver !== device.driver) {
      const newDriverInfo = drivers.find((d) => d.id === newDriver);
      const defaults = newDriverInfo?.default_config ?? {};
      const prefilled: Record<string, string> = {};
      for (const [k, v] of Object.entries(defaults)) {
        if (v !== "" && v != null) prefilled[k] = String(v);
      }
      setConfigValues(prefilled);
    }
  };

  const handleSave = async () => {
    if (!selectedDriver) {
      setError("Driver is required");
      return;
    }

    setSaving(true);
    setError("");
    try {
      const config: Record<string, unknown> = {};
      const schema = (driverInfo?.config_schema ?? {}) as Record<string, Record<string, unknown>>;
      for (const [key, val] of Object.entries(configValues)) {
        if (val === "") continue;
        const fieldType = String(schema[key]?.type || "");
        if (fieldType === "boolean") {
          config[key] = val === "true";
        } else if (fieldType === "integer" || fieldType === "number" || fieldType === "float") {
          const isSimpleNumber = /^-?\d+(\.\d+)?$/.test(val);
          config[key] = isSimpleNumber ? Number(val) : val;
        } else {
          // Try parsing as JSON for object-type values (command_map, etc.)
          try {
            const parsed = JSON.parse(val);
            if (typeof parsed === "object" && parsed !== null) {
              config[key] = parsed;
            } else {
              const isSimpleNumber = /^-?\d+(\.\d+)?$/.test(val);
              config[key] = isSimpleNumber ? Number(val) : val;
            }
          } catch (_) {
            const isSimpleNumber = /^-?\d+(\.\d+)?$/.test(val);
            config[key] = isSimpleNumber ? Number(val) : val;
          }
        }
      }

      const updateData: Record<string, unknown> = {
        name: deviceName || device.id,
        driver: selectedDriver,
        config,
      };

      await api.updateDevice(device.id, updateData as {
        name?: string;
        driver?: string;
        config?: Record<string, unknown>;
      });
      onSaved();
      onClose();
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Edit Device"
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.6)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 1000,
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
        <h3 style={{ fontSize: "var(--font-size-lg)", marginBottom: "var(--space-lg)" }}>
          Edit Device
        </h3>

        {error && (
          <div
            style={{
              background: "var(--color-error-bg)",
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

        <div style={{ marginBottom: "var(--space-md)" }}>
          <label
            style={{
              display: "block",
              fontSize: "var(--font-size-sm)",
              color: "var(--text-secondary)",
              marginBottom: "var(--space-xs)",
            }}
          >
            Device ID
          </label>
          <input value={device.id} disabled style={{ width: "100%", opacity: 0.6 }} />
          <div
            style={{
              fontSize: "11px",
              color: "var(--text-muted)",
              marginTop: "var(--space-xs)",
            }}
          >
            Device ID cannot be changed after creation
          </div>
        </div>

        <div style={{ marginBottom: "var(--space-md)" }}>
          <label
            style={{
              display: "block",
              fontSize: "var(--font-size-sm)",
              color: "var(--text-secondary)",
              marginBottom: "var(--space-xs)",
            }}
          >
            Driver
          </label>
          <DriverSearchSelect
            drivers={
              // Include current driver if not in the loaded list
              selectedDriver && !drivers.some(d => d.id === selectedDriver)
                ? [...drivers, { id: selectedDriver, name: selectedDriver + (drivers.length === 0 ? " (loading...)" : " (not installed)"), manufacturer: "", category: "other", commands: {}, config_schema: {} }]
                : drivers
            }
            value={selectedDriver}
            onChange={handleDriverChange}
          />
        </div>

        <div style={{ marginBottom: "var(--space-md)" }}>
          <label
            style={{
              display: "block",
              fontSize: "var(--font-size-sm)",
              color: "var(--text-secondary)",
              marginBottom: "var(--space-xs)",
            }}
          >
            Display Name
          </label>
          <input
            value={deviceName}
            onChange={(e) => setDeviceName(e.target.value)}
            placeholder="e.g., Main Projector"
            maxLength={128}
            style={{ width: "100%" }}
          />
        </div>


        {configKeys.length > 0 && (
          <div style={{ marginBottom: "var(--space-md)" }}>
            <div
              style={{
                fontSize: "var(--font-size-sm)",
                color: "var(--text-secondary)",
                marginBottom: "var(--space-sm)",
                textTransform: "uppercase",
                letterSpacing: "0.5px",
              }}
            >
              Connection Settings
            </div>
            <ConfigFieldInputs
              configKeys={configKeys}
              driverInfo={driverInfo}
              configValues={configValues}
              setConfigValues={setConfigValues}
            />
          </div>
        )}

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
            Cancel
          </button>
          <button
            onClick={handleSave}
            disabled={saving}
            style={{
              padding: "var(--space-sm) var(--space-lg)",
              borderRadius: "var(--border-radius)",
              background: "var(--accent)",
              color: "var(--text-on-accent)",
              opacity: saving ? 0.6 : 1,
            }}
          >
            {saving ? "Saving..." : "Save Changes"}
          </button>
        </div>
      </div>
    </div>
  );
}
