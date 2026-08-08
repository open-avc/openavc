import { Plus, Trash2 } from "lucide-react";
import type { DriverBridgePortDef, DriverDefinition } from "../../api/types";
import { BRIDGE_PORT_KINDS } from "../../api/types";

interface BridgePortsEditorProps {
  draft: DriverDefinition;
  onUpdate: (partial: Partial<DriverDefinition>) => void;
}

const labelStyle: React.CSSProperties = {
  display: "block",
  fontSize: "var(--font-size-sm)",
  color: "var(--text-secondary)",
  marginBottom: "var(--space-xs)",
};

const helpStyle: React.CSSProperties = {
  fontSize: "11px",
  color: "var(--text-muted)",
  marginTop: "var(--space-xs)",
};

const KIND_LABELS: Record<string, string> = {
  serial: "Serial",
  ir: "IR",
  relay: "Relay",
};

/**
 * Edits the optional `bridge:` block — declares this driver as a bridge (a
 * device that exposes typed ports other devices connect through, e.g. a
 * serial-to-Ethernet or IR bridge) and the ports it advertises. Each port
 * row is merged with the delete-undefined pattern so hand-authored extra
 * keys survive edits verbatim.
 */
export function BridgePortsEditor({ draft, onUpdate }: BridgePortsEditorProps) {
  const bridge = draft.bridge;
  const enabled = bridge !== undefined;
  const ports = bridge?.ports ?? [];

  const writePorts = (next: DriverBridgePortDef[]) => {
    onUpdate({ bridge: { ...bridge, ports: next } });
  };

  const updatePort = (
    index: number,
    partial: Partial<DriverBridgePortDef>,
  ) => {
    // Merge, then strip undefined keys — cleared optional fields vanish from
    // the YAML instead of serializing as null; unnamed keys spread through.
    const merged = { ...ports[index], ...partial } as Record<string, unknown>;
    for (const k of Object.keys(merged)) {
      if (merged[k] === undefined) delete merged[k];
    }
    const next = ports.slice();
    next[index] = merged as unknown as DriverBridgePortDef;
    writePorts(next);
  };

  const addPort = () => {
    // Seed a sensible next id: serial:1, serial:2, ... matching the doc
    // example, so the row is valid the moment it appears.
    const used = new Set(ports.map((p) => p.id));
    let n = ports.length + 1;
    let id = `serial:${n}`;
    while (used.has(id)) {
      n++;
      id = `serial:${n}`;
    }
    writePorts([...ports, { id, kind: "serial" }]);
  };

  const removePort = (index: number) => {
    writePorts(ports.filter((_, i) => i !== index));
  };

  const toggleEnabled = (on: boolean) => {
    if (on) {
      onUpdate({ bridge: { ports: [{ id: "serial:1", kind: "serial" }] } });
      return;
    }
    if (ports.length > 0) {
      const ok = window.confirm(
        `Removing the bridge declaration deletes ${ports.length} port${ports.length === 1 ? "" : "s"}. Continue?`,
      );
      if (!ok) return;
    }
    onUpdate({ bridge: undefined });
  };

  return (
    <div>
      <label
        style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--space-xs)",
          fontSize: "var(--font-size-sm)",
          marginBottom: "var(--space-xs)",
        }}
      >
        <input
          type="checkbox"
          checked={enabled}
          onChange={(e) => toggleEnabled(e.target.checked)}
        />
        This device is a bridge other devices connect through
      </label>
      <div style={{ ...helpStyle, marginTop: 0, marginBottom: "var(--space-md)" }}>
        A bridge advertises typed ports (a serial-to-Ethernet or IR bridge).
        A serial port vends a transparent TCP pass-through; IR and relay
        ports route commands through the bridge at send time.
      </div>

      {enabled && (
        <>
          {ports.map((port, i) => (
            <div
              key={i}
              style={{
                display: "flex",
                gap: "var(--space-sm)",
                alignItems: "end",
                marginBottom: "var(--space-sm)",
              }}
            >
              <div style={{ width: 140 }}>
                <label style={labelStyle}>Port ID</label>
                <input
                  value={port.id ?? ""}
                  onChange={(e) => updatePort(i, { id: e.target.value })}
                  placeholder="serial:1"
                  style={{ width: "100%", fontFamily: "var(--font-mono)" }}
                />
              </div>
              <div style={{ width: 110 }}>
                <label style={labelStyle}>Kind</label>
                <select
                  value={port.kind}
                  onChange={(e) => {
                    const kind = e.target.value as DriverBridgePortDef["kind"];
                    // A pass-through port only means something on a serial
                    // port — drop it when the kind changes so the YAML
                    // doesn't keep an invisible, uneditable leftover.
                    updatePort(i, {
                      kind,
                      passthrough_port:
                        kind === "serial" ? port.passthrough_port : undefined,
                    });
                  }}
                  style={{ width: "100%" }}
                >
                  {BRIDGE_PORT_KINDS.map((k) => (
                    <option key={k} value={k}>
                      {KIND_LABELS[k] ?? k}
                    </option>
                  ))}
                  {port.kind !== undefined &&
                    !(BRIDGE_PORT_KINDS as readonly string[]).includes(
                      port.kind,
                    ) && (
                      <option value={port.kind}>
                        {String(port.kind)} (not supported)
                      </option>
                    )}
                </select>
              </div>
              {port.kind === "serial" && (
                <div style={{ width: 130 }}>
                  <label style={labelStyle}>Pass-through Port</label>
                  <input
                    type="number"
                    min={1}
                    max={65535}
                    value={port.passthrough_port ?? ""}
                    onChange={(e) => {
                      const raw = e.target.value;
                      const parsed = parseInt(raw, 10);
                      updatePort(i, {
                        passthrough_port:
                          raw === "" || !Number.isFinite(parsed)
                            ? undefined
                            : parsed,
                      });
                    }}
                    placeholder="4999"
                    style={{ width: "100%" }}
                  />
                </div>
              )}
              <div style={{ flex: 1 }}>
                <label style={labelStyle}>Label</label>
                <input
                  value={port.label ?? ""}
                  onChange={(e) =>
                    updatePort(i, { label: e.target.value || undefined })
                  }
                  placeholder="e.g. Serial Port 1"
                  style={{ width: "100%" }}
                />
              </div>
              <button
                onClick={() => removePort(i)}
                title="Remove this port"
                style={{ padding: "6px 2px", color: "var(--text-muted)" }}
              >
                <Trash2 size={14} />
              </button>
            </div>
          ))}

          <button
            onClick={addPort}
            style={{
              display: "flex",
              alignItems: "center",
              gap: "var(--space-xs)",
              padding: "var(--space-sm) var(--space-md)",
              borderRadius: "var(--border-radius)",
              background: "var(--bg-hover)",
              fontSize: "var(--font-size-sm)",
              marginTop: "var(--space-xs)",
            }}
          >
            <Plus size={14} /> Add Port
          </button>

          <div style={{ ...helpStyle, marginTop: "var(--space-md)" }}>
            Downstream devices pick a port by its ID when they connect through
            this bridge (serial ports need the TCP pass-through port the bridge
            pipes that line on, e.g. 4999). The port declaration is valid in
            YAML, but the runtime behavior behind a port needs a Python driver:
            pushing serial line settings to the hardware, and emitting or
            learning IR codes for IR ports.
          </div>
        </>
      )}
    </div>
  );
}
