import { useEffect, useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import type {
  DriverDefinition,
  DriverDiscoveryCustomProbe,
  DriverDiscoveryExtractRule,
  DriverDiscoveryHints,
  DriverDiscoveryMdnsEntry,
} from "../../api/types";

// Mirror of `DISALLOWED_OPEN_PORTS` in server/discovery/hints.py.
// Surfaced in the UI so driver authors see the rule at the point of
// authoring rather than discovering it via a load-time error.
const DISALLOWED_OPEN_PORTS: ReadonlySet<number> = new Set([22, 80, 443]);

// Mirrors of DISALLOWED_UDP_BROADCAST_PROBE_PORTS and
// DISALLOWED_TCP_ACTIVE_PROBE_PORTS in server/discovery/hints.py.
const DISALLOWED_UDP_PROBE_PORTS: ReadonlySet<number> = new Set([
  1900, 3702, 4352, 5353, 9131, 41794,
]);
const DISALLOWED_TCP_PROBE_PORTS: ReadonlySet<number> = new Set([
  23, 1515, 1688, 1710, 4352, 10500, 49280,
]);

const ALLOWED_ACTIVE_PROBES = [
  "pjlink_class1",
  "extron_sis",
  "tesira_ttp",
  "qrc",
  "kramer_p3000",
  "shure_dcs",
  "samsung_mdc",
  "visca",
  "crestron_cip_tcp",
  "yamaha_rcp",
] as const;

interface DiscoveryHintsEditorProps {
  draft: DriverDefinition;
  onUpdate: (partial: Partial<DriverDefinition>) => void;
}

export function DiscoveryHintsEditor({ draft, onUpdate }: DiscoveryHintsEditorProps) {
  const hints: DriverDiscoveryHints = draft.discovery ?? {};

  const update = (partial: Partial<DriverDiscoveryHints>) => {
    onUpdate({ discovery: { ...hints, ...partial } });
  };

  const updateOnvif = (kind: "off" | "on" | "manufacturer", manufacturer = "") => {
    if (kind === "off") {
      const { onvif: _drop, ...rest } = hints;
      onUpdate({ discovery: rest });
    } else if (kind === "on") {
      update({ onvif: true });
    } else {
      update({ onvif: { manufacturer } });
    }
  };

  const labelStyle: React.CSSProperties = {
    display: "block",
    fontSize: "var(--font-size-sm)",
    color: "var(--text-secondary)",
    marginBottom: "var(--space-xs)",
    fontWeight: 600,
  };

  const sectionStyle: React.CSSProperties = {
    marginBottom: "var(--space-lg)",
    paddingBottom: "var(--space-md)",
    borderBottom: "1px solid var(--border-color)",
  };

  const helpStyle: React.CSSProperties = {
    fontSize: "11px",
    color: "var(--text-muted)",
    marginTop: "var(--space-xs)",
  };

  const checkboxRow: React.CSSProperties = {
    display: "flex",
    alignItems: "center",
    gap: "var(--space-xs)",
    marginBottom: "var(--space-xs)",
  };

  const onvifEnabled = hints.onvif !== undefined && hints.onvif !== false;
  const onvifManufacturer =
    typeof hints.onvif === "object" && hints.onvif !== null
      ? hints.onvif.manufacturer ?? ""
      : "";

  return (
    <div>
      <p style={{ fontSize: "var(--font-size-sm)", color: "var(--text-muted)", marginBottom: "var(--space-md)" }}>
        Discovery hints map this driver to network signals. Strong
        signals (Tier 1 / 2 / 3) produce an <strong>identified</strong>
        match; soft signals (Tier 4 — OUI, hostname, open port, SNMP
        PEN) surface the device as <strong>possible</strong> with a
        candidate driver list. Any combination is valid. Check
        <strong> Manual only</strong> only if the device expects manual
        IP entry and has no network announcement at all.
      </p>

      {/* Manual only */}
      <div style={sectionStyle}>
        <div style={checkboxRow}>
          <input
            id="discovery-manual-only"
            type="checkbox"
            checked={!!hints.manual_only}
            onChange={(e) => update({ manual_only: e.target.checked || undefined })}
          />
          <label htmlFor="discovery-manual-only" style={{ fontWeight: 600 }}>
            Manual only
          </label>
        </div>
        <div style={helpStyle}>
          Documentation hint that this device expects manual IP entry — it
          has no network announcement and no useful soft signal. Does not
          affect matcher behavior; any signals declared elsewhere on this
          driver still register normally.
        </div>
      </div>

      {/* Tier 1 */}
      <div style={sectionStyle}>
        <label style={labelStyle}>Tier 1 — passive listeners</label>

        <div style={{ marginBottom: "var(--space-md)" }}>
          <label style={{ ...labelStyle, fontWeight: 400 }}>mDNS service types</label>
          <MdnsServicesEditor
            entries={hints.mdns_services ?? []}
            onChange={(items) => update({ mdns_services: items.length ? items : undefined })}
          />
          <div style={helpStyle}>
            e.g. <code>_pjlink._tcp.local.</code>. Add a TXT-record filter
            when the service type is generic (<code>_http._tcp.local.</code>)
            so two drivers don't collide.
          </div>
        </div>

        <div style={{ marginBottom: "var(--space-md)" }}>
          <label style={{ ...labelStyle, fontWeight: 400 }}>SSDP / UPnP device-type URNs</label>
          <ListEditor
            items={hints.ssdp_device_types ?? []}
            onChange={(items) => update({ ssdp_device_types: items.length ? items : undefined })}
            placeholder="urn:schemas-upnp-org:device:MediaRenderer:1"
          />
        </div>

        <div>
          <label style={{ ...labelStyle, fontWeight: 400 }}>AMX DDP beacon (Make / Model pattern)</label>
          <div style={{ display: "flex", gap: "var(--space-xs)" }}>
            <input
              type="text"
              placeholder="Make (e.g. Polycom)"
              value={hints.amx_ddp?.make ?? ""}
              onChange={(e) => {
                const make = e.target.value;
                if (!make) {
                  const { amx_ddp: _drop, ...rest } = hints;
                  onUpdate({ discovery: rest });
                } else {
                  update({
                    amx_ddp: { make, model_pattern: hints.amx_ddp?.model_pattern ?? "*" },
                  });
                }
              }}
              style={{ flex: 1 }}
            />
            <input
              type="text"
              placeholder="Model pattern (e.g. SoundStructure*)"
              value={hints.amx_ddp?.model_pattern ?? ""}
              disabled={!hints.amx_ddp?.make}
              onChange={(e) =>
                update({
                  amx_ddp: { make: hints.amx_ddp!.make, model_pattern: e.target.value || "*" },
                })
              }
              style={{ flex: 1 }}
            />
          </div>
        </div>
      </div>

      {/* Tier 2 */}
      <div style={sectionStyle}>
        <label style={labelStyle}>Tier 2 — broadcast probes</label>
        <div style={checkboxRow}>
          <input
            id="discovery-pjlink2"
            type="checkbox"
            checked={!!hints.pjlink_class2}
            onChange={(e) => update({ pjlink_class2: e.target.checked || undefined })}
          />
          <label htmlFor="discovery-pjlink2">PJLink Class 2 SRCH (UDP 4352)</label>
        </div>
        <div style={checkboxRow}>
          <input
            id="discovery-cip"
            type="checkbox"
            checked={!!hints.crestron_cip}
            onChange={(e) => update({ crestron_cip: e.target.checked || undefined })}
          />
          <label htmlFor="discovery-cip">Crestron CIP (UDP 41794)</label>
        </div>

        <div style={{ marginTop: "var(--space-sm)" }}>
          <div style={checkboxRow}>
            <input
              id="discovery-onvif"
              type="checkbox"
              checked={onvifEnabled}
              onChange={(e) => updateOnvif(e.target.checked ? "on" : "off")}
            />
            <label htmlFor="discovery-onvif">ONVIF WS-Discovery</label>
          </div>
          {onvifEnabled && (
            <input
              type="text"
              placeholder="Filter by manufacturer (e.g. Axis)"
              value={onvifManufacturer}
              onChange={(e) => updateOnvif("manufacturer", e.target.value)}
              style={{ marginLeft: "var(--space-md)", marginTop: "var(--space-xs)", width: "60%" }}
            />
          )}
          <div style={helpStyle}>
            Multiple camera drivers can opt in to ONVIF as long as each
            constrains by manufacturer. The matcher uses the responder's
            <code>Scopes</code> field to disambiguate.
          </div>
        </div>

        <div style={{ ...checkboxRow, marginTop: "var(--space-sm)" }}>
          <input
            id="discovery-hiqnet"
            type="checkbox"
            checked={!!hints.hiqnet}
            onChange={(e) => update({ hiqnet: e.target.checked || undefined })}
          />
          <label htmlFor="discovery-hiqnet">HARMAN HiQnet (UDP 3804)</label>
        </div>
        <div style={checkboxRow}>
          <input
            id="discovery-symetrix"
            type="checkbox"
            checked={!!hints.symetrix}
            onChange={(e) => update({ symetrix: e.target.checked || undefined })}
          />
          <label htmlFor="discovery-symetrix">Symetrix ControlNet (UDP 49216)</label>
        </div>
      </div>

      {/* Phase 9: driver-declared custom probes */}
      <div style={sectionStyle}>
        <label style={labelStyle}>Tier 2/3 — custom probes (driver-declared)</label>
        <div style={{ ...helpStyle, marginBottom: "var(--space-sm)" }}>
          When a vendor's discovery wire format isn't covered by a built-in
          opt-in above, declare it here. The runtime sends your{" "}
          <code>send</code> bytes, listens for <code>response_match</code>,
          and emits Tier 2 (UDP) or Tier 3 (TCP) evidence with whatever
          your <code>extract</code> rules pull out. <strong>Reserved
          extract keys:</strong> <code>manufacturer</code> and <code>make</code>{" "}
          feed the Phase 8.6 vendor_string soft-signal path automatically —
          set them when the response carries a manufacturer string and a
          peer driver might want to claim it via <code>vendor_aliases</code>.
        </div>

        <CustomProbeEditor
          kind="udp"
          probe={hints.udp_broadcast_probe}
          disallowedPorts={DISALLOWED_UDP_PROBE_PORTS}
          onChange={(p) => update({ udp_broadcast_probe: p })}
        />
        <CustomProbeEditor
          kind="tcp"
          probe={hints.tcp_active_probe}
          disallowedPorts={DISALLOWED_TCP_PROBE_PORTS}
          onChange={(p) => update({ tcp_active_probe: p })}
        />

        <div style={{ ...helpStyle, marginTop: "var(--space-sm)" }}>
          For protocols that need multi-step handshakes, encrypted
          payloads, or framing too dynamic for these blocks, ship a
          sibling <code>{"<driver_id>_discovery.py"}</code> module
          alongside the driver file (Phase 9 Python companion).
        </div>
      </div>

      {/* Tier 3 */}
      <div style={sectionStyle}>
        <label style={labelStyle}>Tier 3 — active probes</label>
        <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--space-sm)" }}>
          {ALLOWED_ACTIVE_PROBES.map((probe) => {
            const checked = (hints.active_probes ?? []).includes(probe);
            return (
              <label key={probe} style={{ display: "flex", alignItems: "center", gap: 4 }}>
                <input
                  type="checkbox"
                  checked={checked}
                  onChange={(e) => {
                    const current = new Set(hints.active_probes ?? []);
                    if (e.target.checked) current.add(probe);
                    else current.delete(probe);
                    update({ active_probes: current.size ? Array.from(current) : undefined });
                  }}
                />
                <code style={{ fontSize: "11px" }}>{probe}</code>
              </label>
            );
          })}
        </div>
        <div style={helpStyle}>
          Targeted TCP probes that fire when the device responds to a port
          scan. Adding a new probe ID requires landing it in
          <code>protocol_prober.py</code> first.
        </div>
      </div>

      {/* Tier 4 enrichment */}
      <div style={sectionStyle}>
        <label style={labelStyle}>Tier 4 — soft enrichment hints</label>

        <div style={{ marginBottom: "var(--space-md)" }}>
          <label style={{ ...labelStyle, fontWeight: 400 }}>SNMP PEN (IANA Private Enterprise Number)</label>
          <input
            type="number"
            placeholder="e.g. 17049 for Extron"
            value={hints.snmp_pen ?? ""}
            onChange={(e) => {
              const n = parseInt(e.target.value);
              update({ snmp_pen: !isNaN(n) && n > 0 ? n : undefined });
            }}
            style={{ width: "200px" }}
          />
        </div>

        <div style={{ marginBottom: "var(--space-md)" }}>
          <label style={{ ...labelStyle, fontWeight: 400 }}>OUI prefixes (MAC vendor blocks)</label>
          <ListEditor
            items={hints.oui_prefixes ?? []}
            onChange={(items) => update({ oui_prefixes: items.length ? items : undefined })}
            placeholder="00:05:a6"
          />
        </div>

        <div style={{ marginBottom: "var(--space-md)" }}>
          <label style={{ ...labelStyle, fontWeight: 400 }}>Hostname regex patterns</label>
          <ListEditor
            items={hints.hostname_patterns ?? []}
            onChange={(items) => update({ hostname_patterns: items.length ? items : undefined })}
            placeholder="^DTP-.*"
          />
        </div>

        <div>
          <label style={{ ...labelStyle, fontWeight: 400 }}>Open AV ports</label>
          <OpenPortsEditor
            ports={hints.open_ports ?? []}
            onChange={(ports) => update({ open_ports: ports.length ? ports : undefined })}
          />
          <div style={helpStyle}>
            Vendor-specific TCP/UDP ports the device exposes (e.g.{" "}
            <code>4352</code> for PJLink, <code>17567</code> for Lightware
            LW3). Ports <code>{[...DISALLOWED_OPEN_PORTS].join(", ")}</code>{" "}
            are rejected by the runtime — they would match every web or
            SSH host on the LAN.
          </div>
        </div>

        <div style={helpStyle}>
          Soft signals never produce <em>identified</em> alone — they only
          contribute to the <em>possible</em> state.
        </div>
      </div>
    </div>
  );
}


function MdnsServicesEditor({
  entries,
  onChange,
}: {
  entries: Array<string | DriverDiscoveryMdnsEntry>;
  onChange: (entries: Array<string | DriverDiscoveryMdnsEntry>) => void;
}) {
  const update = (i: number, next: string | DriverDiscoveryMdnsEntry) => {
    const out = [...entries];
    out[i] = next;
    onChange(out);
  };

  const remove = (i: number) => onChange(entries.filter((_, j) => j !== i));

  return (
    <div>
      {entries.map((entry, i) => {
        const service = typeof entry === "string" ? entry : entry.service;
        const txtMatch =
          typeof entry === "object" && entry !== null && entry.txt_match
            ? entry.txt_match
            : null;
        return (
          <div
            key={i}
            style={{
              display: "flex",
              flexDirection: "column",
              gap: "var(--space-xs)",
              marginBottom: "var(--space-sm)",
              padding: "var(--space-xs)",
              border: "1px solid var(--border-color)",
              borderRadius: "var(--radius)",
            }}
          >
            <div style={{ display: "flex", gap: "var(--space-xs)", alignItems: "center" }}>
              <input
                type="text"
                value={service}
                placeholder="_pjlink._tcp.local."
                onChange={(e) => {
                  if (txtMatch) update(i, { service: e.target.value, txt_match: txtMatch });
                  else update(i, e.target.value);
                }}
                style={{ flex: 1, fontFamily: "var(--font-mono)", fontSize: "var(--font-size-sm)" }}
              />
              <button
                type="button"
                onClick={() =>
                  update(
                    i,
                    txtMatch
                      ? service
                      : { service, txt_match: { manufacturer: "" } },
                  )
                }
                style={{ fontSize: "11px", color: "var(--accent)" }}
              >
                {txtMatch ? "Drop filter" : "Add filter"}
              </button>
              <button
                type="button"
                onClick={() => remove(i)}
                style={{ padding: "2px", color: "var(--text-muted)" }}
              >
                <Trash2 size={14} />
              </button>
            </div>
            {txtMatch && (
              <TxtFilterEditor
                txtMatch={txtMatch}
                onChange={(next) => update(i, { service, txt_match: next })}
              />
            )}
          </div>
        );
      })}
      <button
        type="button"
        onClick={() => onChange([...entries, ""])}
        style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--space-xs)",
          fontSize: "var(--font-size-sm)",
          color: "var(--accent)",
          padding: "var(--space-xs) 0",
        }}
      >
        <Plus size={12} /> Add mDNS service
      </button>
    </div>
  );
}


function TxtFilterEditor({
  txtMatch,
  onChange,
}: {
  txtMatch: Record<string, string>;
  onChange: (next: Record<string, string>) => void;
}) {
  const entries = Object.entries(txtMatch);
  return (
    <div style={{ marginLeft: "var(--space-md)" }}>
      {entries.map(([k, v], idx) => (
        <div key={idx} style={{ display: "flex", gap: "var(--space-xs)", marginBottom: 2 }}>
          <input
            type="text"
            placeholder="key (e.g. manufacturer)"
            value={k}
            onChange={(e) => {
              const newKey = e.target.value;
              const next: Record<string, string> = {};
              entries.forEach(([kk, vv], j) => {
                next[j === idx ? newKey : kk] = vv;
              });
              onChange(next);
            }}
            style={{ width: 140, fontFamily: "var(--font-mono)", fontSize: "11px" }}
          />
          <input
            type="text"
            placeholder="value (e.g. Shure)"
            value={v}
            onChange={(e) => {
              const next = { ...txtMatch, [k]: e.target.value };
              onChange(next);
            }}
            style={{ flex: 1, fontFamily: "var(--font-mono)", fontSize: "11px" }}
          />
          <button
            type="button"
            onClick={() => {
              const next = { ...txtMatch };
              delete next[k];
              onChange(next);
            }}
            style={{ padding: "2px", color: "var(--text-muted)" }}
          >
            <Trash2 size={12} />
          </button>
        </div>
      ))}
      <button
        type="button"
        onClick={() => onChange({ ...txtMatch, "": "" })}
        style={{ fontSize: "11px", color: "var(--accent)" }}
      >
        + TXT pair
      </button>
    </div>
  );
}


function OpenPortsEditor({
  ports,
  onChange,
}: {
  ports: number[];
  onChange: (ports: number[]) => void;
}) {
  // Internal string buffer so blank rows can exist while the user is
  // typing. Only finite, in-range, non-disallowed values are written
  // back to the parent so the YAML output stays clean.
  const [buffer, setBuffer] = useState<string[]>(() =>
    ports.map((p) => String(p)),
  );

  // Sync down when the parent's canonical port list diverges (e.g.
  // another editor cleared the discovery section). Skip syncs that
  // would erase the user's in-progress edits.
  useEffect(() => {
    const fromParent = ports.map((p) => String(p));
    const fromBuffer = buffer
      .map((s) => parseInt(s, 10))
      .filter((n) => Number.isFinite(n) && n >= 1 && n <= 65535 && !DISALLOWED_OPEN_PORTS.has(n));
    const same =
      fromBuffer.length === ports.length &&
      fromBuffer.every((n, i) => n === ports[i]);
    if (!same) setBuffer(fromParent);
  }, [ports]); // eslint-disable-line react-hooks/exhaustive-deps

  const commit = (next: string[]) => {
    setBuffer(next);
    const valid: number[] = [];
    for (const s of next) {
      const n = parseInt(s, 10);
      if (Number.isFinite(n) && n >= 1 && n <= 65535 && !DISALLOWED_OPEN_PORTS.has(n)) {
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
    if (DISALLOWED_OPEN_PORTS.has(n)) return "disallowed (too generic)";
    return null;
  };

  return (
    <div>
      {buffer.map((raw, i) => {
        const err = portError(raw);
        return (
          <div
            key={i}
            style={{
              display: "flex",
              gap: "var(--space-xs)",
              marginBottom: "var(--space-xs)",
              alignItems: "center",
            }}
          >
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
              placeholder="e.g. 4352"
              style={{
                flex: 1,
                fontFamily: "var(--font-mono)",
                fontSize: "var(--font-size-sm)",
                borderColor: err ? "var(--danger)" : undefined,
              }}
            />
            {err && (
              <span style={{ color: "var(--danger)", fontSize: "11px" }}>
                {err}
              </span>
            )}
            <button
              type="button"
              onClick={() => commit(buffer.filter((_, j) => j !== i))}
              style={{ padding: "2px", color: "var(--text-muted)" }}
            >
              <Trash2 size={14} />
            </button>
          </div>
        );
      })}
      <button
        type="button"
        onClick={() => commit([...buffer, ""])}
        style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--space-xs)",
          fontSize: "var(--font-size-sm)",
          color: "var(--accent)",
          padding: "var(--space-xs) 0",
        }}
      >
        <Plus size={12} /> Add port
      </button>
    </div>
  );
}


function ListEditor({
  items,
  onChange,
  placeholder,
  inputType = "text",
}: {
  items: string[];
  onChange: (items: string[]) => void;
  placeholder?: string;
  inputType?: string;
}) {
  return (
    <div>
      {items.map((item, i) => (
        <div
          key={i}
          style={{
            display: "flex",
            gap: "var(--space-xs)",
            marginBottom: "var(--space-xs)",
            alignItems: "center",
          }}
        >
          <input
            type={inputType}
            value={item}
            onChange={(e) => {
              const next = [...items];
              next[i] = e.target.value;
              onChange(next);
            }}
            placeholder={placeholder}
            style={{
              flex: 1,
              fontFamily: "var(--font-mono)",
              fontSize: "var(--font-size-sm)",
            }}
          />
          <button
            type="button"
            onClick={() => onChange(items.filter((_, j) => j !== i))}
            style={{ padding: "2px", color: "var(--text-muted)" }}
          >
            <Trash2 size={14} />
          </button>
        </div>
      ))}
      <button
        type="button"
        onClick={() => onChange([...items, ""])}
        style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--space-xs)",
          fontSize: "var(--font-size-sm)",
          color: "var(--accent)",
          padding: "var(--space-xs) 0",
        }}
      >
        <Plus size={12} /> Add
      </button>
    </div>
  );
}


function CustomProbeEditor({
  kind,
  probe,
  disallowedPorts,
  onChange,
}: {
  kind: "udp" | "tcp";
  probe?: DriverDiscoveryCustomProbe;
  disallowedPorts: ReadonlySet<number>;
  onChange: (probe: DriverDiscoveryCustomProbe | undefined) => void;
}) {
  const enabled = probe !== undefined;
  const label =
    kind === "udp" ? "UDP broadcast probe" : "TCP active probe";
  const idPrefix = `discovery-custom-${kind}`;
  const portInvalid =
    enabled && (probe!.port == null || disallowedPorts.has(probe!.port));

  const update = (partial: Partial<DriverDiscoveryCustomProbe>) => {
    onChange({ ...(probe ?? { port: 0, send: {}, response_match: {} }), ...partial });
  };

  const updateSend = (next: { hex?: string; ascii?: string }) => {
    onChange({
      ...(probe ?? { port: 0, send: {}, response_match: {} }),
      send: next,
    });
  };

  const updateMatch = (
    next: Partial<NonNullable<DriverDiscoveryCustomProbe["response_match"]>>,
  ) => {
    onChange({
      ...(probe ?? { port: 0, send: {}, response_match: {} }),
      response_match: { ...(probe?.response_match ?? {}), ...next },
    });
  };

  if (!enabled) {
    return (
      <div style={{ marginBottom: "var(--space-sm)" }}>
        <button
          type="button"
          onClick={() =>
            onChange({
              port: kind === "udp" ? 6000 : 6107,
              send: { ascii: "" },
              response_match: { contains: "" },
            })
          }
          style={{
            fontSize: "var(--font-size-sm)",
            color: "var(--accent)",
            padding: "var(--space-xs) 0",
            display: "flex",
            alignItems: "center",
            gap: "var(--space-xs)",
          }}
        >
          <Plus size={12} /> Declare {label}
        </button>
      </div>
    );
  }

  const sendIsHex = probe!.send?.hex !== undefined;

  return (
    <div
      style={{
        marginBottom: "var(--space-md)",
        padding: "var(--space-sm)",
        border: "1px solid var(--border-color)",
        borderRadius: "var(--radius)",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          marginBottom: "var(--space-sm)",
        }}
      >
        <strong style={{ fontSize: "var(--font-size-sm)" }}>{label}</strong>
        <button
          type="button"
          onClick={() => onChange(undefined)}
          style={{ padding: "2px", color: "var(--text-muted)" }}
          title="Remove this probe"
        >
          <Trash2 size={14} />
        </button>
      </div>

      <div
        style={{ display: "grid", gridTemplateColumns: "100px 1fr", gap: "var(--space-sm)" }}
      >
        <label htmlFor={`${idPrefix}-port`} style={{ alignSelf: "center", fontSize: "var(--font-size-sm)" }}>
          Port
        </label>
        <div>
          <input
            id={`${idPrefix}-port`}
            type="number"
            min={1}
            max={65535}
            value={probe!.port || ""}
            onChange={(e) => update({ port: parseInt(e.target.value) || 0 })}
            style={{
              width: 120,
              borderColor: portInvalid ? "var(--error)" : undefined,
            }}
          />
          {portInvalid && (
            <div style={{ fontSize: 11, color: "var(--error)", marginTop: 2 }}>
              Port reserved for a built-in handler. Use the named opt-in
              instead. Disallowed: {[...disallowedPorts].join(", ")}.
            </div>
          )}
        </div>

        <label style={{ alignSelf: "center", fontSize: "var(--font-size-sm)" }}>Send</label>
        <div style={{ display: "flex", gap: "var(--space-xs)", alignItems: "center" }}>
          <select
            value={sendIsHex ? "hex" : "ascii"}
            onChange={(e) =>
              updateSend(
                e.target.value === "hex"
                  ? { hex: probe!.send?.hex ?? "" }
                  : { ascii: probe!.send?.ascii ?? "" },
              )
            }
          >
            <option value="ascii">ascii</option>
            <option value="hex">hex</option>
          </select>
          <input
            type="text"
            placeholder={sendIsHex ? "00010203" : "WHOIS\\r\\n"}
            value={sendIsHex ? probe!.send?.hex ?? "" : probe!.send?.ascii ?? ""}
            onChange={(e) =>
              updateSend(sendIsHex ? { hex: e.target.value } : { ascii: e.target.value })
            }
            style={{ flex: 1, fontFamily: "var(--font-mono)" }}
          />
        </div>

        <label style={{ alignSelf: "start", fontSize: "var(--font-size-sm)", paddingTop: 4 }}>
          Response match
        </label>
        <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-xs)" }}>
          <input
            type="text"
            placeholder="starts_with_hex (e.g. AA55)"
            value={probe!.response_match?.starts_with_hex ?? ""}
            onChange={(e) =>
              updateMatch({ starts_with_hex: e.target.value || undefined })
            }
            style={{ fontFamily: "var(--font-mono)", fontSize: "var(--font-size-sm)" }}
          />
          <input
            type="text"
            placeholder="contains (substring, e.g. NovaStar)"
            value={probe!.response_match?.contains ?? ""}
            onChange={(e) =>
              updateMatch({ contains: e.target.value || undefined })
            }
            style={{ fontFamily: "var(--font-mono)", fontSize: "var(--font-size-sm)" }}
          />
          <input
            type="text"
            placeholder="regex (latin-1, e.g. ^NS-([A-Z0-9]+))"
            value={probe!.response_match?.regex ?? ""}
            onChange={(e) =>
              updateMatch({ regex: e.target.value || undefined })
            }
            style={{ fontFamily: "var(--font-mono)", fontSize: "var(--font-size-sm)" }}
          />
          <div style={{ fontSize: 11, color: "var(--text-muted)" }}>
            All matchers AND together. At least one must be present for the
            probe to identify a device.
          </div>
        </div>

        <label htmlFor={`${idPrefix}-timeout`} style={{ alignSelf: "center", fontSize: "var(--font-size-sm)" }}>
          Timeout (ms)
        </label>
        <input
          id={`${idPrefix}-timeout`}
          type="number"
          min={1}
          max={10000}
          placeholder={kind === "udp" ? "2000" : "3000"}
          value={probe!.timeout_ms ?? ""}
          onChange={(e) => {
            const n = parseInt(e.target.value);
            update({ timeout_ms: !isNaN(n) && n > 0 ? n : undefined });
          }}
          style={{ width: 120 }}
        />

        <label style={{ alignSelf: "center", fontSize: "var(--font-size-sm)" }}>Generic</label>
        <div>
          <label style={{ display: "flex", alignItems: "center", gap: "var(--space-xs)" }}>
            <input
              type="checkbox"
              checked={probe!.generic ?? false}
              onChange={(e) => update({ generic: e.target.checked || undefined })}
            />
            <span style={{ fontSize: "var(--font-size-sm)" }}>
              Cross-vendor probe (matches every device speaking a standard)
            </span>
          </label>
          <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>
            Set this when the probe identifies devices from multiple vendors.
            The matcher will demote this driver to an alternative when a
            vendor-specific driver claims the response via{" "}
            <code>vendor_aliases</code>.
          </div>
        </div>

        <label style={{ alignSelf: "start", fontSize: "var(--font-size-sm)", paddingTop: 4 }}>
          Extract
        </label>
        <ExtractEditor
          rules={probe!.extract ?? {}}
          onChange={(rules) =>
            update({
              extract:
                Object.keys(rules).length > 0 ? rules : undefined,
            })
          }
        />
      </div>
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
    for (const [k, v] of entries) {
      out[k === oldKey ? newKey : k] = v;
    }
    onChange(out);
  };

  const setValue = (key: string, rule: DriverDiscoveryExtractRule) => {
    onChange({ ...rules, [key]: rule });
  };

  const remove = (key: string) => {
    const out = { ...rules };
    delete out[key];
    onChange(out);
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-xs)" }}>
      {entries.map(([key, rule]) => {
        const isRegex = typeof rule === "object" && rule !== null;
        const reserved = key === "manufacturer" || key === "make";
        return (
          <div
            key={key}
            style={{
              display: "grid",
              gridTemplateColumns: "150px 80px 1fr 60px auto",
              gap: "var(--space-xs)",
              alignItems: "center",
            }}
          >
            <input
              type="text"
              value={key}
              placeholder="field name"
              onChange={(e) => setKey(key, e.target.value)}
              style={{
                fontSize: "var(--font-size-sm)",
                fontFamily: "var(--font-mono)",
                color: reserved ? "var(--accent)" : undefined,
              }}
              title={reserved ? "Reserved key — feeds Tier 4 vendor_string" : undefined}
            />
            <select
              value={isRegex ? "regex" : "literal"}
              onChange={(e) =>
                setValue(
                  key,
                  e.target.value === "regex"
                    ? { regex: typeof rule === "string" ? rule : rule.regex, group: 1 }
                    : typeof rule === "object" ? rule.regex : rule,
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
              style={{ fontFamily: "var(--font-mono)", fontSize: "var(--font-size-sm)" }}
            />
            {isRegex ? (
              <input
                type="number"
                min={0}
                value={rule.group ?? 1}
                onChange={(e) =>
                  setValue(key, { regex: rule.regex, group: parseInt(e.target.value) || 0 })
                }
                title="Capture group"
              />
            ) : (
              <span />
            )}
            <button
              type="button"
              onClick={() => remove(key)}
              style={{ padding: "2px", color: "var(--text-muted)" }}
            >
              <Trash2 size={14} />
            </button>
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
        style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--space-xs)",
          fontSize: "var(--font-size-sm)",
          color: "var(--accent)",
          padding: "var(--space-xs) 0",
        }}
      >
        <Plus size={12} /> Add extract field
      </button>
      <div style={{ fontSize: 11, color: "var(--text-muted)" }}>
        Reserved keys <code>manufacturer</code> and <code>make</code> feed
        the Tier 4 vendor_string soft-signal path; other fields are
        recorded as evidence metadata for the matcher's "Why?" panel.
      </div>
    </div>
  );
}
