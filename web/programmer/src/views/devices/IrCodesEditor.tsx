import { useState, useEffect, useMemo, useRef, useCallback } from "react";
import {
  Plus,
  Trash2,
  Save,
  Check,
  AlertCircle,
  Radio,
  Zap,
  ArrowUp,
  ArrowDown,
  Pencil,
  X,
  Search,
} from "lucide-react";
import { useProjectStore } from "../../store/projectStore";
import * as api from "../../api/restClient";
import { IrLearnSession, type IrLearnMode } from "../../api/irLearn";
import { IrDbSearch } from "./IrDbSearch";

// An IR device's code-set is a map name -> {label, pronto, repeat} stored in
// device.config.ir_codes. Each code becomes a device command that emits through
// the bound bridge's IR port. This editor is the parallel of the inline-protocol
// Commands & Responses editor: it reads/writes device.config, gated on
// driver_info.ir_codes === true.

type IrRow = {
  key: string;
  name: string;
  label: string;
  pronto: string;
  repeat: number;
};

type Capture = { key: string; pronto: string; name: string };

let _keySeq = 0;
const nextKey = () => `ir${++_keySeq}`;

function slugify(s: string): string {
  return (
    s
      .toLowerCase()
      .trim()
      .replace(/[^a-z0-9]+/g, "_")
      .replace(/^_+|_+$/g, "")
      .slice(0, 48) || "code"
  );
}

function parseIrCodes(raw: unknown): IrRow[] {
  if (!raw || typeof raw !== "object") return [];
  const out: IrRow[] = [];
  for (const [name, val] of Object.entries(raw as Record<string, unknown>)) {
    if (!val || typeof val !== "object") continue;
    const v = val as Record<string, unknown>;
    out.push({
      key: nextKey(),
      name,
      label: typeof v.label === "string" ? v.label : name,
      pronto: typeof v.pronto === "string" ? v.pronto : "",
      repeat: Number.isFinite(Number(v.repeat)) ? Math.max(1, Number(v.repeat)) : 1,
    });
  }
  return out;
}

function buildIrCodes(rows: IrRow[]): Record<string, unknown> {
  const map: Record<string, unknown> = {};
  const used = new Set<string>();
  for (const r of rows) {
    if (!r.pronto.trim()) continue;
    let name = slugify(r.name || r.label);
    if (used.has(name)) {
      let n = 2;
      while (used.has(`${name}_${n}`)) n++;
      name = `${name}_${n}`;
    }
    used.add(name);
    map[name] = {
      label: r.label || r.name,
      pronto: r.pronto.trim(),
      repeat: Math.max(1, r.repeat || 1),
    };
  }
  return map;
}

// ── styles (match InlineProtocolEditor conventions) ──────────────────────────
const inputStyle: React.CSSProperties = {
  padding: "var(--space-xs) var(--space-sm)",
  fontSize: "var(--font-size-sm)",
  width: "100%",
  boxSizing: "border-box",
};
const cellLabel: React.CSSProperties = {
  fontSize: 11,
  color: "var(--text-muted)",
  marginBottom: 2,
};
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
const card: React.CSSProperties = {
  background: "var(--bg-surface)",
  borderRadius: "var(--border-radius)",
  border: "1px solid var(--border-color)",
  padding: "var(--space-md)",
  marginBottom: "var(--space-md)",
};

function prontoPreview(pronto: string): string {
  const t = pronto.trim();
  if (!t) return "(no code yet)";
  return t.length > 40 ? t.slice(0, 40) + "…" : t;
}

export function IrCodesEditor({
  deviceId,
  connected,
  onSaved,
}: {
  deviceId: string;
  connected: boolean;
  onSaved: () => void;
}) {
  const project = useProjectStore((s) => s.project);
  const update = useProjectStore((s) => s.update);
  const deviceConfig = project?.devices.find((d) => d.id === deviceId);
  const savedConfig = useMemo(
    () => (deviceConfig?.config ?? {}) as Record<string, unknown>,
    [deviceConfig],
  );

  // The bound bridge (IR emit + learn go through it). From the connections table.
  const conn = project?.connections?.[deviceId] as
    | Record<string, unknown>
    | undefined;
  const bridgeId = (conn?.bridge as string) || "";
  const bridgePort = (conn?.bridge_port as string) || "";
  const bridgeName =
    project?.devices.find((d) => d.id === bridgeId)?.name || bridgeId;
  const canBridge = Boolean(bridgeId && bridgePort);

  const [rows, setRows] = useState<IrRow[]>([]);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  // Per-row inline code editor (paste Pronto / type sendir).
  const [editKey, setEditKey] = useState<string | null>(null);
  const [editText, setEditText] = useState("");
  const [editErr, setEditErr] = useState<string | null>(null);
  const [editBusy, setEditBusy] = useState(false);

  // Per-row test-emit feedback.
  const [testStatus, setTestStatus] = useState<Record<string, string>>({});

  // Database search UI.
  const [searchOpen, setSearchOpen] = useState(false);

  // Learn session UI.
  const [learnOpen, setLearnOpen] = useState(false);
  const [learnMode, setLearnMode] = useState<IrLearnMode>("auto");
  const [learnStatus, setLearnStatus] = useState("");
  const [learnErr, setLearnErr] = useState<string | null>(null);
  const [captures, setCaptures] = useState<Capture[]>([]);
  const sessionRef = useRef<IrLearnSession | null>(null);

  // (Re)load rows when the device changes (keyed on deviceId only, so live
  // updates elsewhere never clobber in-progress edits).
  useEffect(() => {
    setRows(parseIrCodes((deviceConfig?.config ?? {}).ir_codes));
    setDirty(false);
    setSaved(false);
    setSaveError(null);
    setEditKey(null);
    setSearchOpen(false);
    closeLearn();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [deviceId]);

  // Drop any live learn session on unmount.
  useEffect(() => {
    return () => {
      sessionRef.current?.close();
      sessionRef.current = null;
    };
  }, []);

  const markDirty = () => {
    setDirty(true);
    setSaved(false);
  };

  const setRow = (key: string, patch: Partial<IrRow>) => {
    setRows((rs) => rs.map((r) => (r.key === key ? { ...r, ...patch } : r)));
    markDirty();
  };

  const addRow = () => {
    setRows((rs) => [
      ...rs,
      { key: nextKey(), name: "", label: "", pronto: "", repeat: 1 },
    ]);
    markDirty();
  };

  const deleteRow = (key: string) => {
    setRows((rs) => rs.filter((r) => r.key !== key));
    markDirty();
  };

  const moveRow = (key: string, dir: -1 | 1) => {
    setRows((rs) => {
      const i = rs.findIndex((r) => r.key === key);
      const j = i + dir;
      if (i < 0 || j < 0 || j >= rs.length) return rs;
      const copy = [...rs];
      [copy[i], copy[j]] = [copy[j], copy[i]];
      return copy;
    });
    markDirty();
  };

  const handleSave = async () => {
    setSaving(true);
    setSaveError(null);
    try {
      const map = buildIrCodes(rows);
      const newConfig = { ...savedConfig, ir_codes: map };
      await api.updateDevice(deviceId, { config: newConfig });
      const cur = useProjectStore.getState().project;
      if (cur) {
        update({
          devices: cur.devices.map((d) =>
            d.id === deviceId ? { ...d, config: newConfig } : d,
          ),
        });
      }
      setDirty(false);
      setSaved(true);
      onSaved();
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : "Save failed");
    } finally {
      setSaving(false);
    }
  };

  // ── code editing (paste Pronto / type sendir) ──────────────────────────────
  const openEdit = (row: IrRow) => {
    setEditKey(row.key);
    setEditText(row.pronto);
    setEditErr(null);
  };

  const applyEdit = async () => {
    if (editKey === null) return;
    const text = editText.trim();
    if (!text) {
      setEditErr("Enter a Pronto code or a sendir string.");
      return;
    }
    // A sendir string (has commas) is bridge-specific — convert it to Pronto via
    // the bound bridge. A Pronto code (space-separated hex words) is stored as-is.
    if (text.includes(",")) {
      if (!canBridge) {
        setEditErr("Bind this device to an IR bridge first to import a sendir code.");
        return;
      }
      setEditBusy(true);
      setEditErr(null);
      try {
        const res = await api.irImport(bridgeId, text);
        setRow(editKey, { pronto: res.pronto });
        setEditKey(null);
      } catch (e) {
        setEditErr(e instanceof Error ? e.message : "Could not import that code.");
      } finally {
        setEditBusy(false);
      }
      return;
    }
    // Loose Pronto sanity check: learned codes start with the 0000 word.
    if (!/^[0-9a-fA-F]{4}(\s+[0-9a-fA-F]{4})+$/.test(text)) {
      setEditErr("That doesn't look like Pronto hex (space-separated 4-digit hex words).");
      return;
    }
    setRow(editKey, { pronto: text });
    setEditKey(null);
  };

  // ── test emit (fires through the bound bridge, saved or not) ────────────────
  const testCode = async (rowKey: string, pronto: string, repeat: number) => {
    if (!canBridge || !pronto.trim()) return;
    setTestStatus((s) => ({ ...s, [rowKey]: "sending" }));
    try {
      await api.irEmit(bridgeId, { port: bridgePort, pronto: pronto.trim(), repeat });
      setTestStatus((s) => ({ ...s, [rowKey]: "sent" }));
      setTimeout(
        () => setTestStatus((s) => ({ ...s, [rowKey]: "" })),
        1500,
      );
    } catch (e) {
      setTestStatus((s) => ({
        ...s,
        [rowKey]: e instanceof Error ? e.message : "failed",
      }));
    }
  };

  // ── learn ──────────────────────────────────────────────────────────────────
  const startLearn = useCallback(
    (mode: IrLearnMode, targetKey: string | null) => {
      if (!canBridge) return;
      sessionRef.current?.close();
      setSearchOpen(false);
      setLearnOpen(true);
      setLearnMode(mode);
      setLearnErr(null);
      setCaptures([]);
      setLearnStatus("Connecting to the bridge…");
      const session = new IrLearnSession(bridgeId, mode, {
        onStarted: () =>
          setLearnStatus("Point the remote at the bridge and press a button."),
        onCaptured: (pronto) => {
          if (mode === "one_off" && targetKey) {
            setRow(targetKey, { pronto });
            setLearnStatus("Captured.");
            session.close();
            setLearnOpen(false);
          } else {
            setCaptures((c) => [
              ...c,
              { key: nextKey(), pronto, name: "" },
            ]);
          }
        },
        onError: (_code, message) => setLearnErr(message),
        onStopped: () => setLearnStatus("Learning stopped."),
      });
      sessionRef.current = session;
      session.start();
    },
    // setRow is stable enough for our purposes (rows updates don't affect it)
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [bridgeId, canBridge],
  );

  const stopLearn = () => {
    sessionRef.current?.stop();
  };

  const closeLearn = () => {
    sessionRef.current?.close();
    sessionRef.current = null;
    setLearnOpen(false);
    setCaptures([]);
    setLearnErr(null);
    setLearnStatus("");
  };

  // ── database search ──────────────────────────────────────────────────────
  const addFromSearch = (label: string, pronto: string) => {
    setRows((rs) => [
      ...rs,
      { key: nextKey(), name: label, label, pronto, repeat: 1 },
    ]);
    markDirty();
  };

  const addCaptureAsRow = (cap: Capture) => {
    setRows((rs) => [
      ...rs,
      {
        key: nextKey(),
        name: cap.name || "",
        label: cap.name || "",
        pronto: cap.pronto,
        repeat: 1,
      },
    ]);
    setCaptures((c) => c.filter((x) => x.key !== cap.key));
    markDirty();
  };

  return (
    <div style={{ marginBottom: "var(--space-xl)" }}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          marginBottom: "var(--space-sm)",
        }}
      >
        <h3 style={{ margin: 0, fontSize: "var(--font-size-md)" }}>IR Codes</h3>
        <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)" }}>
          {saveError && (
            <span
              style={{
                color: "var(--color-danger)",
                fontSize: "var(--font-size-sm)",
                display: "inline-flex",
                alignItems: "center",
                gap: 4,
              }}
            >
              <AlertCircle size={14} /> {saveError}
            </span>
          )}
          {saved && !dirty && (
            <span
              style={{
                color: "var(--color-success, #38a169)",
                fontSize: "var(--font-size-sm)",
                display: "inline-flex",
                alignItems: "center",
                gap: 4,
              }}
            >
              <Check size={14} /> Saved
            </span>
          )}
          <button
            style={{ ...iconBtn, opacity: dirty ? 1 : 0.6 }}
            onClick={handleSave}
            disabled={!dirty || saving}
          >
            <Save size={14} /> {saving ? "Saving…" : "Save"}
          </button>
        </div>
      </div>

      <div style={{ color: "var(--text-muted)", fontSize: "var(--font-size-sm)", marginBottom: "var(--space-sm)" }}>
        Each code becomes a command you can put on a panel button or call from a
        macro. Learn a code from the remote, paste a Pronto code, or type a raw
        sendir string.
        {canBridge && (
          <> Emits through <strong>{bridgeName}</strong> · {bridgePort}.</>
        )}
      </div>

      {!canBridge && (
        <div
          style={{
            ...card,
            borderColor: "var(--color-warning, #d69e2e)",
            display: "flex",
            alignItems: "center",
            gap: "var(--space-sm)",
          }}
        >
          <AlertCircle size={16} />
          <span style={{ fontSize: "var(--font-size-sm)" }}>
            This IR device isn't bound to a bridge port yet. Set its connection to
            <strong> Through a bridge</strong> and pick an IR port to learn and
            test codes.
          </span>
        </div>
      )}

      {/* Toolbar */}
      <div style={{ display: "flex", gap: "var(--space-sm)", marginBottom: "var(--space-md)" }}>
        <button style={iconBtn} onClick={addRow}>
          <Plus size={14} /> Add code
        </button>
        <button
          style={{ ...iconBtn, opacity: canBridge && connected ? 1 : 0.5 }}
          onClick={() => startLearn("auto", null)}
          disabled={!canBridge || !connected}
          title={
            !canBridge
              ? "Bind this device to an IR bridge first"
              : !connected
                ? "The bridge is offline"
                : "Learn codes from the original remote"
          }
        >
          <Radio size={14} /> Learn from remote
        </button>
        <button
          style={iconBtn}
          onClick={() => {
            closeLearn();
            setSearchOpen((v) => !v);
          }}
          title="Search an online IR code database by brand and device"
        >
          <Search size={14} /> Search database
        </button>
      </div>

      {/* Database search panel */}
      {searchOpen && (
        <IrDbSearch
          canBridge={canBridge}
          connected={connected}
          bridgeId={bridgeId}
          bridgePort={bridgePort}
          onPick={addFromSearch}
          onClose={() => setSearchOpen(false)}
        />
      )}

      {/* Learn panel */}
      {learnOpen && (
        <div style={{ ...card, borderColor: "var(--color-accent, #3182ce)" }}>
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              marginBottom: "var(--space-sm)",
            }}
          >
            <strong style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
              <Radio size={15} />
              {learnMode === "one_off" ? "Learn one code" : "Learn from remote"}
            </strong>
            <div style={{ display: "flex", gap: "var(--space-sm)" }}>
              <button style={iconBtn} onClick={stopLearn}>
                Stop
              </button>
              <button style={iconBtn} onClick={closeLearn}>
                <X size={14} /> Close
              </button>
            </div>
          </div>
          <div style={{ fontSize: "var(--font-size-sm)", color: "var(--text-secondary)" }}>
            {learnErr ? (
              <span style={{ color: "var(--color-danger)", display: "inline-flex", alignItems: "center", gap: 4 }}>
                <AlertCircle size={14} /> {learnErr}
              </span>
            ) : (
              learnStatus || "Starting…"
            )}
          </div>

          {learnMode === "auto" && captures.length > 0 && (
            <div style={{ marginTop: "var(--space-md)", display: "flex", flexDirection: "column", gap: "var(--space-sm)" }}>
              {captures.map((cap) => (
                <div
                  key={cap.key}
                  style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)" }}
                >
                  <span style={{ fontSize: 11, color: "var(--text-muted)", fontFamily: "var(--font-mono)", flex: "0 0 auto" }}>
                    {prontoPreview(cap.pronto)}
                  </span>
                  <input
                    style={{ ...inputStyle, flex: 1 }}
                    placeholder="Name this code (e.g. Power On)"
                    value={cap.name}
                    onChange={(e) =>
                      setCaptures((c) =>
                        c.map((x) => (x.key === cap.key ? { ...x, name: e.target.value } : x)),
                      )
                    }
                  />
                  <button
                    style={iconBtn}
                    onClick={() => testCode(cap.key, cap.pronto, 1)}
                    title="Test this captured code"
                  >
                    <Zap size={14} />
                  </button>
                  <button style={iconBtn} onClick={() => addCaptureAsRow(cap)}>
                    <Plus size={14} /> Add
                  </button>
                  {testStatus[cap.key] && (
                    <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
                      {testStatus[cap.key] === "sending"
                        ? "…"
                        : testStatus[cap.key] === "sent"
                          ? "sent"
                          : testStatus[cap.key]}
                    </span>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Code rows */}
      {rows.length === 0 ? (
        <div style={{ color: "var(--text-muted)", fontSize: "var(--font-size-sm)", padding: "var(--space-sm) 0" }}>
          No codes yet. Add one, or learn from the remote.
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-sm)" }}>
          {rows.map((r, idx) => (
            <div key={r.key} style={card}>
              <div style={{ display: "flex", gap: "var(--space-sm)", alignItems: "flex-start" }}>
                <div style={{ flex: "1 1 160px" }}>
                  <div style={cellLabel}>Name</div>
                  <input
                    style={inputStyle}
                    value={r.label || r.name}
                    placeholder="Power On"
                    onChange={(e) => setRow(r.key, { label: e.target.value, name: e.target.value })}
                  />
                </div>
                <div style={{ flex: "0 0 90px" }}>
                  <div style={cellLabel}>Repeat</div>
                  <input
                    type="number"
                    min={1}
                    max={50}
                    style={inputStyle}
                    value={r.repeat}
                    onChange={(e) => setRow(r.key, { repeat: Math.max(1, Number(e.target.value) || 1) })}
                  />
                </div>
                <div style={{ flex: "2 1 220px" }}>
                  <div style={cellLabel}>Code</div>
                  {editKey === r.key ? (
                    <div>
                      <textarea
                        rows={2}
                        style={{ ...inputStyle, fontFamily: "var(--font-mono)", resize: "vertical" }}
                        value={editText}
                        placeholder="Paste Pronto hex (0000 006D …) or type a sendir string"
                        onChange={(e) => setEditText(e.target.value)}
                      />
                      {editErr && (
                        <div style={{ color: "var(--color-danger)", fontSize: 11, marginTop: 2 }}>
                          {editErr}
                        </div>
                      )}
                      <div style={{ display: "flex", gap: "var(--space-sm)", marginTop: 4 }}>
                        <button style={iconBtn} onClick={applyEdit} disabled={editBusy}>
                          <Check size={14} /> {editBusy ? "Importing…" : "Apply"}
                        </button>
                        <button style={iconBtn} onClick={() => setEditKey(null)}>
                          Cancel
                        </button>
                      </div>
                    </div>
                  ) : (
                    <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)" }}>
                      <code style={{ fontSize: 11, color: "var(--text-muted)", flex: 1, wordBreak: "break-all" }}>
                        {prontoPreview(r.pronto)}
                      </code>
                      <button style={iconBtn} onClick={() => openEdit(r)} title="Paste Pronto / type sendir">
                        <Pencil size={14} />
                      </button>
                      <button
                        style={{ ...iconBtn, opacity: canBridge && connected ? 1 : 0.5 }}
                        onClick={() => startLearn("one_off", r.key)}
                        disabled={!canBridge || !connected}
                        title="Learn this code from the remote"
                      >
                        <Radio size={14} />
                      </button>
                    </div>
                  )}
                </div>
                <div style={{ flex: "0 0 auto", display: "flex", flexDirection: "column", gap: 4, paddingTop: 16 }}>
                  <div style={{ display: "flex", gap: 4 }}>
                    <button
                      style={{ ...iconBtn, opacity: canBridge && connected && r.pronto ? 1 : 0.5 }}
                      onClick={() => testCode(r.key, r.pronto, r.repeat)}
                      disabled={!canBridge || !connected || !r.pronto}
                      title="Fire this code through the bridge now"
                    >
                      <Zap size={14} /> Test
                    </button>
                    <button style={iconBtn} onClick={() => deleteRow(r.key)} title="Delete">
                      <Trash2 size={14} />
                    </button>
                  </div>
                  <div style={{ display: "flex", gap: 4 }}>
                    <button
                      style={{ ...iconBtn, opacity: idx === 0 ? 0.4 : 1 }}
                      onClick={() => moveRow(r.key, -1)}
                      disabled={idx === 0}
                      title="Move up"
                    >
                      <ArrowUp size={14} />
                    </button>
                    <button
                      style={{ ...iconBtn, opacity: idx === rows.length - 1 ? 0.4 : 1 }}
                      onClick={() => moveRow(r.key, 1)}
                      disabled={idx === rows.length - 1}
                      title="Move down"
                    >
                      <ArrowDown size={14} />
                    </button>
                    {testStatus[r.key] && (
                      <span style={{ fontSize: 11, color: "var(--text-muted)", alignSelf: "center" }}>
                        {testStatus[r.key] === "sending"
                          ? "…"
                          : testStatus[r.key] === "sent"
                            ? "sent"
                            : testStatus[r.key]}
                      </span>
                    )}
                  </div>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
