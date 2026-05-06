import { Plus, Trash2 } from "lucide-react";
import type {
  DriverDefinition,
  DriverDiscoveryHints,
  DriverDiscoveryMdnsEntry,
} from "../../api/types";

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

        <div>
          <label style={{ ...labelStyle, fontWeight: 400 }}>Hostname regex patterns</label>
          <ListEditor
            items={hints.hostname_patterns ?? []}
            onChange={(items) => update({ hostname_patterns: items.length ? items : undefined })}
            placeholder="^DTP-.*"
          />
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
