/**
 * Shown by device-backed surfaces (layout.requires_device) when no unit is
 * connected: a plain explanation of how a unit appears, plus the add-virtual
 * path when the plugin declares virtual models. Replaces the old behavior of
 * rendering the static fallback grid as if hardware were attached.
 */
import { useState, useEffect } from "react";
import { Usb } from "lucide-react";
import { NetworkDeckDialog } from "./NetworkDeckDialog";
import { addVirtualUnit } from "./deckHelpers";
import type { SurfaceLayout } from "./types";

export function NoDeviceState({
  pluginId,
  layout,
  config,
  onConfigChange,
}: {
  pluginId: string;
  layout: SurfaceLayout;
  config: Record<string, unknown>;
  onConfigChange: (config: Record<string, unknown>) => void;
}) {
  const noun = layout.device_label || "device";
  const models = layout.virtual_models ?? [];
  const [model, setModel] = useState(models[0] ?? "");
  const [pending, setPending] = useState(false);
  const [showNetwork, setShowNetwork] = useState(false);

  // If the save fails silently, don't leave the button dead forever.
  useEffect(() => {
    if (!pending) return;
    const timer = setTimeout(() => setPending(false), 10000);
    return () => clearTimeout(timer);
  }, [pending]);

  const add = () => {
    if (!model || pending) return;
    onConfigChange(addVirtualUnit(config, model).next);
    setPending(true);
  };

  return (
    <div
      style={{
        maxWidth: 460,
        margin: "var(--space-xl) auto",
        padding: "var(--space-xl)",
        border: "1px solid var(--border-color)",
        borderRadius: "var(--border-radius)",
        background: "var(--bg-surface)",
        textAlign: "center",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        gap: "var(--space-md)",
      }}
    >
      <Usb size={40} strokeWidth={1.2} style={{ color: "var(--text-muted)" }} />
      <div style={{ fontWeight: 600 }}>No {noun} detected</div>
      <div
        style={{
          fontSize: "var(--font-size-sm)",
          color: "var(--text-secondary)",
          lineHeight: 1.6,
        }}
      >
        Connect a {noun} by USB and it appears here automatically, ready to
        set up.
      </div>
      {layout.network && (
        <button
          onClick={() => setShowNetwork(true)}
          style={{
            padding: "var(--space-xs) var(--space-md)",
            borderRadius: "var(--border-radius)",
            border: "1px dashed var(--border-color)",
            background: "transparent",
            color: "var(--text-secondary)",
            fontSize: "var(--font-size-sm)",
            cursor: "pointer",
          }}
          title="Add a deck reached over the network (Network Dock or built-in Ethernet)"
        >
          Add a network {noun}…
        </button>
      )}
      {showNetwork && (
        <NetworkDeckDialog
          pluginId={pluginId}
          config={config}
          onConfigChange={onConfigChange}
          onClose={() => setShowNetwork(false)}
        />
      )}
      {models.length > 0 && (
        <>
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: "var(--space-sm)",
              width: "100%",
              color: "var(--text-muted)",
              fontSize: 11,
            }}
          >
            <span style={{ flex: 1, borderTop: "1px solid var(--border-color)" }} />
            or
            <span style={{ flex: 1, borderTop: "1px solid var(--border-color)" }} />
          </div>
          <div style={{ display: "flex", gap: "var(--space-sm)" }}>
            <select
              value={model}
              onChange={(e) => setModel(e.target.value)}
              style={{
                padding: "var(--space-xs) var(--space-sm)",
                borderRadius: "var(--border-radius)",
                border: "1px solid var(--border-color)",
                background: "var(--bg-surface)",
                color: "var(--text-primary)",
                fontSize: "var(--font-size-sm)",
              }}
            >
              {models.map((m) => (
                <option key={m} value={m}>{m}</option>
              ))}
            </select>
            <button
              onClick={add}
              disabled={pending}
              style={{
                padding: "var(--space-xs) var(--space-md)",
                borderRadius: "var(--border-radius)",
                background: "var(--accent-bg)",
                color: "var(--text-on-accent)",
                fontSize: "var(--font-size-sm)",
                fontWeight: 500,
                cursor: pending ? "default" : "pointer",
                opacity: pending ? 0.6 : 1,
              }}
            >
              {pending ? "Starting..." : `Add virtual ${noun}`}
            </button>
          </div>
          <div
            style={{
              fontSize: 11,
              color: "var(--text-muted)",
              lineHeight: 1.6,
              maxWidth: 360,
            }}
          >
            {pending
              ? `Saving... the virtual ${noun} appears here in a few seconds.`
              : `A virtual ${noun} works exactly like plugged-in hardware: build and test the layout now, and a real ${noun} picks it up the moment it's connected.`}
          </div>
        </>
      )}
    </div>
  );
}
