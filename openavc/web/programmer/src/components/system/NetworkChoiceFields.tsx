import type { CSSProperties } from "react";
import { AlertTriangle, Lock, RefreshCw } from "lucide-react";
import type {
  AdapterIdentity,
  ListenMode,
  ListenPreview,
  ListenProblem,
  NetworkInterface,
  ReachDevicesBlock,
} from "../../api/systemClient";
import { panelBase, type PanelUrlFacts } from "../../views/panelAccessUrls";
import { EnvHeldNote, EnvLock } from "./EnvHeldNote";
import {
  adapterName,
  adapterOptions,
  LABEL_ANY,
  LABEL_EVERY_ADAPTER,
  LABEL_THIS_COMPUTER,
  LISTEN_ANY,
  LISTEN_THIS_COMPUTER,
  listenChoiceFor,
  listenHelp,
  listenValue,
  matchInterface,
  valueForIdentity,
} from "./adapterChoice";

/** Where Docker's own "who can connect" control is explained. */
export const DOCKER_PORTS_DOCS_URL = "https://docs.openavc.com/hardened-deployment/#docker";

const labelStyle: CSSProperties = {
  fontSize: "var(--font-size-sm)",
  color: "var(--text-secondary)",
};

// The Settings screen's own field styles (SystemSettingsView.tsx), so these
// rows read as part of the same card.
const selectStyle: CSSProperties = {
  width: "100%",
  padding: "var(--space-sm) var(--space-md)",
  background: "var(--bg-input, var(--bg-elevated))",
  border: "1px solid var(--border-color)",
  borderRadius: "var(--border-radius)",
  color: "var(--text-primary)",
  fontSize: "var(--font-size-sm)",
  fontFamily: "inherit",
  cursor: "pointer",
};

const helpText: CSSProperties = {
  fontSize: 12,
  color: "var(--text-muted)",
  gridColumn: "2",
  marginTop: -4,
  marginBottom: "var(--space-xs)",
};

const warnLine: CSSProperties = {
  ...helpText,
  display: "flex",
  gap: 6,
  alignItems: "flex-start",
  color: "var(--text-primary)",
};

// ---------------------------------------------------------------------------
// Accept connections from
// ---------------------------------------------------------------------------

export function AcceptConnectionsField({
  interfaces,
  mode,
  adapter,
  runningName,
  problem,
  preview,
  changed,
  heldBy,
  deploymentType,
  configurationOwner,
  urlFacts,
  onChange,
}: {
  interfaces: NetworkInterface[];
  /** The choice as shown: saved, or changed and not yet saved. */
  mode: ListenMode;
  adapter: AdapterIdentity;
  /** What the server calls the chosen adapter, for one it no longer lists. */
  runningName: string;
  /** Why the server is not listening on the network now, for the choice it
   *  runs. Shown only while that is the choice on screen. */
  problem: ListenProblem | null;
  /** The server's answer for the choice on screen. */
  preview: ListenPreview | null;
  /** The choice on screen differs from the saved one. */
  changed: boolean;
  heldBy: string | undefined;
  deploymentType: string;
  /** "firmware" on a product whose firmware owns these settings. */
  configurationOwner?: "firmware";
  urlFacts: PanelUrlFacts;
  onChange: (mode: ListenMode, adapter: AdapterIdentity) => void;
}) {
  const docker = deploymentType === "docker";
  const options = adapterOptions(interfaces, mode === "adapter" ? adapter : null, runningName);
  const value = docker ? LISTEN_ANY : listenValue(mode, adapter, interfaces);
  const shownMode: ListenMode = docker ? "any" : mode;
  const name = adapterName(adapter, interfaces, runningName);
  const panelUrls = (preview?.panel_addresses ?? [])
    .map((ip) => panelBase(ip, urlFacts))
    .filter(Boolean)
    .map((base) => `${base}/panel`);

  return (
    <>
      <label style={labelStyle} htmlFor="cfg-accept-connections">Accept connections from</label>
      <EnvLock held={docker ? "docker" : heldBy}>
        <select
          id="cfg-accept-connections"
          style={selectStyle}
          value={value}
          onChange={(e) => {
            const next = listenChoiceFor(e.target.value, options);
            onChange(next.mode, next.adapter);
          }}
        >
          <option value={LISTEN_ANY}>{LABEL_ANY}</option>
          {options.length > 0 && (
            <optgroup label="One network only">
              {options.map((o) => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </optgroup>
          )}
          <option value={LISTEN_THIS_COMPUTER}>{LABEL_THIS_COMPUTER}</option>
        </select>
      </EnvLock>
      <span style={helpText} data-testid="accept-connections-help">
        {listenHelp(shownMode, name)}
      </span>
      {!changed && problem && (
        <span style={warnLine} role="status" data-testid="accept-connections-problem">
          <AlertTriangle size={13} style={{ color: "rgb(255, 152, 0)", flexShrink: 0, marginTop: 2 }} aria-hidden />
          <span>{problem.message}</span>
        </span>
      )}
      {changed && preview?.problem && (
        <span style={warnLine} role="status" data-testid="accept-connections-problem">
          <AlertTriangle size={13} style={{ color: "rgb(255, 152, 0)", flexShrink: 0, marginTop: 2 }} aria-hidden />
          <span>{preview.problem.message}</span>
        </span>
      )}
      {panelUrls.length > 0 && (
        <div style={{ ...helpText, display: "flex", gap: "var(--space-sm)" }} data-testid="panels-open-at">
          <span style={{ flexShrink: 0 }}>Panels open at:</span>
          <span style={{ display: "flex", flexDirection: "column" }}>
            {panelUrls.map((u) => (
              <code key={u} style={{ fontSize: 12 }}>{u}</code>
            ))}
          </span>
        </div>
      )}
      {changed && (preview?.consequences.length ?? 0) > 0 && (
        <div style={{ ...helpText, color: "var(--text-primary)" }} data-testid="accept-connections-consequences">
          {preview!.consequences.map((c) => (
            <div key={c.code} style={{ display: "flex", gap: 6, alignItems: "flex-start" }}>
              <AlertTriangle size={13} style={{ color: "rgb(255, 152, 0)", flexShrink: 0, marginTop: 2 }} aria-hidden />
              <span>{c.message}</span>
            </div>
          ))}
        </div>
      )}
      {docker ? (
        <span
          data-testid="accept-connections-docker"
          style={{ ...helpText, display: "flex", gap: 6, alignItems: "flex-start", color: "var(--text-secondary)" }}
        >
          <Lock size={12} style={{ flexShrink: 0, marginTop: 3 }} aria-hidden />
          <span>
            In Docker, choose the host address in the compose file's <code>ports:</code> setting.{" "}
            <a href={DOCKER_PORTS_DOCS_URL} target="_blank" rel="noopener noreferrer" style={{ color: "var(--accent)" }}>
              Docker ports
            </a>
          </span>
        </span>
      ) : (
        <EnvHeldNote
          variable={heldBy}
          deploymentType={deploymentType}
          configurationOwner={configurationOwner}
          style={{ gridColumn: "2" }}
        />
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// Reach devices through
// ---------------------------------------------------------------------------

export function ReachDevicesField({
  interfaces,
  chosen,
  resolved,
  heldBy,
  deploymentType,
  configurationOwner,
  loading,
  onChange,
  onRefresh,
}: {
  interfaces: NetworkInterface[];
  /** The choice as shown: `{}` for every connected adapter. */
  chosen: AdapterIdentity;
  /** The server's resolution of the saved choice. */
  resolved: ReachDevicesBlock | null | undefined;
  heldBy: string | undefined;
  deploymentType: string;
  configurationOwner?: "firmware";
  loading: boolean;
  onChange: (identity: AdapterIdentity) => void;
  onRefresh: () => void;
}) {
  const every = Object.keys(chosen).length === 0;
  const options = adapterOptions(interfaces, every ? null : chosen, resolved?.adapter_name ?? "");
  const value = every ? "" : valueForIdentity(chosen, interfaces);
  const match = every ? undefined : matchInterface(chosen, interfaces);
  const name = adapterName(chosen, interfaces, resolved?.adapter_name ?? "");
  const notConnected = !every && (!match || match.link === false || match.addresses.length === 0);

  return (
    <>
      <label style={labelStyle} htmlFor="cfg-reach-devices">Reach devices through</label>
      <EnvLock held={heldBy} style={{ display: "flex", gap: "var(--space-sm)", alignItems: "center" }}>
        <select
          id="cfg-reach-devices"
          style={{ ...selectStyle, flex: 1 }}
          value={value}
          onChange={(e) => {
            const option = options.find((o) => o.value === e.target.value);
            onChange(option ? { ...option.identity } : {});
          }}
        >
          <option value="">{LABEL_EVERY_ADAPTER}</option>
          {options.map((o) => (
            <option key={o.value} value={o.value}>{o.label}</option>
          ))}
        </select>
        <button
          type="button"
          onClick={onRefresh}
          disabled={loading}
          title="Refresh adapter list"
          aria-label="Refresh adapter list"
          style={{
            background: "none",
            border: "1px solid var(--border-color)",
            borderRadius: "var(--border-radius)",
            color: "var(--text-secondary)",
            cursor: "pointer",
            padding: "var(--space-sm)",
            display: "flex",
            alignItems: "center",
          }}
        >
          <RefreshCw size={14} style={loading ? { animation: "spin 1s linear infinite" } : undefined} />
        </button>
      </EnvLock>
      <span style={helpText} data-testid="reach-devices-help">
        {every
          ? "Device control and discovery scans use every connected adapter, reaching each device through the one this computer routes it to."
          : "Device control and discovery scans use this adapter."}{" "}
        Changes apply to the next device connection or scan, with no restart.
      </span>
      {notConnected && (
        <span style={warnLine} role="status" data-testid="reach-devices-problem">
          <AlertTriangle size={13} style={{ color: "rgb(255, 152, 0)", flexShrink: 0, marginTop: 2 }} aria-hidden />
          <span>
            {name || "The chosen adapter"} isn't connected, so devices and scans can't be reached through it.
            Connect it, or choose another adapter.
          </span>
        </span>
      )}
      <EnvHeldNote
        variable={heldBy}
        deploymentType={deploymentType}
        configurationOwner={configurationOwner}
        style={{ gridColumn: "2" }}
      />
    </>
  );
}
