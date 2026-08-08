import { useState } from "react";
import type { DriverDefinition } from "../../api/types";
import { INTERCHANGEABLE_TRANSPORTS } from "../../api/types";
import { scrubForTransport } from "./validateDriver";
import { KeyValueList } from "./CommandBuilder";
import {
  displayDelimiter,
  normalizeDelimiter,
  parseNumericField,
} from "./transportPickerHelpers";

interface TransportPickerProps {
  draft: DriverDefinition;
  onUpdate: (partial: Partial<DriverDefinition>) => void;
}

export function TransportPicker({ draft, onUpdate }: TransportPickerProps) {
  const [revealSecrets, setRevealSecrets] = useState(false);

  const labelStyle: React.CSSProperties = {
    display: "block",
    fontSize: "var(--font-size-sm)",
    color: "var(--text-secondary)",
    marginBottom: "var(--space-xs)",
  };

  const rowStyle: React.CSSProperties = {
    marginBottom: "var(--space-md)",
  };

  // Delimiters compare in canonical form (real control characters);
  // legacy drafts may still hold the escaped text form.
  const delimiter = normalizeDelimiter(draft.delimiter ?? "");

  // Numeric config fields: store the parsed number, unset the key on blank
  // (the placeholder shows the effective default), and ignore unparseable
  // keystrokes — never snap blank or 0 to a magic default mid-edit.
  const setNumericConfig = (key: string, raw: string, float = false) => {
    const parsed = parseNumericField(raw, float);
    if (parsed === undefined) return;
    const next = { ...draft.default_config };
    if (parsed === null) delete next[key];
    else next[key] = parsed;
    onUpdate({ default_config: next });
  };

  const numericValue = (key: string): number | "" =>
    (draft.default_config[key] as number | undefined) ?? "";

  const secretToggle = (
    <button
      type="button"
      onClick={() => setRevealSecrets((v) => !v)}
      style={{
        fontSize: "var(--font-size-sm)",
        color: "var(--accent)",
        padding: "0 var(--space-sm)",
      }}
    >
      {revealSecrets ? "Hide" : "Show"}
    </button>
  );

  const switchTransport = (next: string) => {
    if (next === draft.transport) return;
    // Commands and setting writes keep transport-specific wire fields
    // (an OSC address, an HTTP path) that the form no longer renders
    // after a switch — invisible leftovers that kill the command at
    // runtime. Scrub them, confirming first when authored content would
    // be removed.
    const scrub = scrubForTransport(draft, next);
    const removals = [...scrub.removals];
    // The interchangeable-transports list only applies to a real medium — a
    // bridge device owns no transport, and a leftover list would wrongly
    // offer network/serial connection modes in the Add Device dialog.
    const dropTransports =
      next === "bridge" && (draft.transports ?? []).length > 0;
    if (dropTransports) {
      removals.push({
        name: "also usable over",
        fields: draft.transports ?? [],
      });
    }
    if (removals.length > 0) {
      const detail = removals
        .map((r) => `  ${r.name}: ${r.fields.join(", ")}`)
        .join("\n");
      const ok = window.confirm(
        `Switching the transport removes fields that don't apply to it:\n\n${detail}\n\nContinue?`,
      );
      if (!ok) return;
    }
    onUpdate({
      transport: next,
      commands: scrub.commands,
      ...(scrub.device_settings ? { device_settings: scrub.device_settings } : {}),
      ...(dropTransports ? { transports: undefined } : {}),
    });
  };

  return (
    <div>
      <div style={rowStyle}>
        <label style={labelStyle}>Transport Type</label>
        <select
          value={draft.transport}
          onChange={(e) => switchTransport(e.target.value)}
          style={{ width: "100%" }}
        >
          <option value="tcp">TCP</option>
          <option value="udp">UDP</option>
          <option value="serial">Serial</option>
          <option value="http">HTTP / REST API</option>
          <option value="osc">OSC (Open Sound Control)</option>
          <option value="bridge">Bridge (no address of its own)</option>
        </select>
        <div
          style={{
            fontSize: "11px",
            color: "var(--text-muted)",
            marginTop: "var(--space-xs)",
          }}
        >
          {draft.transport === "http"
            ? "Choose HTTP for devices with REST APIs (JSON, SOAP, etc.)."
            : draft.transport === "udp"
            ? "Choose UDP for devices that use datagram-based protocols (JSON-over-UDP, video wall controllers, etc.). Each command is sent as a single packet."
            : draft.transport === "osc"
            ? "Choose OSC for devices controlled via Open Sound Control (mixing consoles, show control, lighting, media servers). Commands use OSC address paths and typed arguments."
            : draft.transport === "bridge"
            ? "For a device with no address of its own that emits through a live bridge instance (e.g. an IR device on an emitter port). It opens no socket — commands route via the bridge, and the device is online whenever its bridge is."
            : "Choose TCP for network devices, UDP for datagram protocols, Serial for RS-232/RS-485, HTTP for REST APIs, or OSC for Open Sound Control. Bridge is for devices that only exist behind another device (IR devices on an emitter port)."}
        </div>
      </div>

      {draft.transport !== "bridge" && (
        <AlsoUsableOver draft={draft} onUpdate={onUpdate} />
      )}

      {draft.transport !== "http" && draft.transport !== "udp" && draft.transport !== "osc" && draft.transport !== "bridge" && <div style={rowStyle}>
        <label style={labelStyle}>Message Delimiter</label>
        <select
          value={delimiter}
          onChange={(e) => onUpdate({ delimiter: e.target.value })}
          style={{ width: "100%" }}
        >
          <option value={"\r\n"}>CR+LF (\r\n) — most common</option>
          <option value={"\r"}>CR only (\r) — Extron, PJLink</option>
          <option value={"\n"}>LF only (\n) — Biamp, QSC</option>
          {!["\r\n", "\r", "\n"].includes(delimiter) && (
            <option value={delimiter}>Custom: {displayDelimiter(delimiter)}</option>
          )}
        </select>
        <div
          style={{
            fontSize: "11px",
            color: "var(--text-muted)",
            marginTop: "var(--space-xs)",
          }}
        >
          How the device marks the end of each message. Check the device&apos;s
          protocol manual if unsure.
          {!["\r\n", "\r", "\n"].includes(delimiter) && (
            <span> Current value is a custom delimiter: <code>{displayDelimiter(delimiter)}</code></span>
          )}
        </div>
      </div>}

      {draft.transport === "http" && (
        <>
          <div style={rowStyle}>
            <label style={labelStyle}>Default Port</label>
            <input
              type="number"
              value={numericValue("port")}
              placeholder="80"
              onChange={(e) => setNumericConfig("port", e.target.value)}
              style={{ width: 120 }}
            />
          </div>
          <div style={rowStyle}>
            <label style={labelStyle}>Authentication</label>
            <select
              value={
                (draft.default_config.auth_type as string | undefined) ?? "none"
              }
              onChange={(e) =>
                onUpdate({
                  default_config: {
                    ...draft.default_config,
                    auth_type: e.target.value,
                  },
                })
              }
              style={{ width: "100%" }}
            >
              <option value="none">None</option>
              <option value="basic">HTTP Basic Auth</option>
              <option value="digest">HTTP Digest Auth</option>
              <option value="bearer">Bearer Token</option>
              <option value="api_key">API Key (custom header)</option>
            </select>
            <div
              style={{
                fontSize: "11px",
                color: "var(--text-muted)",
                marginTop: "var(--space-xs)",
              }}
            >
              Users configure credentials per device. This sets the auth method.
            </div>
          </div>
          <div style={{ display: "flex", gap: "var(--space-lg)", ...rowStyle }}>
            <label
              style={{
                display: "flex",
                alignItems: "center",
                gap: "var(--space-xs)",
                fontSize: "var(--font-size-sm)",
              }}
            >
              <input
                type="checkbox"
                checked={(draft.default_config.ssl as boolean | undefined) ?? false}
                onChange={(e) =>
                  onUpdate({
                    default_config: {
                      ...draft.default_config,
                      ssl: e.target.checked,
                    },
                  })
                }
              />
              Use HTTPS
            </label>
            <label
              style={{
                display: "flex",
                alignItems: "center",
                gap: "var(--space-xs)",
                fontSize: "var(--font-size-sm)",
              }}
            >
              <input
                type="checkbox"
                checked={(draft.default_config.verify_ssl as boolean | undefined) ?? true}
                onChange={(e) =>
                  onUpdate({
                    default_config: {
                      ...draft.default_config,
                      verify_ssl: e.target.checked,
                    },
                  })
                }
              />
              Verify SSL Certificate
            </label>
          </div>

          {/* Auth-type-specific credential fields */}
          {(draft.default_config.auth_type as string | undefined) === "bearer" && (
            <div style={rowStyle}>
              <label style={labelStyle}>Bearer Token (default)</label>
              <div style={{ display: "flex", alignItems: "center" }}>
                <input
                  type={revealSecrets ? "text" : "password"}
                  autoComplete="new-password"
                  value={(draft.default_config.token as string | undefined) ?? ""}
                  onChange={(e) =>
                    onUpdate({
                      default_config: {
                        ...draft.default_config,
                        token: e.target.value,
                      },
                    })
                  }
                  placeholder="leave blank — users enter per device"
                  style={{ flex: 1, fontFamily: "var(--font-mono)" }}
                />
                {secretToggle}
              </div>
              <div
                style={{
                  fontSize: "11px",
                  color: "var(--text-muted)",
                  marginTop: "var(--space-xs)",
                }}
              >
                Default value if the device ships with a known token. Users
                normally enter their own per-device token. Anything entered
                here is saved in the driver file as plain text.
              </div>
            </div>
          )}

          {(draft.default_config.auth_type as string | undefined) === "api_key" && (
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "var(--space-md)", ...rowStyle }}>
              <div>
                <label style={labelStyle}>API Key Header</label>
                <input
                  value={
                    (draft.default_config.api_key_header as string | undefined) ?? ""
                  }
                  onChange={(e) =>
                    onUpdate({
                      default_config: {
                        ...draft.default_config,
                        api_key_header: e.target.value,
                      },
                    })
                  }
                  placeholder="X-API-Key"
                  style={{ width: "100%", fontFamily: "var(--font-mono)" }}
                />
                <div
                  style={{
                    fontSize: "11px",
                    color: "var(--text-muted)",
                    marginTop: "var(--space-xs)",
                  }}
                >
                  Header name the device expects (e.g. <code>X-API-Key</code>,{" "}
                  <code>Authorization</code>).
                </div>
              </div>
              <div>
                <label style={labelStyle}>API Key (default)</label>
                <div style={{ display: "flex", alignItems: "center" }}>
                  <input
                    type={revealSecrets ? "text" : "password"}
                    autoComplete="new-password"
                    value={(draft.default_config.api_key as string | undefined) ?? ""}
                    onChange={(e) =>
                      onUpdate({
                        default_config: {
                          ...draft.default_config,
                          api_key: e.target.value,
                        },
                      })
                    }
                    placeholder="leave blank — users enter per device"
                    style={{ flex: 1, fontFamily: "var(--font-mono)" }}
                  />
                  {secretToggle}
                </div>
              </div>
            </div>
          )}

          <div style={rowStyle}>
            <label style={labelStyle}>Default Headers</label>
            <KeyValueList
              values={
                (draft.default_config.default_headers as Record<string, string> | undefined) ?? {}
              }
              onChange={(headers) => {
                const next = { ...draft.default_config };
                if (Object.keys(headers).length) next.default_headers = headers;
                else delete next.default_headers;
                onUpdate({ default_config: next });
              }}
              keyPlaceholder="Header-Name"
              valuePlaceholder="e.g. application/json"
              monoValue
            />
            <div
              style={{
                fontSize: "11px",
                color: "var(--text-muted)",
                marginTop: "var(--space-xs)",
              }}
            >
              Sent with every request (fixed <code>Accept</code>, a static{" "}
              <code>User-Agent</code>, a header the device always expects).
              Per-command headers are set on each command and apply on top of
              these.
            </div>
          </div>

          <div style={rowStyle}>
            <label style={labelStyle}>Request Timeout (seconds)</label>
            <input
              type="number"
              value={numericValue("timeout")}
              placeholder="5"
              onChange={(e) => setNumericConfig("timeout", e.target.value, true)}
              min={0.1}
              step={0.5}
              style={{ width: 120 }}
            />
            <div
              style={{
                fontSize: "11px",
                color: "var(--text-muted)",
                marginTop: "var(--space-xs)",
              }}
            >
              Per-request timeout. Default 5 seconds.
            </div>
          </div>
        </>
      )}

      {draft.transport === "tcp" && (
        <>
          <div style={rowStyle}>
            <label style={labelStyle}>Default Port</label>
            <input
              type="number"
              value={numericValue("port")}
              placeholder="23"
              onChange={(e) => setNumericConfig("port", e.target.value)}
              style={{ width: 120 }}
            />
          </div>
        </>
      )}

      {draft.transport === "udp" && (
        <>
          <div style={rowStyle}>
            <label style={labelStyle}>Default Port</label>
            <input
              type="number"
              value={numericValue("port")}
              placeholder="6000"
              onChange={(e) => setNumericConfig("port", e.target.value)}
              style={{ width: 120 }}
            />
          </div>
        </>
      )}

      {draft.transport === "osc" && (
        <>
          <div style={rowStyle}>
            <label style={labelStyle}>Default Send Port</label>
            <input
              type="number"
              value={numericValue("port")}
              placeholder="8000"
              onChange={(e) => setNumericConfig("port", e.target.value)}
              style={{ width: 120 }}
            />
          </div>
          <div style={rowStyle}>
            <label style={labelStyle}>Listen Port</label>
            <input
              type="number"
              value={numericValue("listen_port")}
              placeholder="0"
              onChange={(e) => setNumericConfig("listen_port", e.target.value)}
              style={{ width: 120 }}
            />
            <div
              style={{
                fontSize: "11px",
                color: "var(--text-muted)",
                marginTop: "var(--space-xs)",
              }}
            >
              Port to receive device feedback on. Set to 0 to receive on the same
              socket used for sending (most devices). Only set this if the device
              sends feedback to a specific port.
            </div>
          </div>
        </>
      )}

      {draft.transport === "serial" && (
        <>
          <div style={rowStyle}>
            <label style={labelStyle}>Default Baud Rate</label>
            <select
              value={
                String(
                  (draft.default_config.baudrate as number | undefined) ?? 9600
                )
              }
              onChange={(e) =>
                onUpdate({
                  default_config: {
                    ...draft.default_config,
                    baudrate: parseInt(e.target.value),
                  },
                })
              }
              style={{ width: 160 }}
            >
              {[1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200].map(
                (r) => (
                  <option key={r} value={String(r)}>
                    {r}
                  </option>
                )
              )}
            </select>
          </div>
          <div style={{ display: "flex", gap: "var(--space-lg)", ...rowStyle }}>
            <div>
              <label style={labelStyle}>Default Parity</label>
              <select
                value={
                  (draft.default_config.parity as string | undefined) ?? "N"
                }
                onChange={(e) =>
                  onUpdate({
                    default_config: {
                      ...draft.default_config,
                      parity: e.target.value,
                    },
                  })
                }
                style={{ width: 120 }}
              >
                <option value="N">None</option>
                <option value="E">Even</option>
                <option value="O">Odd</option>
              </select>
            </div>
            <div>
              <label style={labelStyle}>Data Bits</label>
              <select
                value={String(
                  (draft.default_config.bytesize as number | undefined) ?? 8
                )}
                onChange={(e) =>
                  onUpdate({
                    default_config: {
                      ...draft.default_config,
                      bytesize: parseInt(e.target.value),
                    },
                  })
                }
                style={{ width: 100 }}
              >
                <option value="5">5</option>
                <option value="6">6</option>
                <option value="7">7</option>
                <option value="8">8</option>
              </select>
            </div>
            <div>
              <label style={labelStyle}>Stop Bits</label>
              <select
                value={String(
                  (draft.default_config.stopbits as number | undefined) ?? 1
                )}
                onChange={(e) =>
                  onUpdate({
                    default_config: {
                      ...draft.default_config,
                      stopbits: parseFloat(e.target.value),
                    },
                  })
                }
                style={{ width: 100 }}
              >
                <option value="1">1</option>
                <option value="1.5">1.5</option>
                <option value="2">2</option>
              </select>
            </div>
          </div>
          <div
            style={{
              fontSize: "11px",
              color: "var(--text-muted)",
              marginTop: "calc(-1 * var(--space-md))",
              marginBottom: "var(--space-md)",
            }}
          >
            Most modern AV gear uses 8/N/1. Older RS-232 protocols may need
            7/E/1 — check the device manual.
          </div>
        </>
      )}

      {/* Inter-command delay — TCP, UDP, serial, and OSC. A bridge device
          owns no transport of its own, so pacing is the bridge's job. */}
      {draft.transport !== "http" && draft.transport !== "bridge" && <div style={rowStyle}>
        <label style={labelStyle}>Inter-Command Delay (seconds)</label>
        <input
          type="number"
          value={numericValue("inter_command_delay")}
          placeholder="0"
          onChange={(e) => setNumericConfig("inter_command_delay", e.target.value, true)}
          min={0}
          step={0.01}
          style={{ width: 120 }}
        />
        <div
          style={{
            fontSize: "11px",
            color: "var(--text-muted)",
            marginTop: "var(--space-xs)",
          }}
        >
          Minimum delay between commands. Some devices need this to avoid
          command flooding (e.g., Extron recommends 0.1s).
        </div>
      </div>}
    </div>
  );
}

const TRANSPORT_LABELS: Record<string, string> = {
  tcp: "TCP",
  serial: "Serial",
  udp: "UDP",
  http: "HTTP",
  osc: "OSC",
};

/**
 * Edits the optional `transports:` interchangeable list — the transports this
 * driver can use interchangeably because its command/response strings are
 * byte-identical across the listed media (e.g. the same text protocol over
 * the network or a serial line). The per-device connection picks the actual
 * transport; listing Serial makes the device offerable over a direct serial
 * port or through a bridge.
 *
 * Corpus convention (mirrored here): the list names every usable transport,
 * the primary included — `transport: tcp` + `transports: [tcp, serial]`.
 * The primary is kept in the list implicitly; the checkboxes cover the rest.
 * No boxes checked = the key is removed so minimal YAML stays minimal.
 */
function AlsoUsableOver({
  draft,
  onUpdate,
}: {
  draft: DriverDefinition;
  onUpdate: (partial: Partial<DriverDefinition>) => void;
}) {
  const declared = draft.transports ?? [];
  const others = INTERCHANGEABLE_TRANSPORTS.filter(
    (t) => t !== draft.transport,
  );

  const toggle = (transport: string, on: boolean) => {
    const chosen = new Set(declared);
    if (on) chosen.add(transport);
    else chosen.delete(transport);
    // The primary is implicit — rebuilding below re-adds it in front, so
    // drop it here to decide whether anything beyond it remains.
    chosen.delete(draft.transport);
    if (chosen.size === 0) {
      onUpdate({ transports: undefined });
      return;
    }
    // Canonical order: primary first (corpus convention), the known
    // interchangeable transports next, then any unrecognized entries from a
    // hand-authored file preserved verbatim (validation flags them).
    const known = INTERCHANGEABLE_TRANSPORTS.filter((t) => chosen.has(t));
    const unknown = [...chosen].filter(
      (t) => !(INTERCHANGEABLE_TRANSPORTS as readonly string[]).includes(t),
    );
    onUpdate({ transports: [draft.transport, ...known, ...unknown] });
  };

  return (
    <div style={{ marginBottom: "var(--space-md)" }}>
      <label
        style={{
          display: "block",
          fontSize: "var(--font-size-sm)",
          color: "var(--text-secondary)",
          marginBottom: "var(--space-xs)",
        }}
      >
        Also Usable Over
      </label>
      <div style={{ display: "flex", gap: "var(--space-lg)", flexWrap: "wrap" }}>
        {others.map((t) => (
          <label
            key={t}
            style={{
              display: "flex",
              alignItems: "center",
              gap: "var(--space-xs)",
              fontSize: "var(--font-size-sm)",
            }}
          >
            <input
              type="checkbox"
              checked={declared.includes(t)}
              onChange={(e) => toggle(t, e.target.checked)}
            />
            {TRANSPORT_LABELS[t] ?? t}
          </label>
        ))}
      </div>
      <div
        style={{
          fontSize: "11px",
          color: "var(--text-muted)",
          marginTop: "var(--space-xs)",
        }}
      >
        Opt-in, for protocols whose command and response strings are
        byte-identical across the listed media (e.g. the same text protocol
        over the network or a serial line). The per-device connection picks
        the actual transport; listing Serial makes the device offerable over
        a direct serial port or through a bridge. Only declare it when the
        strings really are identical.
      </div>
    </div>
  );
}

