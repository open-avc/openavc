import { useCallback, useEffect, useMemo, useState } from "react";
import { ArrowUpCircle, CheckCircle, Download, Loader2, ShieldCheck } from "lucide-react";
import * as audit from "../../../../api/auditClient";
import * as driversApi from "../../../../api/driverClient";
import { parseApiError } from "../../../../api/errors";
import type { CommunityDriver, DriverInfo, InstalledDriver } from "../../../../api/types";
import { useAuditStore } from "../../../../store/auditStore";
import { useConnectionStore } from "../../../../store/connectionStore";
import {
  buildDriverOptions,
  confidenceText,
  driversFor,
  installState,
  manufacturers,
  modelsFor,
  optionConfidence,
  preselect,
  type DriverOption,
} from "../driverPicker";
import { ErrorLine } from "../auditParts";
import {
  buttonStyle,
  headingStyle,
  hintStyle,
  inputStyle,
  labelStyle,
  panelStyle,
  spinStyle,
} from "../auditStyles";

const CATALOG_BASE = "https://raw.githubusercontent.com/open-avc/openavc-drivers/main/";
const NOT_LISTED = "__not_listed__";

/** Step 3: the manufacturer, the model, and the driver to test. */
export function DriverStep() {
  const session = useAuditStore((s) => s.session);
  const runningVersion = useConnectionStore((s) => String(s.liveState["system.version"] ?? ""));
  const [catalog, setCatalog] = useState<CommunityDriver[]>([]);
  const [registered, setRegistered] = useState<DriverInfo[]>([]);
  const [installed, setInstalled] = useState<InstalledDriver[]>([]);
  const [loading, setLoading] = useState(true);
  const [catalogError, setCatalogError] = useState("");
  const [brand, setBrand] = useState("");
  const [model, setModel] = useState("");
  const [typedModel, setTypedModel] = useState("");
  const [driverId, setDriverId] = useState("");
  const [firmware, setFirmware] = useState("");
  const [busy, setBusy] = useState<"" | "install" | "continue" | "none">("");
  const [error, setError] = useState("");
  const [preselected, setPreselected] = useState(false);

  const loadInstalled = useCallback(async () => {
    const [reg, inst] = await Promise.all([
      driversApi.listDrivers(),
      driversApi.listInstalledDrivers().catch(() => [] as InstalledDriver[]),
    ]);
    setRegistered(reg);
    setInstalled(inst);
  }, []);

  useEffect(() => {
    let alive = true;
    Promise.all([
      driversApi.fetchCommunityDrivers().catch((e) => {
        if (alive) setCatalogError(parseApiError(e));
        return [] as CommunityDriver[];
      }),
      loadInstalled(),
    ])
      .then(([cat]) => alive && setCatalog(cat))
      .catch((e) => alive && setError(parseApiError(e)))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [loadInstalled]);

  const options = useMemo(
    () => buildDriverOptions(catalog, registered, installed, runningVersion),
    [catalog, registered, installed, runningVersion],
  );
  const brands = useMemo(() => manufacturers(options), [options]);
  const models = useMemo(() => (brand ? modelsFor(options, brand) : []), [options, brand]);
  const notListed = model === NOT_LISTED || (brand !== "" && models.length === 0);
  const drivers = useMemo(
    () => (brand ? driversFor(options, brand, notListed || !model ? null : model) : []),
    [options, brand, model, notListed],
  );
  const chosen = drivers.find((d) => d.id === driverId) ?? null;

  // Start from the verdict's pick and what the device reported, once.
  useEffect(() => {
    if (preselected || loading || !session) return;
    setPreselected(true);
    const result = session.check?.result;
    const device = session.device;
    if (device?.manufacturer) {
      setFirmware(device.firmware ?? "");
    }
    // From a device page, the driver that device uses; otherwise the verdict's.
    const pick = preselect(
      options,
      session.origin?.driver || result?.verdict.identification.driver_id || null,
      result?.verdict.identification.candidates ?? [],
      { manufacturer: result?.device?.manufacturer, model: result?.device?.model },
    );
    if (pick) {
      setBrand(pick.brand);
      setModel(pick.model ?? (modelsFor(options, pick.brand).length > 0 ? "" : NOT_LISTED));
      setDriverId(pick.driverId);
    }
  }, [preselected, loading, session, options]);

  if (!session) return null;
  const sessionId = session.session_id;
  const verdict = session.check?.result?.verdict;
  const modelText = notListed ? typedModel.trim() : model;

  const install = async (option: DriverOption) => {
    setError("");
    setBusy("install");
    try {
      const url = `${CATALOG_BASE}${option.file}`;
      if (option.installed) {
        await driversApi.updateCommunityDriver(option.id, url, option.minPlatform);
      } else {
        await driversApi.installCommunityDriver(option.id, url, option.minPlatform);
      }
      await loadInstalled();
    } catch (e) {
      setError(parseApiError(e));
    } finally {
      setBusy("");
    }
  };

  const submit = async (id: string | null) => {
    setError("");
    setBusy(id ? "continue" : "none");
    try {
      const { session: next } = await audit.setAuditDriver(sessionId, {
        driver_id: id,
        manufacturer: brand,
        model: modelText,
        firmware: firmware.trim(),
      });
      useAuditStore.getState().setSession(next);
      useAuditStore.getState().setStep(id ? "connection" : "report");
    } catch (e) {
      setError(parseApiError(e));
    } finally {
      setBusy("");
    }
  };

  const canContinue = !!chosen && chosen.installed && busy === "";

  return (
    <div style={{ maxWidth: 720 }}>
      <h2 style={headingStyle}>Which driver?</h2>
      {session.origin ? (
        <p style={{ ...hintStyle, fontSize: "var(--font-size-sm)", marginTop: 0 }}>
          The driver {session.origin.name} uses is selected below. Choose another to test it
          instead.
        </p>
      ) : (
        verdict?.identification.driver_id && (
          <p style={{ ...hintStyle, fontSize: "var(--font-size-sm)", marginTop: 0 }}>
            {verdict.sentence} It is selected below; change it if the device is something else.
          </p>
        )
      )}
      {catalogError && (
        <ErrorLine
          text={`The driver catalog could not be loaded (${catalogError}). Only the drivers on this system are listed.`}
        />
      )}
      {loading ? (
        <div style={{ display: "flex", gap: "var(--space-sm)", alignItems: "center" }}>
          <Loader2 size={14} style={spinStyle} /> Loading drivers
        </div>
      ) : (
        <>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "var(--space-md)" }}>
            <div>
              <label htmlFor="audit-brand" style={labelStyle}>
                Manufacturer
              </label>
              <select
                id="audit-brand"
                value={brand}
                onChange={(e) => {
                  setBrand(e.target.value);
                  setModel("");
                  setDriverId("");
                }}
                style={inputStyle}
              >
                <option value="">Choose a manufacturer</option>
                {brands.map((b) => (
                  <option key={b} value={b}>
                    {b}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label htmlFor="audit-model" style={labelStyle}>
                Model
              </label>
              <select
                id="audit-model"
                value={notListed ? NOT_LISTED : model}
                disabled={!brand}
                onChange={(e) => {
                  setModel(e.target.value);
                  setDriverId("");
                }}
                style={inputStyle}
              >
                <option value="">{brand ? "Choose a model" : "Choose a manufacturer first"}</option>
                {models.map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
                <option value={NOT_LISTED}>My model is not listed</option>
              </select>
            </div>
          </div>
          {brand && notListed && (
            <div style={{ marginTop: "var(--space-md)" }}>
              <label htmlFor="audit-typed-model" style={labelStyle}>
                Exact model
              </label>
              <input
                id="audit-typed-model"
                value={typedModel}
                onChange={(e) => setTypedModel(e.target.value)}
                placeholder="As printed on the device"
                autoComplete="off"
                style={inputStyle}
              />
              <div style={hintStyle}>
                Choose the {brand} driver closest to your model below. The report notes that the
                driver does not list this model.
              </div>
            </div>
          )}

          {brand && (notListed || model || driverId) && (
            <fieldset style={{ border: "none", margin: "var(--space-md) 0 0", padding: 0 }}>
              <legend style={labelStyle}>Driver</legend>
              {drivers.length === 0 ? (
                <div style={hintStyle}>No driver lists this manufacturer yet.</div>
              ) : (
                <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-xs)" }}>
                  {drivers.map((d) => (
                    <DriverRow
                      key={d.id}
                      option={d}
                      confidence={optionConfidence(d, notListed || !model ? null : model)}
                      selected={d.id === driverId}
                      onSelect={() => setDriverId(d.id)}
                      onInstall={() => void install(d)}
                      installing={busy === "install" && d.id === driverId}
                    />
                  ))}
                </div>
              )}
            </fieldset>
          )}

          <div style={{ marginTop: "var(--space-md)", maxWidth: 320 }}>
            <label htmlFor="audit-firmware" style={labelStyle}>
              Firmware version (optional)
            </label>
            <input
              id="audit-firmware"
              value={firmware}
              onChange={(e) => setFirmware(e.target.value)}
              placeholder="From the device's menu or web page"
              autoComplete="off"
              style={inputStyle}
            />
          </div>
        </>
      )}

      {error && (
        <div style={{ marginTop: "var(--space-md)" }}>
          <ErrorLine text={error} />
        </div>
      )}

      <div
        style={{
          marginTop: "var(--space-lg)",
          display: "flex",
          gap: "var(--space-sm)",
          flexWrap: "wrap",
          alignItems: "center",
        }}
      >
        <button
          type="button"
          onClick={() => void submit(chosen?.id ?? null)}
          disabled={!canContinue}
          style={buttonStyle("primary", !canContinue)}
        >
          {busy === "continue" && <Loader2 size={14} style={spinStyle} />}
          Continue
        </button>
        <button
          type="button"
          onClick={() => void submit(null)}
          disabled={busy !== ""}
          style={buttonStyle("muted", busy !== "")}
        >
          {busy === "none" && <Loader2 size={14} style={spinStyle} />}
          There is no driver for this device yet
        </button>
      </div>
      {!loading && chosen && !chosen.installed && (
        <p style={{ ...hintStyle, fontSize: "var(--font-size-sm)" }}>
          Install the driver to continue.
        </p>
      )}
    </div>
  );
}

function DriverRow({
  option,
  confidence,
  selected,
  onSelect,
  onInstall,
  installing,
}: {
  option: DriverOption;
  /** The chosen model's confidence, or the manufacturer's with no model chosen. */
  confidence: DriverOption["confidence"];
  selected: boolean;
  onSelect: () => void;
  onInstall: () => void;
  installing: boolean;
}) {
  const state = installState(option);
  const version = option.installed ? option.installedVersion : option.catalogVersion;
  const source =
    option.source === "imported"
      ? "Imported on this system"
      : option.source === "built_in"
        ? "Built into OpenAVC"
        : "";
  return (
    <label
      style={{
        ...panelStyle,
        display: "flex",
        alignItems: "flex-start",
        gap: "var(--space-sm)",
        padding: "var(--space-sm) var(--space-md)",
        cursor: "pointer",
        borderColor: selected ? "var(--accent)" : "var(--border-color)",
      }}
    >
      <input
        type="radio"
        name="audit-driver"
        checked={selected}
        onChange={onSelect}
        style={{ marginTop: 3 }}
      />
      <div style={{ flex: 1, minWidth: 0, fontSize: "var(--font-size-sm)" }}>
        <div style={{ fontWeight: 600 }}>
          {option.name}{" "}
          <span style={{ color: "var(--text-secondary)", fontWeight: 400 }}>
            {option.id}
            {version ? ` · ${version}` : ""}
          </span>
        </div>
        <div
          style={{
            color: "var(--text-secondary)",
            display: "flex",
            gap: "var(--space-sm)",
            flexWrap: "wrap",
            fontSize: "var(--font-size-xs)",
          }}
        >
          {option.verified && (
            <span style={{ display: "inline-flex", alignItems: "center", gap: 3 }}>
              <ShieldCheck size={12} /> Verified
            </span>
          )}
          {confidenceText(confidence) && <span>{confidenceText(confidence)}</span>}
          {option.isVia && <span>A general driver that also covers {option.brand}</span>}
          {source && <span>{source}</span>}
          {option.deprecated && <span>No longer maintained</span>}
        </div>
      </div>
      <div style={{ flexShrink: 0, fontSize: "var(--font-size-xs)" }}>
        {state === "installed" && (
          <span style={{ display: "inline-flex", alignItems: "center", gap: 3, color: "var(--color-success)" }}>
            <CheckCircle size={12} /> Installed
          </span>
        )}
        {state === "blocked" && (
          <span style={{ color: "var(--color-warning)" }}>Needs OpenAVC {option.blockedBy}</span>
        )}
        {state === "unavailable" && <span style={{ color: "var(--text-secondary)" }}>Not installed</span>}
        {(state === "install" || state === "update") && (
          <button
            type="button"
            onClick={(e) => {
              e.preventDefault();
              onSelect();
              onInstall();
            }}
            disabled={installing}
            style={{ ...buttonStyle("muted", installing), padding: "2px var(--space-sm)" }}
          >
            {installing ? (
              <Loader2 size={12} style={spinStyle} />
            ) : state === "update" ? (
              <ArrowUpCircle size={12} />
            ) : (
              <Download size={12} />
            )}
            {state === "update" ? `Update to ${option.catalogVersion}` : "Install"}
          </button>
        )}
      </div>
    </label>
  );
}
