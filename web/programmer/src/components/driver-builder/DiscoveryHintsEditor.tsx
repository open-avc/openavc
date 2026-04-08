import { Plus, Trash2 } from "lucide-react";
import type { DriverDefinition, DriverDiscoveryHints } from "../../api/types";

interface DiscoveryHintsEditorProps {
  draft: DriverDefinition;
  onUpdate: (partial: Partial<DriverDefinition>) => void;
}

export function DiscoveryHintsEditor({ draft, onUpdate }: DiscoveryHintsEditorProps) {
  const hints: DriverDiscoveryHints = draft.discovery ?? {};

  const update = (partial: Partial<DriverDiscoveryHints>) => {
    onUpdate({ discovery: { ...hints, ...partial } });
  };

  const labelStyle: React.CSSProperties = {
    display: "block",
    fontSize: "var(--font-size-sm)",
    color: "var(--text-secondary)",
    marginBottom: "var(--space-xs)",
  };

  const rowStyle: React.CSSProperties = {
    marginBottom: "var(--space-md)",
  };

  const helpStyle: React.CSSProperties = {
    fontSize: "11px",
    color: "var(--text-muted)",
    marginTop: "var(--space-xs)",
  };

  return (
    <div>
      <p
        style={{
          fontSize: "var(--font-size-sm)",
          color: "var(--text-muted)",
          marginBottom: "var(--space-md)",
        }}
      >
        Discovery hints help OpenAVC automatically identify this device type
        during network scans. All fields are optional but improve discovery accuracy.
      </p>

      {/* Ports */}
      <div style={rowStyle}>
        <label style={labelStyle}>TCP Ports</label>
        <ListEditor
          items={(hints.ports ?? []).map(String)}
          onChange={(items) => update({ ports: items.map((s) => parseInt(s)).filter((n) => !isNaN(n)) })}
          placeholder="e.g., 23"
          inputType="number"
        />
        <div style={helpStyle}>
          TCP ports this device type typically listens on. Used during port scanning.
        </div>
      </div>

      {/* MAC Prefixes */}
      <div style={rowStyle}>
        <label style={labelStyle}>MAC Address Prefixes (OUI)</label>
        <ListEditor
          items={hints.mac_prefixes ?? []}
          onChange={(items) => update({ mac_prefixes: items })}
          placeholder="e.g., 00:05:a6"
        />
        <div style={helpStyle}>
          First 3 octets of the manufacturer&apos;s MAC address (e.g., 00:05:a6 for Extron).
          Find these in device documentation or by checking ARP tables.
        </div>
      </div>

      {/* Protocols */}
      <div style={rowStyle}>
        <label style={labelStyle}>Protocol Identifiers</label>
        <ListEditor
          items={hints.protocols ?? []}
          onChange={(items) => update({ protocols: items })}
          placeholder="e.g., pjlink, extron_sis"
        />
        <div style={helpStyle}>
          Protocol names this driver implements. Matched against protocol probes during discovery.
        </div>
      </div>

      {/* mDNS Services */}
      <div style={rowStyle}>
        <label style={labelStyle}>mDNS Service Types</label>
        <ListEditor
          items={hints.mdns_services ?? []}
          onChange={(items) => update({ mdns_services: items })}
          placeholder="e.g., _http._tcp.local."
        />
        <div style={helpStyle}>
          mDNS/Bonjour service types the device advertises on the network.
        </div>
      </div>

      {/* Hostname Patterns */}
      <div style={rowStyle}>
        <label style={labelStyle}>Hostname Patterns (Regex)</label>
        <ListEditor
          items={hints.hostname_patterns ?? []}
          onChange={(items) => update({ hostname_patterns: items })}
          placeholder="e.g., ^DTP-.*"
        />
        <div style={helpStyle}>
          Regex patterns to match against device hostnames found via DNS or NetBIOS.
        </div>
      </div>
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
            onClick={() => onChange(items.filter((_, j) => j !== i))}
            style={{ padding: "2px", color: "var(--text-muted)" }}
          >
            <Trash2 size={14} />
          </button>
        </div>
      ))}
      <button
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
