import { useState, useEffect, useRef } from "react";
import { Radio, Zap, X, AlertCircle } from "lucide-react";
import * as api from "../../api/restClient";
import { IrLearnSession } from "../../api/irLearn";

// Standalone learn + raw-emit diagnostics for one IR port on a bridge card.
// This is for testing the emitter and codes directly on the bridge, without a
// downstream IR device bound to it. It never saves anything; captured/typed
// codes are just fired back through the port.

const btn: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 4,
  padding: "var(--space-xs) var(--space-sm)",
  background: "var(--bg-hover)",
  color: "var(--text-secondary)",
  border: "1px solid var(--border-color)",
  borderRadius: "var(--border-radius)",
  cursor: "pointer",
  fontSize: "var(--font-size-sm)",
};

export function BridgeIrTools({
  bridgeId,
  port,
  connected,
}: {
  bridgeId: string;
  port: string;
  connected: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [pronto, setPronto] = useState("");
  const [status, setStatus] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [learning, setLearning] = useState(false);
  const sessionRef = useRef<IrLearnSession | null>(null);

  useEffect(() => {
    return () => {
      sessionRef.current?.close();
      sessionRef.current = null;
    };
  }, []);

  const emit = async () => {
    const code = pronto.trim();
    if (!code) return;
    setStatus("sending…");
    setErr(null);
    try {
      await api.irEmit(bridgeId, { port, pronto: code, repeat: 1 });
      setStatus("sent");
    } catch (e) {
      setErr(e instanceof Error ? e.message : "emit failed");
      setStatus("");
    }
  };

  const learnOne = () => {
    sessionRef.current?.close();
    setLearning(true);
    setErr(null);
    setStatus("Point a remote at the bridge and press a button…");
    const session = new IrLearnSession(bridgeId, "one_off", {
      onCaptured: (p) => {
        setPronto(p);
        setStatus("Captured. Test it below.");
        setLearning(false);
        session.close();
      },
      onError: (_c, message) => {
        setErr(message);
        setLearning(false);
      },
      onStopped: () => setLearning(false),
    });
    sessionRef.current = session;
    session.start();
  };

  const stopLearn = () => sessionRef.current?.stop();

  if (!open) {
    return (
      <div style={{ marginTop: "var(--space-sm)" }}>
        <button
          style={{ ...btn, opacity: connected ? 1 : 0.5 }}
          onClick={() => setOpen(true)}
          disabled={!connected}
          title={connected ? "Learn or fire a code to test this emitter — nothing is saved" : "The bridge is offline"}
        >
          <Radio size={14} /> Test this port
        </button>
      </div>
    );
  }

  return (
    <div
      style={{
        marginTop: "var(--space-sm)",
        padding: "var(--space-sm)",
        border: "1px dashed var(--border-color)",
        borderRadius: "var(--border-radius)",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 6 }}>
        <span style={{ fontSize: "var(--font-size-sm)", fontWeight: 600 }}>Test this port</span>
        <button style={btn} onClick={() => { stopLearn(); setOpen(false); }}>
          <X size={14} />
        </button>
      </div>
      <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: 6 }}>
        A diagnostic to check the emitter — learn or fire a code without saving it.
        To build a reusable code set, add an IR device on this port.
      </div>
      <div style={{ display: "flex", gap: "var(--space-sm)", marginBottom: 6 }}>
        {learning ? (
          <button style={btn} onClick={stopLearn}>Stop learning</button>
        ) : (
          <button style={{ ...btn, opacity: connected ? 1 : 0.5 }} onClick={learnOne} disabled={!connected}>
            <Radio size={14} /> Learn a code
          </button>
        )}
      </div>
      <textarea
        rows={2}
        style={{
          width: "100%",
          boxSizing: "border-box",
          fontFamily: "var(--font-mono)",
          fontSize: 11,
          padding: "var(--space-xs) var(--space-sm)",
          resize: "vertical",
        }}
        placeholder="Pronto hex to fire (0000 006D …)"
        value={pronto}
        onChange={(e) => setPronto(e.target.value)}
      />
      <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)", marginTop: 4 }}>
        <button
          style={{ ...btn, opacity: connected && pronto.trim() ? 1 : 0.5 }}
          onClick={emit}
          disabled={!connected || !pronto.trim()}
        >
          <Zap size={14} /> Test emit
        </button>
        {err ? (
          <span style={{ color: "var(--color-danger)", fontSize: 11, display: "inline-flex", alignItems: "center", gap: 4 }}>
            <AlertCircle size={12} /> {err}
          </span>
        ) : (
          status && <span style={{ fontSize: 11, color: "var(--text-muted)" }}>{status}</span>
        )}
      </div>
    </div>
  );
}
