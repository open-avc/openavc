import { useEffect, useMemo, useState } from "react";
import {
  Search,
  ChevronRight,
  ArrowLeft,
  X,
  Plus,
  Zap,
  AlertCircle,
  Check,
} from "lucide-react";
import * as api from "../../api/restClient";
import type { IrDbDevice, IrDbFunction } from "../../api/deviceClient";

// Search flow for the external IR code database (IRDB): brand -> code set ->
// function, then take one rendered Pronto code into the code-set editor. The
// platform fetches and renders; this component is the browse-and-pick UI. It is
// vendor-neutral (any IR bridge emits the resulting Pronto).

const iconBtn: React.CSSProperties = {
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

const rowBtn: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  width: "100%",
  padding: "var(--space-sm)",
  background: "transparent",
  color: "var(--text-primary)",
  border: "none",
  borderBottom: "1px solid var(--border-color)",
  cursor: "pointer",
  fontSize: "var(--font-size-sm)",
  textAlign: "left",
};

const listBox: React.CSSProperties = {
  maxHeight: 260,
  overflowY: "auto",
  border: "1px solid var(--border-color)",
  borderRadius: "var(--border-radius)",
  background: "var(--bg-surface)",
};

function prontoPreview(pronto: string): string {
  return pronto.length > 34 ? pronto.slice(0, 34) + "…" : pronto;
}

export function IrDbSearch({
  canBridge,
  connected,
  bridgeId,
  bridgePort,
  onPick,
  onClose,
}: {
  canBridge: boolean;
  connected: boolean;
  bridgeId: string;
  bridgePort: string;
  onPick: (label: string, pronto: string) => void;
  onClose: () => void;
}) {
  const [allBrands, setAllBrands] = useState<string[] | null>(null);
  const [notice, setNotice] = useState("");
  const [homepage, setHomepage] = useState("https://github.com/probonopd/irdb");
  const [loadErr, setLoadErr] = useState<string | null>(null);

  const [query, setQuery] = useState("");
  const [brand, setBrand] = useState<string | null>(null);
  const [devices, setDevices] = useState<IrDbDevice[] | null>(null);
  const [device, setDevice] = useState<IrDbDevice | null>(null);
  const [functions, setFunctions] = useState<IrDbFunction[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [added, setAdded] = useState<Record<string, boolean>>({});
  const [testStatus, setTestStatus] = useState<Record<string, string>>({});

  // Load the brand list once when the panel opens.
  useEffect(() => {
    let alive = true;
    api
      .irDbBrands()
      .then((res) => {
        if (!alive) return;
        setAllBrands(res.brands);
        setNotice(res.notice);
        setHomepage(res.homepage);
      })
      .catch((e) => alive && setLoadErr(e instanceof Error ? e.message : "Failed to load"));
    return () => {
      alive = false;
    };
  }, []);

  const filteredBrands = useMemo(() => {
    if (!allBrands) return [];
    const q = query.trim().toLowerCase();
    const matches = q
      ? allBrands.filter((b) => b.toLowerCase().includes(q))
      : allBrands;
    return matches.slice(0, 200);
  }, [allBrands, query]);

  const pickBrand = async (b: string) => {
    setBrand(b);
    setDevices(null);
    setDevice(null);
    setFunctions(null);
    setBusy(true);
    setLoadErr(null);
    try {
      const res = await api.irDbDevices(b);
      setDevices(res.devices);
    } catch (e) {
      setLoadErr(e instanceof Error ? e.message : "Failed to load device list");
    } finally {
      setBusy(false);
    }
  };

  const pickDevice = async (d: IrDbDevice) => {
    setDevice(d);
    setFunctions(null);
    setBusy(true);
    setLoadErr(null);
    try {
      const res = await api.irDbFunctions(d.path);
      setFunctions(res.functions);
      if (res.notice) setNotice(res.notice);
    } catch (e) {
      setLoadErr(e instanceof Error ? e.message : "Failed to load codes");
    } finally {
      setBusy(false);
    }
  };

  const addFn = (fn: IrDbFunction) => {
    if (!fn.pronto) return;
    onPick(titleCase(fn.name), fn.pronto);
    setAdded((a) => ({ ...a, [fnKey(fn)]: true }));
  };

  const testFn = async (fn: IrDbFunction) => {
    if (!fn.pronto || !canBridge || !connected) return;
    const key = fnKey(fn);
    setTestStatus((s) => ({ ...s, [key]: "sending" }));
    try {
      await api.irEmit(bridgeId, { port: bridgePort, pronto: fn.pronto, repeat: 1 });
      setTestStatus((s) => ({ ...s, [key]: "sent" }));
      setTimeout(() => setTestStatus((s) => ({ ...s, [key]: "" })), 1500);
    } catch (e) {
      setTestStatus((s) => ({ ...s, [key]: e instanceof Error ? e.message : "failed" }));
    }
  };

  const reset = () => {
    if (device) {
      setDevice(null);
      setFunctions(null);
    } else if (brand) {
      setBrand(null);
      setDevices(null);
    }
  };

  return (
    <div
      style={{
        background: "var(--bg-surface)",
        borderRadius: "var(--border-radius)",
        border: "1px solid var(--color-accent, #3182ce)",
        padding: "var(--space-md)",
        marginBottom: "var(--space-md)",
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
        <strong style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
          <Search size={15} /> Search IR code database
        </strong>
        <button style={iconBtn} onClick={onClose}>
          <X size={14} /> Close
        </button>
      </div>

      {/* Breadcrumb */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
          flexWrap: "wrap",
          fontSize: "var(--font-size-sm)",
          color: "var(--text-secondary)",
          marginBottom: "var(--space-sm)",
        }}
      >
        {(brand || device) && (
          <button style={iconBtn} onClick={reset} title="Back">
            <ArrowLeft size={13} /> Back
          </button>
        )}
        <span>{brand || "Choose a brand"}</span>
        {device && (
          <>
            <ChevronRight size={13} />
            <span>
              {device.type} · code {device.device}
              {device.subdevice >= 0 ? `/${device.subdevice}` : ""}
            </span>
          </>
        )}
      </div>

      {loadErr && (
        <div
          style={{
            color: "var(--color-danger)",
            fontSize: "var(--font-size-sm)",
            display: "flex",
            alignItems: "center",
            gap: 4,
            marginBottom: "var(--space-sm)",
          }}
        >
          <AlertCircle size={14} /> {loadErr}
        </div>
      )}

      {/* Phase 1: brand */}
      {!brand && (
        <>
          <input
            autoFocus
            placeholder="Type a brand (e.g. Sony, Samsung, Denon)…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            style={{
              padding: "var(--space-xs) var(--space-sm)",
              fontSize: "var(--font-size-sm)",
              width: "100%",
              boxSizing: "border-box",
              marginBottom: "var(--space-sm)",
            }}
          />
          {allBrands === null && !loadErr ? (
            <div style={{ color: "var(--text-muted)", fontSize: "var(--font-size-sm)" }}>
              Loading the code database…
            </div>
          ) : (
            <div style={listBox}>
              {filteredBrands.map((b) => (
                <button key={b} style={rowBtn} onClick={() => pickBrand(b)}>
                  <span>{b}</span>
                  <ChevronRight size={14} />
                </button>
              ))}
              {filteredBrands.length === 0 && (
                <div style={{ padding: "var(--space-sm)", color: "var(--text-muted)", fontSize: "var(--font-size-sm)" }}>
                  No matching brands.
                </div>
              )}
            </div>
          )}
        </>
      )}

      {/* Phase 2: code set */}
      {brand && !device && (
        <div style={listBox}>
          {busy && <div style={{ padding: "var(--space-sm)", color: "var(--text-muted)", fontSize: "var(--font-size-sm)" }}>Loading…</div>}
          {devices?.map((d) => (
            <button key={d.path} style={rowBtn} onClick={() => pickDevice(d)}>
              <span>
                {d.type}{" "}
                <span style={{ color: "var(--text-muted)" }}>
                  · code {d.device}
                  {d.subdevice >= 0 ? `/${d.subdevice}` : ""}
                </span>
              </span>
              <ChevronRight size={14} />
            </button>
          ))}
          {devices && devices.length === 0 && (
            <div style={{ padding: "var(--space-sm)", color: "var(--text-muted)", fontSize: "var(--font-size-sm)" }}>
              No code sets for this brand.
            </div>
          )}
        </div>
      )}

      {/* Phase 3: functions */}
      {device && (
        <>
          <div style={{ color: "var(--text-muted)", fontSize: 11, marginBottom: 4 }}>
            Add a code, then test it against the device. If a code set doesn't
            work, go back and try another for this brand.
          </div>
          <div style={listBox}>
            {busy && <div style={{ padding: "var(--space-sm)", color: "var(--text-muted)", fontSize: "var(--font-size-sm)" }}>Loading codes…</div>}
            {functions?.map((fn) => {
              const key = fnKey(fn);
              return (
                <div
                  key={key}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: "var(--space-sm)",
                    padding: "var(--space-xs) var(--space-sm)",
                    borderBottom: "1px solid var(--border-color)",
                    opacity: fn.supported ? 1 : 0.55,
                  }}
                >
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontSize: "var(--font-size-sm)" }}>{titleCase(fn.name)}</div>
                    <div style={{ fontSize: 11, color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
                      {fn.supported && fn.pronto
                        ? prontoPreview(fn.pronto)
                        : fn.error || `${fn.protocol} not supported`}
                    </div>
                  </div>
                  {fn.supported && fn.pronto ? (
                    <>
                      <button
                        style={{ ...iconBtn, opacity: canBridge && connected ? 1 : 0.5 }}
                        onClick={() => testFn(fn)}
                        disabled={!canBridge || !connected}
                        title={canBridge && connected ? "Test this code through the bridge" : "Connect the bridge to test"}
                      >
                        <Zap size={13} />
                      </button>
                      <button
                        style={iconBtn}
                        onClick={() => addFn(fn)}
                        title="Add this code to the device"
                      >
                        {added[key] ? <Check size={13} /> : <Plus size={13} />}{" "}
                        {added[key] ? "Added" : "Add"}
                      </button>
                      {testStatus[key] && (
                        <span style={{ fontSize: 11, color: "var(--text-muted)", minWidth: 30 }}>
                          {testStatus[key] === "sending" ? "…" : testStatus[key] === "sent" ? "sent" : testStatus[key]}
                        </span>
                      )}
                    </>
                  ) : (
                    <span
                      style={{ fontSize: 11, color: "var(--text-muted)" }}
                      title="This protocol can't be rendered yet — learn the code from the physical remote instead."
                    >
                      unsupported
                    </span>
                  )}
                </div>
              );
            })}
            {functions && functions.length === 0 && (
              <div style={{ padding: "var(--space-sm)", color: "var(--text-muted)", fontSize: "var(--font-size-sm)" }}>
                This code set has no codes.
              </div>
            )}
          </div>
        </>
      )}

      {/* Attribution (required by the database license) */}
      {notice && (
        <div style={{ marginTop: "var(--space-sm)", fontSize: 11, color: "var(--text-muted)" }}>
          {notice.replace(/https:\/\/\S+$/, "")}
          <a href={homepage} target="_blank" rel="noreferrer" style={{ color: "var(--text-secondary)" }}>
            {homepage}
          </a>
        </div>
      )}
    </div>
  );
}

function fnKey(fn: IrDbFunction): string {
  return `${fn.protocol}:${fn.device}:${fn.subdevice}:${fn.function}:${fn.name}`;
}

// Database function names are shouty ("VOLUME UP"); present them nicely.
function titleCase(s: string): string {
  return s
    .toLowerCase()
    .split(/\s+/)
    .map((w) => (w ? w[0].toUpperCase() + w.slice(1) : w))
    .join(" ");
}
