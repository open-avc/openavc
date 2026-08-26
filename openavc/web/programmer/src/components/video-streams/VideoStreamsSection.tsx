import { useState, useEffect, useCallback, useMemo } from "react";
import type { CSSProperties, ReactNode } from "react";
import { Plus, Trash2, Pencil, RefreshCw, Image as ImageIcon } from "lucide-react";
import { Dialog } from "../shared/Dialog";
import { ConfirmDialog } from "../shared/ConfirmDialog";
import { showError, showSuccess } from "../../store/toastStore";
import { useProjectStore } from "../../store/projectStore";
import { useConnectionStore } from "../../store/connectionStore";
import { parseStateOptionRows } from "../shared/paramOptions";
import { byGroup, chipStyle, groupHeadingStyle, OptionRowCard } from "../shared/optionRowUi";
import * as streamsApi from "../../api/streamsClient";
import type { Stream, ProbeResult } from "../../api/streamsClient";

// Stream add/edit/delete persists into the project's plugin config server-side,
// outside the project store's save path. Re-sync the project store afterward so
// its in-memory project + ETag track the server and a later UI Builder save
// can't overwrite the stream list. No-op when there are unsaved edits; the
// server-side revision bump + 409 guard covers that case.
async function syncProjectStore() {
  await useProjectStore.getState().load();
}

const TRANSCODE_OPTS: [string, string][] = [
  ["auto", "Auto (only when needed)"],
  ["always", "Always transcode to H.264"],
  ["never", "Never transcode"],
];

const HWACCEL_OPTS: [string, string][] = [
  ["auto", "Auto-detect"],
  ["none", "Off (software)"],
  ["qsv", "Intel QuickSync"],
  ["nvenc", "NVIDIA NVENC"],
  ["vaapi", "VA-API"],
  ["v4l2m2m", "V4L2 M2M (Raspberry Pi)"],
];

const labelStyle: CSSProperties = {
  display: "block",
  fontSize: 11,
  color: "var(--text-muted)",
  textTransform: "uppercase",
  letterSpacing: "0.5px",
  marginBottom: 4,
};

const hintStyle: CSSProperties = {
  fontSize: 11,
  color: "var(--text-muted)",
  marginTop: 4,
  lineHeight: 1.4,
};

const inputStyle: CSSProperties = {
  width: "100%",
  padding: "5px 8px",
  fontSize: "var(--font-size-sm)",
  borderRadius: "var(--border-radius)",
  border: "1px solid var(--border-color)",
  background: "var(--bg-primary)",
  color: "var(--text-primary)",
};

const primaryBtn: CSSProperties = {
  padding: "6px 16px",
  borderRadius: "var(--border-radius)",
  background: "var(--accent-bg)",
  color: "#fff",
  border: "none",
  cursor: "pointer",
  fontSize: "var(--font-size-sm)",
};

const secondaryBtn: CSSProperties = {
  padding: "6px 16px",
  borderRadius: "var(--border-radius)",
  background: "var(--bg-hover)",
  color: "var(--text-secondary)",
  border: "none",
  cursor: "pointer",
  fontSize: "var(--font-size-sm)",
};

const iconBtnStyle: CSSProperties = {
  display: "flex",
  padding: 6,
  borderRadius: "var(--border-radius)",
  background: "transparent",
  border: "none",
  color: "var(--text-muted)",
  cursor: "pointer",
};

function slugify(name: string): string {
  const s = name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
  return s || "stream";
}

// Map a probe's detected codec to a stored codec_hint. The server uses this to
// decide whether "auto" transcoding kicks in (HEVC -> transcode). An unknown or
// failed probe stays "auto" (treated as passthrough).
function codecHintFromProbe(result: ProbeResult | null): string {
  if (!result || !result.success || !result.codec) return "auto";
  const c = result.codec.toLowerCase();
  if (c === "hevc" || c === "h265") return "h265";
  if (c === "h264") return "h264";
  return "auto";
}

function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <div style={{ flex: 1, minWidth: 0 }}>
      <label style={labelStyle}>{label}</label>
      {children}
      {hint ? <div style={hintStyle}>{hint}</div> : null}
    </div>
  );
}

function ProbeReadout({ result }: { result: ProbeResult }) {
  let color = "var(--color-success, #2e7d32)";
  if (!result.success) color = "var(--color-error, #c0392b)";
  else if (result.transcode_recommended) color = "var(--color-warning, #b26a00)";

  const dims = result.width && result.height ? `${result.width}x${result.height}` : null;
  const summary = result.success
    ? [result.codec?.toUpperCase(), result.profile, dims, result.fps ? `${result.fps} fps` : null]
        .filter(Boolean)
        .join("  -  ")
    : null;

  return (
    <div
      style={{
        marginTop: "var(--space-sm)",
        padding: "var(--space-sm) var(--space-md)",
        borderRadius: "var(--border-radius)",
        background: "var(--bg-surface)",
        border: `1px solid ${color}`,
        fontSize: "var(--font-size-sm)",
      }}
    >
      {summary && <div style={{ color, fontWeight: 600, marginBottom: 2 }}>{summary}</div>}
      <div style={{ color: result.success ? "var(--text-secondary)" : color }}>{result.advice || result.message}</div>
    </div>
  );
}

function StreamForm({
  stream,
  onClose,
  onSaved,
}: {
  stream: Stream | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [name, setName] = useState(stream?.name ?? "");
  const [streamId, setStreamId] = useState(stream?.stream_id ?? "");
  const [streamIdTouched, setStreamIdTouched] = useState(!!stream);
  const [rtspUrl, setRtspUrl] = useState(stream?.rtsp_url ?? "");
  const [username, setUsername] = useState(stream?.username ?? "");
  const [password, setPassword] = useState(stream?.password ?? "");
  const [transcode, setTranscode] = useState(stream?.transcode ?? "auto");
  const [hwAccel, setHwAccel] = useState(stream?.hardware_accel ?? "auto");
  const [probing, setProbing] = useState(false);
  const [probeResult, setProbeResult] = useState<ProbeResult | null>(null);
  const [saving, setSaving] = useState(false);

  const effectiveId = (streamIdTouched ? streamId : slugify(name)).trim();
  const valid = name.trim() && rtspUrl.includes("://") && effectiveId;

  async function probe(): Promise<ProbeResult> {
    setProbing(true);
    setProbeResult(null);
    let result: ProbeResult;
    try {
      result = await streamsApi.probeStream({ rtsp_url: rtspUrl, username, password });
    } catch (err) {
      result = { success: false, message: streamsApi.errorMessage(err) };
    }
    setProbeResult(result);
    setProbing(false);
    return result;
  }

  // The server needs a codec_hint so "auto" transcoding can tell HEVC apart. An
  // explicit Test this session wins; otherwise keep a previously detected codec;
  // otherwise, for auto-transcode, probe now so HEVC sources just work without
  // the user clicking Test. A best-effort probe of an unreachable source stays
  // "auto" (passthrough), which it would be anyway.
  async function resolveCodecHint(): Promise<string> {
    const tested = codecHintFromProbe(probeResult);
    if (tested !== "auto") return tested;
    if (stream?.codec_hint && stream.codec_hint !== "auto") return stream.codec_hint;
    if (transcode === "auto") return codecHintFromProbe(await probe());
    return "auto";
  }

  async function save() {
    if (!valid) return;
    setSaving(true);
    try {
      const payload = {
        name: name.trim(),
        stream_id: effectiveId,
        rtsp_url: rtspUrl.trim(),
        username,
        password,
        codec_hint: await resolveCodecHint(),
        transcode,
        hardware_accel: hwAccel,
      };
      if (stream) await streamsApi.updateStream(stream.stream_id, payload);
      else await streamsApi.createStream(payload);
      showSuccess(stream ? "Stream updated." : "Stream added.");
      onSaved();
    } catch (err) {
      showError(streamsApi.errorMessage(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog title={stream ? "Edit Stream" : "Add Stream"} onClose={onClose}>
      <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-md)", maxHeight: "68vh", overflowY: "auto" }}>
        <Field label="Name">
          <input style={inputStyle} value={name} placeholder="Front Door" onChange={(e) => setName(e.target.value)} autoFocus />
        </Field>
        <Field label="Stream ID">
          <input
            style={inputStyle}
            value={effectiveId}
            onChange={(e) => {
              setStreamIdTouched(true);
              setStreamId(e.target.value);
            }}
          />
        </Field>
        <Field
          label="Source URL"
          hint="RTSP, SRT, RTMP or an HLS playlist. The OpenAVC server connects to this address, so it has to be reachable from the server rather than from the panel."
        >
          <input
            style={inputStyle}
            value={rtspUrl}
            placeholder="rtsp://192.168.1.50:554/stream1  or  srt://192.168.1.60:10000"
            onChange={(e) => setRtspUrl(e.target.value)}
          />
        </Field>
        <div style={{ display: "flex", gap: "var(--space-md)" }}>
          <Field label="Username">
            <input style={inputStyle} value={username} onChange={(e) => setUsername(e.target.value)} />
          </Field>
          <Field label="Password">
            <input style={inputStyle} type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} />
          </Field>
        </div>

        <div>
          <button style={secondaryBtn} onClick={() => probe()} disabled={probing || !rtspUrl.includes("://")}>
            {probing ? "Testing..." : "Test"}
          </button>
          {probeResult && <ProbeReadout result={probeResult} />}
        </div>

        <div style={{ display: "flex", gap: "var(--space-md)" }}>
          <Field label="Transcode">
            <select style={inputStyle} value={transcode} onChange={(e) => setTranscode(e.target.value)}>
              {TRANSCODE_OPTS.map(([v, l]) => (
                <option key={v} value={v}>
                  {l}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Hardware acceleration">
            <select style={inputStyle} value={hwAccel} onChange={(e) => setHwAccel(e.target.value)}>
              {HWACCEL_OPTS.map(([v, l]) => (
                <option key={v} value={v}>
                  {l}
                </option>
              ))}
            </select>
          </Field>
        </div>
      </div>

      <div style={{ display: "flex", justifyContent: "flex-end", gap: "var(--space-sm)", marginTop: "var(--space-lg)" }}>
        <button style={secondaryBtn} onClick={onClose}>
          Cancel
        </button>
        <button style={{ ...primaryBtn, opacity: valid && !saving ? 1 : 0.5 }} onClick={save} disabled={!valid || saving}>
          {saving ? "Saving..." : stream ? "Save" : "Add Stream"}
        </button>
      </div>
    </Dialog>
  );
}

function PreviewDialog({ stream, onClose }: { stream: Stream; onClose: () => void }) {
  const [state, setState] = useState<"loading" | "ok" | "error">("loading");
  const [src, setSrc] = useState<string | null>(null);

  // The snapshot endpoint requires auth on a claimed instance, and only
  // fetch() carries the Programmer's credential — a native <img src> load
  // doesn't. Fetch into an object URL, revoke it when the dialog closes.
  useEffect(() => {
    let objectUrl: string | null = null;
    let cancelled = false;
    streamsApi
      .fetchSnapshot(stream.stream_id)
      .then((url) => {
        if (cancelled) {
          URL.revokeObjectURL(url);
          return;
        }
        objectUrl = url;
        setSrc(url);
      })
      .catch(() => {
        if (!cancelled) setState("error");
      });
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [stream.stream_id]);

  return (
    <Dialog title={`Preview: ${stream.name}`} onClose={onClose}>
      <div
        style={{
          minHeight: 180,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          background: "var(--bg-surface)",
          borderRadius: "var(--border-radius)",
          overflow: "hidden",
        }}
      >
        {state === "loading" && <span style={{ color: "var(--text-muted)" }}>Capturing a frame...</span>}
        {state === "error" && (
          <span style={{ color: "var(--text-muted)", padding: "var(--space-lg)", textAlign: "center" }}>
            Could not capture a frame. The source may be offline, or the URL or credentials may be wrong.
          </span>
        )}
        {src && (
          <img
            src={src}
            alt={stream.name}
            onLoad={() => setState("ok")}
            onError={() => setState("error")}
            style={{ maxWidth: "100%", display: state === "ok" ? "block" : "none" }}
          />
        )}
      </div>
    </Dialog>
  );
}

/**
 * Sources the devices in this project offered on their own.
 *
 * Nothing is typed to get one of these: a driver publishes a preview and the
 * plugin lists it. They are read-only here and that is the point -- what this
 * block is for is the ones that are NOT ready, because a source one setting
 * away from working is otherwise invisible everywhere.
 */
function DeviceSources() {
  const raw = useConnectionStore((s) => s.liveState["plugin.video_panel.stream_ids"]);
  const rows = useMemo(
    () => parseStateOptionRows(raw).filter((r) => r.group || r.status || r.setup),
    [raw],
  );
  if (rows.length === 0) return null;

  return (
    <div style={{ marginTop: "var(--space-xl)" }}>
      <h3 style={{ fontSize: "var(--font-size-base)", color: "var(--text-secondary)", margin: 0, marginBottom: "var(--space-sm)" }}>
        Found on your devices
      </h3>
      <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-sm)" }}>
        {byGroup(rows).map(([group, groupRows]) => (
          <div key={group || "_"} style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {group && <div style={groupHeadingStyle}>{group}</div>}
            {groupRows.map((row) => (
              <OptionRowCard
                key={row.id ?? row.value ?? row.label}
                row={row}
                trailing={
                  row.value && !row.status ? (
                    <span style={{ ...chipStyle, color: "var(--text-secondary)" }}>Ready</span>
                  ) : null
                }
              />
            ))}
          </div>
        ))}
      </div>
      <p style={{ marginTop: "var(--space-sm)", fontSize: "var(--font-size-sm)", color: "var(--text-muted)", lineHeight: 1.5 }}>
        These appear in the UI Builder's Video Stream picker on their own. Nothing to add here.
      </p>
    </div>
  );
}

export function VideoStreamsSection({ embedded = false }: { embedded?: boolean } = {}) {
  const [streams, setStreams] = useState<Stream[]>([]);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState<Stream | "new" | null>(null);
  const [deleting, setDeleting] = useState<Stream | null>(null);
  const [preview, setPreview] = useState<Stream | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      setStreams(await streamsApi.listStreams());
    } catch (err) {
      showError(streamsApi.errorMessage(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  async function confirmDelete() {
    if (!deleting) return;
    const s = deleting;
    setDeleting(null);
    try {
      await streamsApi.deleteStream(s.stream_id);
      showSuccess(`Removed "${s.name}".`);
      refresh();
      syncProjectStore();
    } catch (err) {
      showError(streamsApi.errorMessage(err));
    }
  }

  return (
    <div style={{ marginTop: embedded ? 0 : "var(--space-2xl)", maxWidth: 600 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "var(--space-md)" }}>
        {/* The page already carries this name in its own heading; a second one
            directly under it just reads as a mistake. */}
        <h3 style={{ fontSize: "var(--font-size-base)", color: "var(--text-secondary)", margin: 0 }}>
          {embedded ? "Added by hand" : "Video Streams"}
        </h3>
        <div style={{ display: "flex", gap: "var(--space-sm)" }}>
          <button
            onClick={refresh}
            title="Refresh"
            style={{ display: "flex", padding: 6, borderRadius: "var(--border-radius)", background: "var(--bg-hover)", border: "none", color: "var(--text-secondary)", cursor: "pointer" }}
          >
            <RefreshCw size={15} />
          </button>
          <button
            onClick={() => setEditing("new")}
            style={{ display: "flex", alignItems: "center", gap: "var(--space-xs)", ...primaryBtn, padding: "var(--space-xs) var(--space-md)" }}
          >
            <Plus size={15} /> Add Stream
          </button>
        </div>
      </div>

      <div style={{ background: "var(--bg-surface)", borderRadius: "var(--border-radius)", border: "1px solid var(--border-color)", overflow: "hidden" }}>
        {loading ? (
          <div style={{ padding: "var(--space-lg)", color: "var(--text-muted)", fontSize: "var(--font-size-sm)" }}>Loading streams...</div>
        ) : streams.length === 0 ? (
          <div style={{ padding: "var(--space-lg)", color: "var(--text-muted)", fontSize: "var(--font-size-sm)" }}>
            No video streams yet. Add an RTSP/RTMP/SRT source (such as an IP camera) to show it on a panel with the Video Stream element.
          </div>
        ) : (
          streams.map((s, i) => (
            <div
              key={s.stream_id}
              style={{
                display: "flex",
                alignItems: "center",
                gap: "var(--space-md)",
                padding: "var(--space-sm) var(--space-md)",
                borderTop: i === 0 ? "none" : "1px solid var(--border-color)",
              }}
            >
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: "var(--font-size-sm)", color: "var(--text-primary)" }}>{s.name}</div>
                <div style={{ fontSize: 11, color: "var(--text-muted)", fontFamily: "var(--font-mono)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {s.stream_id}
                </div>
              </div>
              <span
                style={{
                  fontSize: 10,
                  fontWeight: 600,
                  textTransform: "uppercase",
                  letterSpacing: "0.5px",
                  color: s.status === "streaming" ? "var(--color-success, #2e7d32)" : "var(--text-muted)",
                }}
              >
                {s.status === "streaming" ? "Live" : "Idle"}
              </span>
              <button onClick={() => setPreview(s)} title="Preview" style={iconBtnStyle}>
                <ImageIcon size={15} />
              </button>
              <button onClick={() => setEditing(s)} title="Edit" style={iconBtnStyle}>
                <Pencil size={15} />
              </button>
              <button onClick={() => setDeleting(s)} title="Remove" style={iconBtnStyle}>
                <Trash2 size={15} />
              </button>
            </div>
          ))
        )}
      </div>

      <p style={{ marginTop: "var(--space-md)", fontSize: "var(--font-size-sm)", color: "var(--text-muted)", lineHeight: 1.5 }}>
        Video streams play on panels through the Video Stream element in the UI Builder. Use Test to check that a source
        is reachable and whether it needs transcoding before you save it.
      </p>

      <DeviceSources />

      {editing && (
        <StreamForm
          stream={editing === "new" ? null : editing}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            refresh();
            syncProjectStore();
          }}
        />
      )}
      {deleting && (
        <ConfirmDialog
          title="Remove stream"
          message={`Remove "${deleting.name}"? Panels bound to this stream will stop showing it.`}
          confirmLabel="Remove"
          destructive
          onConfirm={confirmDelete}
          onCancel={() => setDeleting(null)}
        />
      )}
      {preview && <PreviewDialog stream={preview} onClose={() => setPreview(null)} />}
    </div>
  );
}
