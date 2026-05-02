import { Plus, Trash2 } from "lucide-react";
import type { DriverDefinition } from "../../api/types";

interface LifecycleEditorProps {
  draft: DriverDefinition;
  onUpdate: (partial: Partial<DriverDefinition>) => void;
}

/**
 * Edits the `on_connect` lifecycle hook — the sequence of wire strings sent
 * immediately after the device connects (and after any auth handshake). Used
 * for verbose-mode toggles, "GET ALL" requests, push subscriptions, etc.
 *
 * Real-world examples from the community fleet:
 *   - Extron SIS:    "\x1b3CV\r\n"                 (verbose mode 3)
 *   - Shure:         "< GET ALL >"                  (initial state dump)
 *   - Yamaha MTX:    "devstatus runmode\n", ...    (multi-line bring-up)
 *   - Behringer X32: "/xremote", "/info"           (OSC subscriptions)
 *   - Christie:      "(SST+CONF?)\r", "(SST+VERS?)\r"
 */
export function LifecycleEditor({ draft, onUpdate }: LifecycleEditorProps) {
  const items = draft.on_connect ?? [];

  const update = (next: string[]) => {
    // Drop the field entirely when the list is empty so we don't write
    // `on_connect: []` into YAML for drivers that don't need it.
    onUpdate({ on_connect: next.length ? next : undefined });
  };

  const addItem = () => update([...items, ""]);
  const removeItem = (i: number) => update(items.filter((_, idx) => idx !== i));
  const updateItem = (i: number, value: string) => {
    const next = [...items];
    next[i] = value;
    update(next);
  };

  const transport = draft.transport;
  const placeholder =
    transport === "osc"
      ? "/xremote"
      : transport === "http"
        ? "command_name_or_path"
        : transport === "serial" || transport === "tcp" || transport === "udp"
          ? '"\\x1b3CV\\r\\n"  or  "GET ALL\\r"'
          : 'command string';

  const helpForTransport = (() => {
    switch (transport) {
      case "osc":
        return (
          <>
            OSC addresses sent on connect (no arguments — used for
            subscription registration). Behringer X32 uses{" "}
            <code>/xremote</code> here to start receiving state pushes.
          </>
        );
      case "http":
        return (
          <>
            For HTTP drivers, items are typically command names already
            defined under Commands — they run immediately after connect to
            seed initial state. Cisco RoomOS does this with{" "}
            <code>query_audio</code>, <code>query_standby</code>, etc.
          </>
        );
      default:
        return (
          <>
            Wire strings sent in order on every connect. Use{" "}
            <code>{"\\r"}</code>, <code>{"\\n"}</code>, <code>{"\\x1b"}</code>{" "}
            for control bytes. Use <code>{"{config_key}"}</code> to substitute
            from device config (e.g. <code>{"{username}"}</code>).
            Common uses: enable verbose mode, request initial state, register
            for push notifications.
          </>
        );
    }
  })();

  return (
    <div style={{ marginTop: "var(--space-xl)" }}>
      <div
        style={{
          display: "flex",
          alignItems: "baseline",
          gap: "var(--space-md)",
          marginBottom: "var(--space-xs)",
        }}
      >
        <h3 style={{ fontSize: "var(--font-size-md)", margin: 0 }}>
          Connect Sequence
        </h3>
        <span style={{ fontSize: "11px", color: "var(--text-muted)" }}>
          on_connect — runs once per connection
        </span>
      </div>
      <p
        style={{
          fontSize: "var(--font-size-sm)",
          color: "var(--text-muted)",
          marginBottom: "var(--space-md)",
        }}
      >
        {helpForTransport}
      </p>

      {items.length === 0 && (
        <div
          style={{
            fontSize: "var(--font-size-sm)",
            color: "var(--text-muted)",
            padding: "var(--space-sm) var(--space-md)",
            border: "1px dashed var(--border-color)",
            borderRadius: "var(--border-radius)",
            marginBottom: "var(--space-sm)",
          }}
        >
          No connect commands. Most drivers don't need any — leave empty.
        </div>
      )}

      {items.map((item, i) => (
        <div
          key={i}
          style={{
            display: "flex",
            gap: "var(--space-sm)",
            marginBottom: "var(--space-xs)",
            alignItems: "center",
          }}
        >
          <span
            style={{
              fontSize: "11px",
              color: "var(--text-muted)",
              fontFamily: "var(--font-mono)",
              width: 24,
              textAlign: "right",
            }}
          >
            {i + 1}.
          </span>
          <input
            value={item}
            onChange={(e) => updateItem(i, e.target.value)}
            placeholder={placeholder}
            style={{
              flex: 1,
              fontFamily: "var(--font-mono)",
              fontSize: "var(--font-size-sm)",
            }}
          />
          <button
            onClick={() => removeItem(i)}
            style={{ padding: "2px", color: "var(--text-muted)" }}
            title="Remove"
          >
            <Trash2 size={14} />
          </button>
        </div>
      ))}

      <button
        onClick={addItem}
        style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--space-xs)",
          padding: "var(--space-sm) var(--space-md)",
          borderRadius: "var(--border-radius)",
          background: "var(--bg-hover)",
          fontSize: "var(--font-size-sm)",
          marginTop: "var(--space-sm)",
        }}
      >
        <Plus size={14} /> Add Step
      </button>
    </div>
  );
}
