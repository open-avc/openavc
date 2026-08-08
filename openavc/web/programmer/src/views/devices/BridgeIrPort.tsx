import { useState } from "react";
import { Plus, ExternalLink, Loader2, AlertCircle } from "lucide-react";
import { useProjectStore } from "../../store/projectStore";
import type { DeviceConfig } from "../../api/types";
import { BridgeIrTools } from "./BridgeIrTools";

// Per-IR-port controls on a bridge card. The bridge port is the entry point to
// IR control: from here you create (or open) the IR device that holds this
// emitter's code set. Codes live on that device — each becomes a normal device
// command — so panel buttons and macros bind to them the usual way. The raw
// learn/emit tools below are a diagnostic only (they save nothing); real
// authoring happens on the IR device's IR Codes editor.

const btn: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 6,
  padding: "var(--space-xs) var(--space-md)",
  background: "var(--bg-hover)",
  color: "var(--text-secondary)",
  border: "1px solid var(--border-color)",
  borderRadius: "var(--border-radius)",
  cursor: "pointer",
  fontSize: "var(--font-size-sm)",
};

function slugify(s: string): string {
  return (
    s
      .toLowerCase()
      .trim()
      .replace(/[^a-z0-9]+/g, "_")
      .replace(/^_+|_+$/g, "")
      .slice(0, 48) || "ir_device"
  );
}

function uniqueId(base: string, taken: Set<string>): string {
  if (!taken.has(base)) return base;
  let n = 2;
  while (taken.has(`${base}_${n}`)) n++;
  return `${base}_${n}`;
}

export function BridgeIrPort({
  bridgeId,
  portId,
  portLabel,
  connected,
  bound,
  onOpenDevice,
}: {
  bridgeId: string;
  portId: string;
  portLabel: string;
  connected: boolean;
  bound: DeviceConfig[];
  onOpenDevice?: (deviceId: string) => void;
}) {
  const update = useProjectStore((s) => s.update);
  const save = useProjectStore((s) => s.save);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const addIrDevice = async () => {
    setCreating(true);
    setError(null);
    try {
      const current = useProjectStore.getState().project;
      const taken = new Set((current?.devices ?? []).map((d) => d.id));
      const name = `IR Device (${portLabel})`;
      const id = uniqueId(slugify(`${bridgeId}_${portId}`), taken);
      const newDevice: DeviceConfig = {
        id,
        driver: "generic_ir",
        name,
        config: { ir_codes: {} },
      };
      update({
        devices: [...(current?.devices ?? []), newDevice],
        connections: {
          ...(current?.connections ?? {}),
          [id]: { bridge: bridgeId, bridge_port: portId },
        },
      });
      await save();
      onOpenDevice?.(id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not add the IR device.");
    } finally {
      setCreating(false);
    }
  };

  return (
    <div style={{ marginTop: "var(--space-sm)" }}>
      <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--space-sm)", alignItems: "center" }}>
        {bound.length === 0 ? (
          <button style={btn} onClick={addIrDevice} disabled={creating}>
            {creating ? <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} /> : <Plus size={14} />}
            {creating ? "Adding…" : "Add IR Device"}
          </button>
        ) : (
          bound.map((d) => (
            <button key={d.id} style={btn} onClick={() => onOpenDevice?.(d.id)}>
              <ExternalLink size={14} /> Open {d.name || d.id}
            </button>
          ))
        )}
      </div>
      {bound.length === 0 && (
        <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4 }}>
          Creates an IR device bound to this port. Build its code set (learn, paste
          Pronto, or type a sendir string) on its device page — each code becomes
          a command you can put on a panel button or call from a macro.
        </div>
      )}
      {error && (
        <div style={{ color: "var(--color-danger)", fontSize: 11, marginTop: 4, display: "inline-flex", alignItems: "center", gap: 4 }}>
          <AlertCircle size={12} /> {error}
        </div>
      )}
      {/* Diagnostic only — fires a code to test the emitter; nothing is saved. */}
      <BridgeIrTools bridgeId={bridgeId} port={portId} connected={connected} />
    </div>
  );
}
