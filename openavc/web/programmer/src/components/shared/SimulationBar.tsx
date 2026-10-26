import { useState } from "react";
import { getTunnelPrefix } from "../../api/restClient";
import { useConnectionStore } from "../../store/connectionStore";
import { showError } from "../../store/toastStore";

/**
 * The notice on every view while the devices are simulated, and while
 * simulation is left on but not running.
 *
 * Simulation stays on through a restart until somebody stops it, so a space
 * left simulating would otherwise keep its devices off the real equipment
 * with nothing in the IDE but the sidebar button to say so. The panel and the
 * cloud portal mark it too. The second form is a comeback that failed, or a
 * simulator that stopped by itself: the devices are on the real equipment
 * now, the server's sentence says why, and Turn Off is the only way to keep
 * the next restart from simulating again.
 */
export function SimulationBar() {
  const active = useConnectionStore((s) => Boolean(s.liveState["system.simulation_active"]));
  const error = useConnectionStore((s) => String(s.liveState["system.simulation_error"] ?? ""));
  const [busy, setBusy] = useState(false);

  if (!active && !error) return null;

  const post = async (action: "start" | "stop", failure: string) => {
    setBusy(true);
    try {
      const res = await fetch(`${getTunnelPrefix()}/api/simulation/${action}`, { method: "POST" });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        showError(body.detail || failure);
      }
    } catch {
      showError(failure);
    } finally {
      setBusy(false);
    }
  };

  const tint = active ? "255, 152, 0" : "244, 67, 54";
  const buttonStyle: React.CSSProperties = {
    padding: "2px 10px",
    borderRadius: 4,
    border: `1px solid rgba(${tint}, 0.5)`,
    background: "transparent",
    color: "var(--text-primary)",
    fontSize: 12,
    cursor: busy ? "default" : "pointer",
    whiteSpace: "nowrap",
  };
  const primaryStyle: React.CSSProperties = {
    ...buttonStyle,
    border: "none",
    background: `rgb(${tint})`,
    color: "#fff",
  };

  return (
    <div
      role="status"
      data-testid="simulation-bar"
      style={{
        display: "flex", alignItems: "center", justifyContent: "space-between",
        gap: "var(--space-md)",
        padding: "var(--space-sm) var(--space-md)",
        background: `rgba(${tint}, 0.12)`,
        borderBottom: `1px solid rgba(${tint}, 0.3)`,
        fontSize: 13, color: "var(--text-primary)", flexShrink: 0,
      }}
    >
      {active ? (
        <span>
          <strong>Simulation is on.</strong> The devices are connected to
          simulators, not the real equipment. It stays on after a restart until
          you stop it.
        </span>
      ) : (
        <span>
          {error} Simulation comes back at the next restart unless you turn it off.
        </span>
      )}
      <div style={{ display: "flex", gap: "var(--space-sm)", flexShrink: 0 }}>
        {active ? (
          <button
            type="button"
            disabled={busy}
            onClick={() => { void post("stop", "Failed to stop simulation"); }}
            style={primaryStyle}
          >
            Stop Simulation
          </button>
        ) : (
          <>
            <button
              type="button"
              disabled={busy}
              onClick={() => { void post("start", "Failed to start simulation"); }}
              style={primaryStyle}
            >
              Start Simulation
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => { void post("stop", "Failed to turn off simulation"); }}
              style={buttonStyle}
            >
              Turn Off Simulation
            </button>
          </>
        )}
      </div>
    </div>
  );
}
