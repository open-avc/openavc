import { useEffect, useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import type {
  DriverDefinition,
  DriverDiscoveryAmxDdpFingerprint,
  DriverDiscoveryConfig,
  DriverDiscoveryExtractRule,
  DriverDiscoveryMdnsFingerprint,
  DriverDiscoveryProbe,
  DriverDiscoveryPython,
  DriverDiscoverySsdpFingerprint,
} from "../../api/types";
// Single source of truth for the disallowed-open-ports rule (mirrors
// server/discovery/hints.py), shared with the driver validator.
import { DISALLOWED_OPEN_PORTS } from "./validateDriver";

// Style tokens.
const SECTION: React.CSSProperties = {
  marginBottom: "var(--space-lg)",
  paddingBottom: "var(--space-md)",
  borderBottom: "1px solid var(--border-color)",
};
const H2: React.CSSProperties = {
  fontSize: "var(--font-size-md)",
  fontWeight: 700,
  marginBottom: "var(--space-xs)",
};
const INTRO: React.CSSProperties = {
  fontSize: "var(--font-size-sm)",
  color: "var(--text-muted)",
  marginBottom: "var(--space-md)",
};
const LABEL: React.CSSProperties = {
  display: "block",
  fontSize: "var(--font-size-sm)",
  fontWeight: 600,
  color: "var(--text-secondary)",
  marginTop: "var(--space-md)",
  marginBottom: "var(--space-xs)",
};
const HELP: React.CSSProperties = {
  fontSize: 11,
  color: "var(--text-muted)",
  marginTop: "var(--space-xs)",
};
const ROW: React.CSSProperties = {
  display: "flex",
  gap: "var(--space-xs)",
  alignItems: "center",
  marginBottom: "var(--space-xs)",
};
const CARD: React.CSSProperties = {
  padding: "var(--space-sm)",
  border: "1px solid var(--border-color)",
  borderRadius: "var(--radius)",
  marginBottom: "var(--space-sm)",
  display: "flex",
  flexDirection: "column",
  gap: "var(--space-xs)",
};
const ADD: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: "var(--space-xs)",
  fontSize: "var(--font-size-sm)",
  color: "var(--accent)",
  padding: "var(--space-xs) 0",
};
const MONO: React.CSSProperties = {
  flex: 1,
  fontFamily: "var(--font-mono)",
  fontSize: "var(--font-size-sm)",
};
const FIELD_W: React.CSSProperties = { width: 110 };

// ---------------------------------------------------------------------------
// Sugar — collapse fingerprint records to bare strings when only the
// required field is set, so YAML output stays minimal.
// ---------------------------------------------------------------------------

const readMdns = (raw: string | DriverDiscoveryMdnsFingerprint) =>
  typeof raw === "string" ? { service: raw } : raw;

const writeMdns = (
  fp: DriverDiscoveryMdnsFingerprint,
): string | DriverDiscoveryMdnsFingerprint => {
  const txt = fp.txt && Object.keys(fp.txt).length > 0 ? fp.txt : undefined;
  if (!txt && !fp.cross_vendor) return fp.service;
  return {
    service: fp.service,
    ...(txt && { txt }),
    ...(fp.cross_vendor && { cross_vendor: true as const }),
  };
};

const readSsdp = (raw: string | DriverDiscoverySsdpFingerprint) =>
  typeof raw === "string" ? { device_type: raw } : raw;

const writeSsdp = (fp: DriverDiscoverySsdpFingerprint) =>
  fp.cross_vendor || fp.model || fp.manufacturer || fp.friendly_name
    ? fp
    : fp.device_type;

const readPython = (raw: string | DriverDiscoveryPython) =>
  typeof raw === "string" ? { file: raw } : raw;

const writePython = (fp: DriverDiscoveryPython) =>
  fp.cross_vendor ? fp : fp.file;

// ---------------------------------------------------------------------------
// Top-level
// ---------------------------------------------------------------------------

// Schema fields the update() function below knows how to persist. Keep in
// sync with DriverDiscoveryConfig in api/types.ts.
const KNOWN_DISCOVERY_KEYS: ReadonlySet<string> = new Set([
  "requires", "mdns", "ssdp", "amx_ddp", "tcp_probe", "udp_probe", "python",
  "oui", "hostname", "port_open", "manufacturer_alias", "snmp_pen",
]);

interface DiscoveryHintsEditorProps {
  draft: DriverDefinition;
  onUpdate: (partial: Partial<DriverDefinition>) => void;
}

export function DiscoveryHintsEditor({
  draft,
  onUpdate,
}: DiscoveryHintsEditorProps) {
  const cfg: DriverDiscoveryConfig = draft.discovery ?? {};

  // Drop empty arrays / undefined fields so the persisted YAML stays clean.
  const update = (next: DriverDiscoveryConfig) => {
    // Dev-time guard: future fields added to DriverDiscoveryConfig will
    // silently disappear from edits unless they're handled below. Warn
    // loudly so the omission gets noticed during development.
    for (const key of Object.keys(next)) {
      if (!KNOWN_DISCOVERY_KEYS.has(key)) {
        console.warn(
          `DiscoveryHintsEditor: unknown discovery field "${key}" will be ` +
          "dropped on save. Add it to KNOWN_DISCOVERY_KEYS and the per-key " +
          "handling below.",
        );
      }
    }
    const t: DriverDiscoveryConfig = {};
    // `requires` is catalog-stamped, not authored here — but it must survive
    // edits to the other discovery fields (this rebuild used to drop it).
    if (next.requires) t.requires = next.requires;
    if (next.mdns?.length) t.mdns = next.mdns;
    if (next.ssdp?.length) t.ssdp = next.ssdp;
    if (next.amx_ddp?.length) t.amx_ddp = next.amx_ddp;
    if (next.tcp_probe) t.tcp_probe = next.tcp_probe;
    if (next.udp_probe) t.udp_probe = next.udp_probe;
    if (next.python) t.python = next.python;
    if (next.oui?.length) t.oui = next.oui;
    if (next.hostname?.length) t.hostname = next.hostname;
    if (next.port_open?.length) t.port_open = next.port_open;
    if (next.manufacturer_alias?.length)
      t.manufacturer_alias = next.manufacturer_alias;
    if (next.snmp_pen != null) t.snmp_pen = next.snmp_pen;
    onUpdate({ discovery: Object.keys(t).length > 0 ? t : undefined });
  };

  return (
    <div>
      {/* Read-only — stamped by the catalog build (scripts/build_index.py)
          when a fingerprint needs a newer discovery parser; older platforms
          skip the block cleanly. No authoring control on purpose. */}
      {cfg.requires && (
        <div
          style={{
            fontSize: "var(--font-size-sm)",
            color: "var(--text-muted)",
            marginBottom: "var(--space-md)",
          }}
        >
          Requires platform {cfg.requires} — stamped by the driver catalog,
          not hand-edited.
        </div>
      )}
      <FingerprintsSection cfg={cfg} update={update} />
      <HintsSection cfg={cfg} update={update} />
      <AdvancedSection />
      <HelpSection />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Fingerprints
// ---------------------------------------------------------------------------

function FingerprintsSection({
  cfg,
  update,
}: {
  cfg: DriverDiscoveryConfig;
  update: (next: DriverDiscoveryConfig) => void;
}) {
  return (
    <div style={SECTION}>
      <div style={H2}>Fingerprints</div>
      <div style={INTRO}>
        A fingerprint identifies the device on its own — one match is
        enough for the platform to claim the device for this driver.
        Add any combination below.
      </div>

      <MdnsList
        items={(cfg.mdns ?? []).map(readMdns)}
        onChange={(next) => update({ ...cfg, mdns: next.map(writeMdns) })}
      />
      <SsdpList
        items={(cfg.ssdp ?? []).map(readSsdp)}
        onChange={(next) => update({ ...cfg, ssdp: next.map(writeSsdp) })}
      />
      <AmxDdpList
        items={cfg.amx_ddp ?? []}
        onChange={(next) => update({ ...cfg, amx_ddp: next })}
      />
      <ProbeBlock
        kind="tcp"
        probe={cfg.tcp_probe}
        onChange={(p) => update({ ...cfg, tcp_probe: p })}
      />
      <ProbeBlock
        kind="udp"
        probe={cfg.udp_probe}
        onChange={(p) => update({ ...cfg, udp_probe: p })}
      />
      <PythonBlock
        python={cfg.python ? readPython(cfg.python) : undefined}
        onChange={(p) =>
          update({ ...cfg, python: p ? writePython(p) : undefined })
        }
      />
    </div>
  );
}

function MdnsList({
  items,
  onChange,
}: {
  items: DriverDiscoveryMdnsFingerprint[];
  onChange: (next: DriverDiscoveryMdnsFingerprint[]) => void;
}) {
  const setAt = (
    i: number,
    patch: Partial<DriverDiscoveryMdnsFingerprint>,
  ) => {
    const next = [...items];
    next[i] = { ...next[i], ...patch };
    onChange(next);
  };
  const remove = (i: number) => onChange(items.filter((_, j) => j !== i));
  return (
    <div>
      <label style={LABEL}>mDNS announcement</label>
      {items.map((item, i) => (
        <div key={i} style={CARD}>
          <div style={ROW}>
            <input
              type="text"
              value={item.service}
              placeholder="_pjlink._tcp.local."
              onChange={(e) => setAt(i, { service: e.target.value })}
              style={MONO}
            />
            <CrossVendorToggle
              checked={!!item.cross_vendor}
              onChange={(v) => setAt(i, { cross_vendor: v || undefined })}
            />
            <RemoveButton onClick={() => remove(i)} />
          </div>
          <TxtFilterEditor
            txt={item.txt}
            onChange={(txt) => setAt(i, { txt })}
          />
        </div>
      ))}
      <button
        type="button"
        onClick={() => onChange([...items, { service: "" }])}
        style={ADD}
      >
        <Plus size={12} /> Add mDNS service
      </button>
      <div style={HELP}>
        Add a TXT-record filter when the service type is generic (
        <code>_http._tcp.local.</code>) so two drivers don't collide.
      </div>
    </div>
  );
}

function SsdpList({
  items,
  onChange,
}: {
  items: DriverDiscoverySsdpFingerprint[];
  onChange: (next: DriverDiscoverySsdpFingerprint[]) => void;
}) {
  const setAt = (
    i: number,
    patch: Partial<DriverDiscoverySsdpFingerprint>,
  ) => {
    const next = [...items];
    next[i] = { ...next[i], ...patch };
    onChange(next);
  };
  return (
    <div>
      <label style={LABEL}>SSDP / UPnP device type</label>
      {items.map((item, i) => (
        <div key={i} style={CARD}>
          <div style={ROW}>
            <input
              type="text"
              value={item.device_type}
              placeholder="urn:schemas-upnp-org:device:MediaRenderer:1"
              onChange={(e) => setAt(i, { device_type: e.target.value })}
              style={MONO}
            />
            <CrossVendorToggle
              checked={!!item.cross_vendor}
              onChange={(v) => setAt(i, { cross_vendor: v || undefined })}
            />
            <RemoveButton
              onClick={() => onChange(items.filter((_, j) => j !== i))}
            />
          </div>
          <div style={ROW}>
            <input
              type="text"
              value={item.model ?? ""}
              placeholder="Model filter (exact, e.g. ATDM-0604a)"
              onChange={(e) => setAt(i, { model: e.target.value || undefined })}
              style={MONO}
            />
            <input
              type="text"
              value={item.manufacturer ?? ""}
              placeholder="Manufacturer filter"
              onChange={(e) =>
                setAt(i, { manufacturer: e.target.value || undefined })
              }
              style={MONO}
            />
            <input
              type="text"
              value={item.friendly_name ?? ""}
              placeholder="Friendly-name filter"
              onChange={(e) =>
                setAt(i, { friendly_name: e.target.value || undefined })
              }
              style={MONO}
            />
          </div>
        </div>
      ))}
      <button
        type="button"
        onClick={() => onChange([...items, { device_type: "" }])}
        style={ADD}
      >
        <Plus size={12} /> Add SSDP type
      </button>
      <div style={HELP}>
        Add a model / manufacturer filter when several devices share one
        device-type URN (common for a vendor&apos;s whole product family) —
        the filter matches the device&apos;s UPnP description exactly,
        case-insensitive.
      </div>
    </div>
  );
}

function AmxDdpList({
  items,
  onChange,
}: {
  items: DriverDiscoveryAmxDdpFingerprint[];
  onChange: (next: DriverDiscoveryAmxDdpFingerprint[]) => void;
}) {
  const setAt = (
    i: number,
    patch: Partial<DriverDiscoveryAmxDdpFingerprint>,
  ) => {
    const next = [...items];
    next[i] = { ...next[i], ...patch };
    onChange(next);
  };
  return (
    <div>
      <label style={LABEL}>AMX device beacon</label>
      {items.map((item, i) => (
        <div key={i} style={ROW}>
          <input
            type="text"
            placeholder="Make (e.g. Polycom)"
            value={item.make}
            onChange={(e) => setAt(i, { make: e.target.value })}
            style={{ flex: 1 }}
          />
          <input
            type="text"
            placeholder="Model pattern (e.g. SoundStructure*)"
            value={item.model_pattern ?? ""}
            onChange={(e) =>
              setAt(i, { model_pattern: e.target.value || undefined })
            }
            style={{ flex: 1 }}
          />
          <CrossVendorToggle
            checked={!!item.cross_vendor}
            onChange={(v) => setAt(i, { cross_vendor: v || undefined })}
          />
          <RemoveButton
            onClick={() => onChange(items.filter((_, j) => j !== i))}
          />
        </div>
      ))}
      <button
        type="button"
        onClick={() => onChange([...items, { make: "" }])}
        style={ADD}
      >
        <Plus size={12} /> Add AMX beacon
      </button>
      <div style={HELP}>
        AMX devices broadcast a UDP beacon on port 9131. Match on
        manufacturer name and (optionally) a model glob.
      </div>
    </div>
  );
}

function ProbeBlock({
  kind,
  probe,
  onChange,
}: {
  kind: "tcp" | "udp";
  probe?: DriverDiscoveryProbe;
  onChange: (probe: DriverDiscoveryProbe | undefined) => void;
}) {
  const labelText = kind === "tcp" ? "TCP probe" : "UDP probe";

  if (!probe) {
    return (
      <div>
        <label style={LABEL}>{labelText}</label>
        <button
          type="button"
          onClick={() =>
            onChange(
              kind === "tcp"
                ? { port: 5000 }
                : { port: 6000, send_ascii: "", expect: "" },
            )
          }
          style={ADD}
        >
          <Plus size={12} /> Add {labelText}
        </button>
        <div style={HELP}>
          {kind === "tcp"
            ? "Connect on a vendor port. Optionally send a query and match the response."
            : "Broadcast a query on a vendor port and match the reply."}
        </div>
      </div>
    );
  }

  // "send" mode: ascii / hex / none (none = TCP connect-only).
  const sendMode: "ascii" | "hex" | "none" =
    probe.send_hex !== undefined
      ? "hex"
      : probe.send_ascii !== undefined
        ? "ascii"
        : "none";

  const setSendMode = (next: "ascii" | "hex" | "none") => {
    const carry =
      probe.send_ascii !== undefined ? probe.send_ascii : probe.send_hex ?? "";
    const { send_ascii: _a, send_hex: _h, ...rest } = probe;
    if (next === "none") onChange(rest);
    else if (next === "ascii") onChange({ ...rest, send_ascii: carry });
    else onChange({ ...rest, send_hex: carry });
  };

  const setSend = (val: string) => {
    const { send_ascii: _a, send_hex: _h, ...rest } = probe;
    if (sendMode === "ascii") onChange({ ...rest, send_ascii: val });
    else if (sendMode === "hex") onChange({ ...rest, send_hex: val });
  };

  // The runtime accepts exactly one response matcher per probe (hints.py:
  // _parse_response_match rejects more than one). Model it as a single mode +
  // value rather than three independent inputs, mirroring the Send selector
  // above, so an author can never declare two.
  const expectMode: "none" | "expect" | "expect_regex" | "expect_hex" =
    probe.expect !== undefined
      ? "expect"
      : probe.expect_regex !== undefined
        ? "expect_regex"
        : probe.expect_hex !== undefined
          ? "expect_hex"
          : "none";

  const setExpectMode = (next: typeof expectMode) => {
    const carry = probe.expect ?? probe.expect_regex ?? probe.expect_hex ?? "";
    const { expect: _e, expect_regex: _r, expect_hex: _h, ...rest } = probe;
    if (next === "none") onChange(rest);
    else onChange({ ...rest, [next]: carry });
  };

  const setExpect = (val: string) => {
    if (expectMode === "none") return;
    const { expect: _e, expect_regex: _r, expect_hex: _h, ...rest } = probe;
    onChange({ ...rest, [expectMode]: val });
  };

  const expectPlaceholder =
    expectMode === "expect_regex"
      ? "^NS-([A-Z0-9]+)"
      : expectMode === "expect_hex"
        ? "AA55"
        : "NovaStar";

  return (
    <div>
      <label style={LABEL}>{labelText}</label>
      <div style={CARD}>
        <div style={ROW}>
          <span style={FIELD_W}>Port</span>
          <input
            type="number"
            min={1}
            max={65535}
            value={probe.port || ""}
            onChange={(e) =>
              onChange({ ...probe, port: parseInt(e.target.value) || 0 })
            }
            style={{ width: 120 }}
          />
          <span style={{ flex: 1 }} />
          <CrossVendorToggle
            checked={!!probe.cross_vendor}
            onChange={(v) =>
              onChange({ ...probe, cross_vendor: v || undefined })
            }
          />
          <RemoveButton onClick={() => onChange(undefined)} />
        </div>

        <div style={ROW}>
          <span style={FIELD_W}>Send</span>
          <select
            value={sendMode}
            onChange={(e) =>
              setSendMode(e.target.value as "ascii" | "hex" | "none")
            }
          >
            {kind === "tcp" && <option value="none">connect-only</option>}
            <option value="ascii">ASCII</option>
            <option value="hex">Hex</option>
          </select>
          {sendMode !== "none" && (
            <input
              type="text"
              placeholder={sendMode === "hex" ? "00010203" : "QUERY\\r\\n"}
              value={
                sendMode === "hex"
                  ? probe.send_hex ?? ""
                  : probe.send_ascii ?? ""
              }
              onChange={(e) => setSend(e.target.value)}
              style={MONO}
            />
          )}
        </div>

        <div style={ROW}>
          <span style={FIELD_W}>Expect</span>
          <select
            value={expectMode}
            onChange={(e) => setExpectMode(e.target.value as typeof expectMode)}
          >
            <option value="none">none</option>
            <option value="expect">substring</option>
            <option value="expect_regex">regex</option>
            <option value="expect_hex">hex prefix</option>
          </select>
          {expectMode !== "none" && (
            <input
              type="text"
              placeholder={expectPlaceholder}
              value={probe[expectMode] ?? ""}
              onChange={(e) => setExpect(e.target.value)}
              style={MONO}
            />
          )}
        </div>
        <div style={HELP}>
          Pick exactly one matcher — substring, regex, or hex prefix.
          {kind === "udp" && " UDP probes need a matcher."}
          {kind === "tcp" &&
            sendMode !== "none" &&
            " TCP probes that send bytes also need a matcher."}
        </div>

        <div style={ROW}>
          <span style={FIELD_W}>Manufacturer</span>
          <input
            type="text"
            placeholder="Acme"
            value={probe.extract_manufacturer ?? ""}
            onChange={(e) =>
              onChange({
                ...probe,
                extract_manufacturer: e.target.value || undefined,
              })
            }
            style={{ flex: 1 }}
          />
        </div>
        <div style={{ ...HELP, marginTop: 0 }}>
          Static manufacturer string the response confirms — feeds the
          manufacturer-alias hint path so peer vendor drivers can claim
          the device.
        </div>

        <div style={ROW}>
          <span style={FIELD_W}>Timeout (ms)</span>
          <input
            type="number"
            min={1}
            max={10000}
            placeholder={kind === "tcp" ? "3000" : "2000"}
            value={probe.timeout_ms ?? ""}
            onChange={(e) =>
              onChange({
                ...probe,
                timeout_ms: parseInt(e.target.value) || undefined,
              })
            }
            style={{ width: 120 }}
          />
        </div>

        <label style={{ ...LABEL, marginTop: 0 }}>
          Extract metadata (optional)
        </label>
        <ExtractEditor
          rules={probe.extract ?? {}}
          onChange={(rules) =>
            onChange({
              ...probe,
              extract: Object.keys(rules).length > 0 ? rules : undefined,
            })
          }
        />
      </div>
    </div>
  );
}

function PythonBlock({
  python,
  onChange,
}: {
  python: DriverDiscoveryPython | undefined;
  onChange: (python: DriverDiscoveryPython | undefined) => void;
}) {
  if (!python) {
    return (
      <div>
        <label style={LABEL}>Python escape hatch</label>
        <button
          type="button"
          onClick={() => onChange({ file: "" })}
          style={ADD}
        >
          <Plus size={12} /> Add Python file
        </button>
        <div style={HELP}>
          Use when the wire format needs multi-step handshakes,
          encrypted payloads, or framing too dynamic for the
          declarative TCP/UDP probe blocks. Path is relative to the
          driver file. The module must export{" "}
          <code>async def probe(ctx) -&gt; None</code>.
        </div>
      </div>
    );
  }
  return (
    <div>
      <label style={LABEL}>Python escape hatch</label>
      <div style={ROW}>
        <input
          type="text"
          placeholder="./pjlink_class1_discovery.py"
          value={python.file}
          onChange={(e) => onChange({ ...python, file: e.target.value })}
          style={MONO}
        />
        <CrossVendorToggle
          checked={!!python.cross_vendor}
          onChange={(v) =>
            onChange({ ...python, cross_vendor: v || undefined })
          }
        />
        <RemoveButton onClick={() => onChange(undefined)} />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Hints
// ---------------------------------------------------------------------------

function HintsSection({
  cfg,
  update,
}: {
  cfg: DriverDiscoveryConfig;
  update: (next: DriverDiscoveryConfig) => void;
}) {
  return (
    <div style={SECTION}>
      <div style={H2}>Hints</div>
      <div style={INTRO}>
        Hints don't identify a device alone, but combining several
        narrows the candidates down to a "possible" match.
      </div>

      <label style={LABEL}>OUI prefixes (MAC vendor)</label>
      <StringList
        items={cfg.oui ?? []}
        onChange={(next) => update({ ...cfg, oui: next })}
        placeholder="00:0e:dd"
      />

      <label style={LABEL}>Hostname patterns (regex)</label>
      <StringList
        items={cfg.hostname ?? []}
        onChange={(next) => update({ ...cfg, hostname: next })}
        placeholder="^MXA"
      />

      <label style={LABEL}>Open ports</label>
      <PortList
        ports={cfg.port_open ?? []}
        onChange={(next) => update({ ...cfg, port_open: next })}
      />
      <div style={HELP}>
        Vendor-specific TCP/UDP ports the device exposes (e.g.{" "}
        <code>4352</code> for PJLink, <code>17567</code> for Lightware
        LW3). Common web/SSH and admin-UI ports (
        <code>{[...DISALLOWED_OPEN_PORTS].sort((a, b) => a - b).join(", ")}</code>)
        are rejected — they would match nearly every host on the LAN.
      </div>

      <label style={LABEL}>Manufacturer aliases</label>
      <StringList
        items={cfg.manufacturer_alias ?? []}
        onChange={(next) => update({ ...cfg, manufacturer_alias: next })}
        placeholder="Sharp NEC"
      />
      <div style={HELP}>
        Names the matcher should treat as belonging to this driver
        when another driver's probe response carries them
        (case-insensitive).
      </div>

      <label style={LABEL}>SNMP enterprise number</label>
      <input
        type="number"
        placeholder="17049"
        value={cfg.snmp_pen ?? ""}
        onChange={(e) => {
          const n = parseInt(e.target.value);
          update({ ...cfg, snmp_pen: !isNaN(n) && n > 0 ? n : undefined });
        }}
        style={{ width: 200 }}
      />
      <div style={HELP}>
        IANA Private Enterprise Number (e.g. 17049 for Extron).
        Surfaces when SNMP is enabled in the discovery scan.
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Advanced + Help
// ---------------------------------------------------------------------------

function AdvancedSection() {
  return (
    <div style={SECTION}>
      <div style={H2}>Advanced</div>
      <label style={LABEL}>Cross-vendor fingerprints</label>
      <div style={INTRO}>
        Toggle <strong>cross-vendor</strong> on a fingerprint when the
        same wire signal is emitted by more than one vendor's devices —
        PJLink projectors from any manufacturer, Crestron family
        devices on CIP, ONVIF-compliant cameras. The matcher treats
        this driver as a fallback: if a peer driver claims the same
        device through a manufacturer alias, OUI, or hostname hint,
        this entry demotes to <em>alternative</em>.
      </div>
      <label style={LABEL}>Manual-only devices</label>
      <div style={INTRO}>
        If your device has no network announcement and no useful hint
        (no distinctive OUI, hostname pattern, or open port), leave
        both <em>Fingerprints</em> and <em>Hints</em> empty.
        Integrators add it manually from the Add Device dialog;
        document the manual-add steps under Help → Setup on the
        General tab so they know what host / port to enter.
      </div>
    </div>
  );
}

function HelpSection() {
  return (
    <div>
      <div style={H2}>Help</div>
      <div style={INTRO}>
        A typical driver declares one fingerprint plus one or two
        hints:
      </div>
      <pre
        style={{
          background: "var(--bg-secondary)",
          border: "1px solid var(--border-color)",
          borderRadius: "var(--radius)",
          padding: "var(--space-sm)",
          fontSize: "var(--font-size-sm)",
          marginBottom: "var(--space-md)",
          whiteSpace: "pre-wrap",
        }}
      >
{`discovery:
  mdns: "_pjlink._tcp.local."
  oui:
    - "00:0e:dd"`}
      </pre>
      <div style={INTRO}>
        See the{" "}
        <a
          href="https://docs.openavc.com/creating-drivers/#discovery"
          target="_blank"
          rel="noreferrer"
        >
          Discovery section of creating-drivers
        </a>{" "}
        for the full schema reference.
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tiny shared bits
// ---------------------------------------------------------------------------

function CrossVendorToggle({
  checked,
  onChange,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
}) {
  return (
    <label
      title="Cross-vendor: signal shared by multiple manufacturers; demote this driver when a peer claims via a vendor-specific hint."
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 4,
        fontSize: 11,
        color: "var(--text-muted)",
      }}
    >
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
      />
      cross-vendor
    </label>
  );
}

function RemoveButton({ onClick }: { onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      style={{ padding: 2, color: "var(--text-muted)" }}
      title="Remove"
    >
      <Trash2 size={14} />
    </button>
  );
}

function StringList({
  items,
  onChange,
  placeholder,
}: {
  items: string[];
  onChange: (items: string[]) => void;
  placeholder?: string;
}) {
  return (
    <div>
      {items.map((item, i) => (
        <div key={i} style={ROW}>
          <input
            type="text"
            value={item}
            onChange={(e) => {
              const next = [...items];
              next[i] = e.target.value;
              onChange(next);
            }}
            placeholder={placeholder}
            style={MONO}
          />
          <RemoveButton
            onClick={() => onChange(items.filter((_, j) => j !== i))}
          />
        </div>
      ))}
      <button
        type="button"
        onClick={() => onChange([...items, ""])}
        style={ADD}
      >
        <Plus size={12} /> Add
      </button>
    </div>
  );
}

function PortList({
  ports,
  onChange,
}: {
  ports: number[];
  onChange: (ports: number[]) => void;
}) {
  // String buffer so blank rows can exist while typing; only finite,
  // in-range, non-disallowed values flow back to the parent.
  const [buffer, setBuffer] = useState<string[]>(() => ports.map(String));

  useEffect(() => {
    const fromBuffer = buffer
      .map((s) => parseInt(s, 10))
      .filter(
        (n) =>
          Number.isFinite(n) &&
          n >= 1 &&
          n <= 65535 &&
          !DISALLOWED_OPEN_PORTS.has(n),
      );
    const same =
      fromBuffer.length === ports.length &&
      fromBuffer.every((n, i) => n === ports[i]);
    if (!same) setBuffer(ports.map(String));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ports]);

  const commit = (next: string[]) => {
    setBuffer(next);
    const valid: number[] = [];
    for (const s of next) {
      const n = parseInt(s, 10);
      if (
        Number.isFinite(n) &&
        n >= 1 &&
        n <= 65535 &&
        !DISALLOWED_OPEN_PORTS.has(n)
      ) {
        valid.push(n);
      }
    }
    onChange(valid);
  };

  const portError = (raw: string): string | null => {
    if (raw === "") return null;
    const n = parseInt(raw, 10);
    if (!Number.isFinite(n) || String(n) !== raw.trim()) return "not a number";
    if (n < 1 || n > 65535) return "out of range";
    if (DISALLOWED_OPEN_PORTS.has(n)) {
      const disallowed = [...DISALLOWED_OPEN_PORTS].sort((a, b) => a - b).join("/");
      return `too generic (${disallowed} disallowed)`;
    }
    return null;
  };

  return (
    <div>
      {buffer.map((raw, i) => {
        const err = portError(raw);
        return (
          <div key={i} style={ROW}>
            <input
              type="number"
              min={1}
              max={65535}
              value={raw}
              onChange={(e) => {
                const next = [...buffer];
                next[i] = e.target.value;
                commit(next);
              }}
              placeholder="2202"
              style={{
                ...MONO,
                borderColor: err ? "var(--danger)" : undefined,
              }}
            />
            {err && (
              <span style={{ color: "var(--danger)", fontSize: 11 }}>
                {err}
              </span>
            )}
            <RemoveButton
              onClick={() => commit(buffer.filter((_, j) => j !== i))}
            />
          </div>
        );
      })}
      <button
        type="button"
        onClick={() => commit([...buffer, ""])}
        style={ADD}
      >
        <Plus size={12} /> Add port
      </button>
    </div>
  );
}

function TxtFilterEditor({
  txt,
  onChange,
}: {
  txt: Record<string, string> | undefined;
  onChange: (txt: Record<string, string> | undefined) => void;
}) {
  const entries = txt ? Object.entries(txt) : [];
  if (entries.length === 0) {
    return (
      <button
        type="button"
        onClick={() => onChange({ "": "" })}
        style={{ ...ADD, marginLeft: "var(--space-md)" }}
      >
        <Plus size={12} /> Add TXT-record filter
      </button>
    );
  }
  const txtMonoSm: React.CSSProperties = {
    fontFamily: "var(--font-mono)",
    fontSize: 11,
  };
  return (
    <div style={{ marginLeft: "var(--space-md)" }}>
      {entries.map(([k, v], idx) => (
        <div key={idx} style={ROW}>
          <input
            type="text"
            placeholder="key (e.g. manufacturer)"
            value={k}
            onChange={(e) => {
              const next: Record<string, string> = {};
              entries.forEach(([kk, vv], j) => {
                next[j === idx ? e.target.value : kk] = vv;
              });
              onChange(next);
            }}
            style={{ width: 160, ...txtMonoSm }}
          />
          <input
            type="text"
            placeholder="value (e.g. Shure)"
            value={v}
            onChange={(e) => onChange({ ...txt, [k]: e.target.value })}
            style={{ flex: 1, ...txtMonoSm }}
          />
          <RemoveButton
            onClick={() => {
              const next = { ...txt };
              delete next[k];
              onChange(Object.keys(next).length > 0 ? next : undefined);
            }}
          />
        </div>
      ))}
      <button
        type="button"
        onClick={() => onChange({ ...(txt ?? {}), "": "" })}
        style={{ ...ADD, fontSize: 11 }}
      >
        <Plus size={10} /> TXT pair
      </button>
    </div>
  );
}

function ExtractEditor({
  rules,
  onChange,
}: {
  rules: Record<string, DriverDiscoveryExtractRule>;
  onChange: (rules: Record<string, DriverDiscoveryExtractRule>) => void;
}) {
  const entries = Object.entries(rules);

  const setKey = (oldKey: string, newKey: string) => {
    const out: Record<string, DriverDiscoveryExtractRule> = {};
    for (const [k, v] of entries) out[k === oldKey ? newKey : k] = v;
    onChange(out);
  };
  const setValue = (key: string, rule: DriverDiscoveryExtractRule) =>
    onChange({ ...rules, [key]: rule });
  const remove = (key: string) => {
    const out = { ...rules };
    delete out[key];
    onChange(out);
  };

  return (
    <div>
      {entries.map(([key, rule]) => {
        const isRegex = typeof rule === "object" && rule !== null;
        return (
          <div
            key={key}
            style={{
              display: "grid",
              gridTemplateColumns: "150px 80px 1fr 60px auto",
              gap: "var(--space-xs)",
              alignItems: "center",
              marginBottom: "var(--space-xs)",
            }}
          >
            <input
              type="text"
              value={key}
              placeholder="field name"
              onChange={(e) => setKey(key, e.target.value)}
              style={MONO}
            />
            <select
              value={isRegex ? "regex" : "literal"}
              onChange={(e) =>
                setValue(
                  key,
                  e.target.value === "regex"
                    ? {
                        regex: typeof rule === "string" ? rule : rule.regex,
                        group: 1,
                      }
                    : typeof rule === "object"
                      ? rule.regex
                      : rule,
                )
              }
            >
              <option value="literal">literal</option>
              <option value="regex">regex</option>
            </select>
            <input
              type="text"
              value={isRegex ? rule.regex : (rule as string)}
              placeholder={isRegex ? "model=([^,]+)" : "static value"}
              onChange={(e) =>
                setValue(
                  key,
                  isRegex
                    ? { regex: e.target.value, group: rule.group ?? 1 }
                    : e.target.value,
                )
              }
              style={MONO}
            />
            {isRegex ? (
              <input
                type="number"
                min={0}
                value={rule.group ?? 1}
                onChange={(e) =>
                  setValue(key, {
                    regex: rule.regex,
                    group: parseInt(e.target.value) || 0,
                  })
                }
                title="Capture group"
              />
            ) : (
              <span />
            )}
            <RemoveButton onClick={() => remove(key)} />
          </div>
        );
      })}
      <button
        type="button"
        onClick={() => {
          let candidate = "field";
          let i = 1;
          while (candidate in rules) candidate = `field${++i}`;
          onChange({ ...rules, [candidate]: "" });
        }}
        style={ADD}
      >
        <Plus size={12} /> Add extract field
      </button>
      <div style={HELP}>
        Optional metadata pulled from the response. Surfaced in scan
        results' "Why?" reveal so users see what identified the device.
      </div>
    </div>
  );
}
