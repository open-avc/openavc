import { useState, useEffect, useCallback, useRef } from "react";
import type { CSSProperties, ReactNode } from "react";
import { Plus, Trash2, Pencil, RefreshCw, ExternalLink, KeyRound, ShieldAlert } from "lucide-react";
import { Dialog } from "../shared/Dialog";
import { ConfirmDialog } from "../shared/ConfirmDialog";
import { CopyButton } from "../shared/CopyButton";
import { showError, showSuccess } from "../../store/toastStore";
import { useProjectStore } from "../../store/projectStore";
import { getTlsStatus } from "../../api/systemClient";
import * as presentApi from "../../api/presentClient";
import type { HostOutputs, PresentDisplay, PresentStatus } from "../../api/presentClient";

// The Present plugin's management panel, rendered on its detail page in the
// Plugins view: the connect address guests type (with an HTTPS warning when
// screen capture would be blocked), the space's displays (add/edit/delete,
// display links, keys), and the routing matrix (per-display source).
// Platform-side component keyed to the plugin id; generalize a
// plugin-management-panel slot only if a second plugin needs one.

// How often the panel refreshes who's presenting and what each display shows
// while it is on screen. Matches the plugin's own presence poll cadence
// closely enough to feel live without hammering the server.
const REFRESH_MS = 3000;

// Display add/edit/delete and routing persist into the project's plugin
// config server-side, outside the project store's save path. Re-sync the
// project store afterward so its in-memory project + ETag track the server
// and a later UI Builder save can't overwrite the display list. No-op when
// there are unsaved edits; the server-side revision bump + 409 guard covers
// that case.
async function syncProjectStore() {
  await useProjectStore.getState().load();
}

const labelStyle: CSSProperties = {
  display: "block",
  fontSize: 11,
  color: "var(--text-muted)",
  textTransform: "uppercase",
  letterSpacing: "0.5px",
  marginBottom: 4,
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
  return s || "display";
}

// The plugin returns a site-relative Display URL (key included); the copyable
// link needs the absolute form a separate display device can open.
function displayUrl(display: PresentDisplay): string {
  return `${window.location.origin}${display.display_path}`;
}

// Stream displays are pulled straight from the media helper's LAN listeners.
// The plugin supplies the path (stream key included) and ports; the host is
// whatever this browser reached the server on — the best default a copyable
// URL can have.
function rtspUrl(display: PresentDisplay): string {
  return `rtsp://${window.location.hostname}:${display.rtsp_port ?? 8554}/${display.stream_path ?? ""}`;
}

function srtUrl(display: PresentDisplay): string {
  return `srt://${window.location.hostname}:${display.srt_port ?? 8899}?streamid=read:${display.stream_path ?? ""}`;
}

// The join line is scheme-qualified when the instance runs HTTPS (the card
// shows exactly what a guest should type); older/plain-HTTP forms are bare
// host:port and open over http.
function joinHref(joinUrl: string): string {
  return /^https?:\/\//i.test(joinUrl) ? joinUrl : `http://${joinUrl}`;
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div style={{ flex: 1, minWidth: 0 }}>
      <label style={labelStyle}>{label}</label>
      {children}
    </div>
  );
}

// The local-window status chip on a display row. Running is quiet ("On
// <output>"); the waiting/error states are what the integrator needs to see.
function localStateChip(d: PresentDisplay): { text: string; warn: boolean } | null {
  if (!d.local_output) return null;
  switch (d.local_state) {
    case "waiting_for_output":
      return { text: "Output not connected", warn: true };
    case "waiting_for_signin":
      return { text: "Waiting for Windows sign-in", warn: true };
    case "error":
      return { text: "Window error — see System Log", warn: true };
    case "unsupported":
      return { text: "Not supported on this server", warn: true };
    case "starting":
      return { text: "Opening window…", warn: false };
    case "running":
      return { text: `On ${d.local_output_name || "this server"}`, warn: false };
    default:
      return null;
  }
}

function outputOptionLabel(o: presentApi.HostOutput, currentDisplayId: string | null): string {
  let label = `${o.name} — ${o.width}x${o.height}`;
  if (o.primary) label += " (primary)";
  if (o.in_use_by && o.in_use_by !== currentDisplayId) label += ` — in use by ${o.in_use_by}`;
  return label;
}

function DisplayForm({
  display,
  onClose,
  onSaved,
}: {
  display: PresentDisplay | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [label, setLabel] = useState(display?.label ?? "");
  const [displayId, setDisplayId] = useState(display?.id ?? "");
  const [idTouched, setIdTouched] = useState(!!display);
  const [kind, setKind] = useState(display?.kind ?? "browser");
  const [localOutput, setLocalOutput] = useState(display?.local_output ?? "");
  // null = still loading; the picker renders once the answer is in.
  const [hostOutputs, setHostOutputs] = useState<HostOutputs | null>(null);
  const [saving, setSaving] = useState(false);

  // This server's video outputs, for the "show it from this server" picker.
  // Fetched fresh per form open so a just-plugged output appears.
  useEffect(() => {
    presentApi
      .getOutputs()
      .then(setHostOutputs)
      .catch(() => setHostOutputs({ supported: false, reason: "", outputs: [] }));
  }, []);

  const effectiveId = (idTouched ? displayId : slugify(label)).trim();
  const valid = label.trim() && effectiveId;

  // Non-primary outputs first: the primary is usually the console/panel
  // screen, so the natural pick sits at the top of the list.
  const pickableOutputs = [...(hostOutputs?.outputs ?? [])].sort(
    (a, b) => Number(a.primary) - Number(b.primary),
  );
  const selectedOutput = pickableOutputs.find((o) => o.id === localOutput);
  const selectedMissing = !!localOutput && !selectedOutput;

  async function save() {
    if (!valid) return;
    setSaving(true);
    try {
      const payload = {
        label: label.trim(),
        display_id: effectiveId,
        kind,
        // Always sent: "" clears. A stream display never keeps one.
        local_output: kind === "browser" ? localOutput : "",
      };
      if (display) await presentApi.updateDisplay(display.id, payload);
      else await presentApi.createDisplay(payload);
      showSuccess(display ? "Display updated." : "Display added.");
      onSaved();
    } catch (err) {
      showError(presentApi.errorMessage(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog title={display ? "Edit Display" : "Add Display"} onClose={onClose}>
      <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-md)" }}>
        <Field label="Name">
          <input
            style={inputStyle}
            value={label}
            placeholder="Main Screen"
            onChange={(e) => setLabel(e.target.value)}
            autoFocus
          />
        </Field>
        <Field label="Display ID">
          <input
            style={inputStyle}
            value={effectiveId}
            onChange={(e) => {
              setIdTouched(true);
              setDisplayId(e.target.value);
            }}
          />
        </Field>
        <Field label="Type">
          <select style={{ ...inputStyle, cursor: "pointer" }} value={kind} onChange={(e) => setKind(e.target.value)}>
            <option value="browser">Browser — a device opens the display link in a browser</option>
            <option value="stream">Stream — a hardware decoder pulls an RTSP/SRT address</option>
          </select>
        </Field>
        {kind === "stream" && (!display || display.kind !== "stream") && (
          <p style={{ margin: 0, fontSize: "var(--font-size-sm)", color: "var(--text-muted)", lineHeight: 1.5 }}>
            A stream display runs a continuous encoder on this server and
            publishes RTSP and SRT addresses for a decoder to pull. Expect
            about a second of latency; use a browser display where latency
            matters most.
          </p>
        )}
        {kind === "browser" && hostOutputs && (hostOutputs.supported || localOutput) && (
          <Field label="Show on this server's output">
            <select
              style={{ ...inputStyle, cursor: "pointer" }}
              value={localOutput}
              onChange={(e) => setLocalOutput(e.target.value)}
            >
              <option value="">Not shown from this server</option>
              {pickableOutputs.map((o) => (
                <option
                  key={o.id}
                  value={o.id}
                  disabled={!!o.in_use_by && o.in_use_by !== display?.id}
                >
                  {outputOptionLabel(o, display?.id ?? null)}
                </option>
              ))}
              {selectedMissing && (
                <option value={localOutput}>
                  {(display?.local_output_name || "Configured output") + " (not connected)"}
                </option>
              )}
            </select>
          </Field>
        )}
        {kind === "browser" && hostOutputs && !hostOutputs.supported && hostOutputs.reason && (
          <p style={{ margin: 0, fontSize: "var(--font-size-sm)", color: "var(--text-muted)", lineHeight: 1.5 }}>
            This display can&apos;t be shown from this server: {hostOutputs.reason}
          </p>
        )}
        {kind === "browser" && selectedOutput?.primary && (
          <p style={{ margin: 0, fontSize: "var(--font-size-sm)", color: "var(--color-warning, #b26a00)", lineHeight: 1.5 }}>
            That is this server&apos;s primary screen — usually the console or
            panel display. Present will cover it whenever this display is on.
          </p>
        )}
        {kind === "browser" && selectedMissing && (
          <p style={{ margin: 0, fontSize: "var(--font-size-sm)", color: "var(--color-warning, #b26a00)", lineHeight: 1.5 }}>
            The configured output isn&apos;t connected right now. The window
            opens automatically when it returns, or pick a connected output
            above.
          </p>
        )}
        {display && effectiveId !== display.id && (
          <p style={{ margin: 0, fontSize: "var(--font-size-sm)", color: "var(--color-warning, #b26a00)" }}>
            Changing the display ID changes its display link
            {display.kind === "stream" ? " and its stream addresses" : ""}. Any
            device already using the old one will need the new one.
          </p>
        )}
      </div>

      <div style={{ display: "flex", justifyContent: "flex-end", gap: "var(--space-sm)", marginTop: "var(--space-lg)" }}>
        <button style={secondaryBtn} onClick={onClose}>
          Cancel
        </button>
        <button style={{ ...primaryBtn, opacity: valid && !saving ? 1 : 0.5 }} onClick={save} disabled={!valid || saving}>
          {saving ? "Saving..." : display ? "Save" : "Add Display"}
        </button>
      </div>
    </Dialog>
  );
}

function SourceSelect({
  display,
  status,
  onRouted,
}: {
  display: PresentDisplay;
  status: PresentStatus | null;
  onRouted: () => void;
}) {
  const [routing, setRouting] = useState(false);

  // The pick list is auto + live presenters. A pinned presenter who isn't
  // sharing must still appear (marked), or the select would misreport the
  // actual assignment.
  const options = [...(status?.sources ?? [{ value: "auto", label: "Auto (active presenter)" }])];
  if (display.source !== "auto" && !options.some((o) => o.value === display.source)) {
    options.push({ value: display.source, label: `${display.source} (not sharing)` });
  }

  async function route(source: string) {
    if (source === display.source) return;
    setRouting(true);
    try {
      await presentApi.routeDisplay(display.id, source);
      onRouted();
    } catch (err) {
      showError(presentApi.errorMessage(err));
    } finally {
      setRouting(false);
    }
  }

  return (
    <select
      value={display.source}
      disabled={routing}
      onChange={(e) => route(e.target.value)}
      title="Source this display shows"
      style={{
        ...inputStyle,
        width: 170,
        flexShrink: 0,
        cursor: "pointer",
        opacity: routing ? 0.6 : 1,
      }}
    >
      {options.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  );
}

export function PresentManagementPanel({ running }: { running: boolean }) {
  const [displays, setDisplays] = useState<PresentDisplay[]>([]);
  const [status, setStatus] = useState<PresentStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState<PresentDisplay | "new" | null>(null);
  const [deleting, setDeleting] = useState<PresentDisplay | null>(null);
  const [rekeying, setRekeying] = useState<PresentDisplay | null>(null);
  // null = unknown (fetch failed): show nothing rather than warn wrongly.
  const [tlsEnabled, setTlsEnabled] = useState<boolean | null>(null);
  // Cloud-paired systems can install a trusted certificate, which removes
  // the guest browser warning — worth a pointer in the HTTPS-off warning.
  const [cloudCertPaired, setCloudCertPaired] = useState(false);
  // Background refreshes must not flash the loading state or toast transient
  // errors; only the first load and manual refreshes report.
  const firstLoad = useRef(true);

  // Screen capture in a guest's browser needs a secure context, so the
  // integrator should hear about a disabled-HTTPS instance here, next to the
  // connect address — not from a confused guest. One fetch per mount is
  // enough (TLS changes require a server restart anyway).
  useEffect(() => {
    if (!running) return;
    getTlsStatus()
      .then((s) => {
        setTlsEnabled(!!s.enabled);
        setCloudCertPaired(!!s.cloud_cert?.paired);
      })
      .catch(() => setTlsEnabled(null));
  }, [running]);

  const refresh = useCallback(async (quiet = false) => {
    if (!quiet) setLoading(true);
    try {
      const [d, s] = await Promise.all([presentApi.listDisplays(), presentApi.getStatus()]);
      setDisplays(d);
      setStatus(s);
    } catch (err) {
      if (!quiet) showError(presentApi.errorMessage(err));
    } finally {
      if (!quiet) setLoading(false);
      firstLoad.current = false;
    }
  }, []);

  useEffect(() => {
    if (!running) return;
    refresh();
    const timer = setInterval(() => refresh(true), REFRESH_MS);
    return () => clearInterval(timer);
  }, [running, refresh]);

  async function confirmDelete() {
    if (!deleting) return;
    const d = deleting;
    setDeleting(null);
    try {
      await presentApi.deleteDisplay(d.id);
      showSuccess(`Removed "${d.label}".`);
      refresh(true);
      syncProjectStore();
    } catch (err) {
      showError(presentApi.errorMessage(err));
    }
  }

  async function confirmRekey() {
    if (!rekeying) return;
    const d = rekeying;
    setRekeying(null);
    try {
      if (d.kind === "stream") {
        await presentApi.regenerateStreamKey(d.id);
        showSuccess(`New stream addresses created for "${d.label}".`);
      } else {
        await presentApi.regenerateKey(d.id);
        showSuccess(`New display link created for "${d.label}".`);
      }
      refresh(true);
      syncProjectStore();
    } catch (err) {
      showError(presentApi.errorMessage(err));
    }
  }

  if (!running) {
    return (
      <div
        style={{
          marginBottom: "var(--space-lg)",
          padding: "var(--space-md)",
          borderRadius: "var(--border-radius)",
          border: "1px dashed var(--border-color)",
          color: "var(--text-muted)",
          fontSize: "var(--font-size-sm)",
        }}
      >
        Enable the plugin to manage its displays.
      </div>
    );
  }

  const presenting = status?.presenters ?? [];

  return (
    <div style={{ marginBottom: "var(--space-lg)", maxWidth: 640 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "var(--space-md)" }}>
        <h3 style={{ fontSize: "var(--font-size-sm)", fontWeight: 600, color: "var(--text-secondary)", margin: 0 }}>
          Displays
        </h3>
        <div style={{ display: "flex", gap: "var(--space-sm)" }}>
          <button
            onClick={() => refresh()}
            title="Refresh"
            style={{ display: "flex", padding: 6, borderRadius: "var(--border-radius)", background: "var(--bg-hover)", border: "none", color: "var(--text-secondary)", cursor: "pointer" }}
          >
            <RefreshCw size={15} />
          </button>
          <button
            onClick={() => setEditing("new")}
            style={{ display: "flex", alignItems: "center", gap: "var(--space-xs)", ...primaryBtn, padding: "var(--space-xs) var(--space-md)" }}
          >
            <Plus size={15} /> Add Display
          </button>
        </div>
      </div>

      {/* Space status strip: what the connect cards show, who's presenting. */}
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          gap: "var(--space-xs)",
          padding: "var(--space-sm) var(--space-md)",
          marginBottom: "var(--space-sm)",
          borderRadius: "var(--border-radius)",
          background: "var(--bg-surface)",
          border: "1px solid var(--border-color)",
          fontSize: "var(--font-size-sm)",
          color: "var(--text-secondary)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "var(--space-lg)" }}>
          <span style={{ flex: 1, minWidth: 0, display: "flex", alignItems: "center", gap: 4 }}>
            Guests connect at{" "}
            <strong
              style={{
                fontFamily: "var(--font-mono)",
                color: "var(--text-primary)",
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
              }}
            >
              {status?.join_url || "—"}
            </strong>
            {status?.join_url && (
              <>
                <CopyButton value={status.join_url} title="Copy connect address" />
                <button
                  onClick={() => window.open(joinHref(status.join_url), "_blank", "noopener")}
                  title="Open connect page"
                  style={iconBtnStyle}
                >
                  <ExternalLink size={13} />
                </button>
              </>
            )}
          </span>
          <span style={{ flexShrink: 0 }}>
            Join code{" "}
            <strong style={{ fontFamily: "var(--font-mono)", color: "var(--text-primary)", letterSpacing: "0.15em" }}>
              {status?.code || "—"}
            </strong>
          </span>
        </div>
        <div style={{ color: presenting.length ? "var(--color-success, #2e7d32)" : "var(--text-muted)" }}>
          {presenting.length
            ? `Presenting: ${presenting.map((p) => p.label || p.name).join(", ")}`
            : "No one is presenting"}
        </div>
      </div>

      {tlsEnabled === false && (
        <div
          style={{
            display: "flex",
            alignItems: "flex-start",
            gap: "var(--space-sm)",
            padding: "var(--space-sm) var(--space-md)",
            marginBottom: "var(--space-sm)",
            borderRadius: "var(--border-radius)",
            border: "1px solid var(--color-warning, #b26a00)",
            color: "var(--text-secondary)",
            fontSize: "var(--font-size-sm)",
            lineHeight: 1.5,
          }}
        >
          <ShieldAlert size={16} style={{ flexShrink: 0, marginTop: 2, color: "var(--color-warning, #b26a00)" }} />
          <span>
            Guests can&apos;t share their screen yet: browsers only allow screen
            capture over HTTPS, and HTTPS is off on this system. Enable it in
            Settings &gt; Security. Displays are not affected.
            {cloudCertPaired && (
              <>
                {" "}This system is paired with the cloud, so a trusted
                certificate (same Settings page) can also remove the guest
                browser warning entirely.
              </>
            )}
          </span>
        </div>
      )}

      <div style={{ background: "var(--bg-surface)", borderRadius: "var(--border-radius)", border: "1px solid var(--border-color)", overflow: "hidden" }}>
        {loading && firstLoad.current ? (
          <div style={{ padding: "var(--space-lg)", color: "var(--text-muted)", fontSize: "var(--font-size-sm)" }}>Loading displays...</div>
        ) : displays.length === 0 ? (
          <div style={{ padding: "var(--space-lg)", color: "var(--text-muted)", fontSize: "var(--font-size-sm)" }}>
            No displays yet. Add one, then open its display link in a browser
            on the device driving that screen — or add a stream display and
            point a hardware decoder at its address.
          </div>
        ) : (
          displays.map((d, i) => (
            <div
              key={d.id}
              style={{
                padding: "var(--space-sm) var(--space-md)",
                borderTop: i === 0 ? "none" : "1px solid var(--border-color)",
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: "var(--space-md)" }}>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: "var(--font-size-sm)", color: "var(--text-primary)" }}>{d.label}</div>
                  <div style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 11, color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
                    <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{d.id}</span>
                    {d.kind !== "stream" && <CopyButton value={displayUrl(d)} title="Copy display link" />}
                  </div>
                </div>
                <span
                  title={d.showing ? `Showing ${d.showing}` : "Showing the connect card"}
                  style={{
                    fontSize: 10,
                    fontWeight: 600,
                    textTransform: "uppercase",
                    letterSpacing: "0.5px",
                    flexShrink: 0,
                    color: d.output_state === "live" ? "var(--color-success, #2e7d32)" : "var(--text-muted)",
                  }}
                >
                  {d.output_state === "live" ? `Live: ${d.showing}` : "Idle"}
                </span>
                {(() => {
                  const chip = localStateChip(d);
                  if (!chip) return null;
                  return (
                    <span
                      title="This display's fullscreen window on this server"
                      style={{
                        fontSize: 10,
                        fontWeight: 600,
                        flexShrink: 0,
                        padding: "2px 8px",
                        borderRadius: 999,
                        border: `1px solid ${chip.warn ? "var(--color-warning, #b26a00)" : "var(--border-color)"}`,
                        color: chip.warn ? "var(--color-warning, #b26a00)" : "var(--text-muted)",
                      }}
                    >
                      {chip.text}
                    </span>
                  );
                })()}
                <SourceSelect
                  display={d}
                  status={status}
                  onRouted={() => {
                    refresh(true);
                    syncProjectStore();
                  }}
                />
                {d.kind !== "stream" && (
                  <button
                    onClick={() => window.open(displayUrl(d), "_blank", "noopener")}
                    title="Open display page"
                    style={iconBtnStyle}
                  >
                    <ExternalLink size={15} />
                  </button>
                )}
                <button
                  onClick={() => setRekeying(d)}
                  title={d.kind === "stream" ? "Regenerate stream key" : "Regenerate display link"}
                  style={iconBtnStyle}
                >
                  <KeyRound size={15} />
                </button>
                <button onClick={() => setEditing(d)} title="Edit" style={iconBtnStyle}>
                  <Pencil size={15} />
                </button>
                <button onClick={() => setDeleting(d)} title="Remove" style={iconBtnStyle}>
                  <Trash2 size={15} />
                </button>
              </div>
              {d.kind === "stream" && (
                <div style={{ marginTop: 6, display: "flex", flexDirection: "column", gap: 2 }}>
                  {[
                    ["RTSP", rtspUrl(d), "Copy RTSP address"],
                    ["SRT", srtUrl(d), "Copy SRT address"],
                  ].map(([proto, url, copyTitle]) => (
                    <div key={proto} style={{ display: "flex", alignItems: "center", gap: 6, minWidth: 0 }}>
                      <span
                        style={{
                          fontSize: 10,
                          fontWeight: 600,
                          letterSpacing: "0.5px",
                          color: "var(--text-muted)",
                          width: 32,
                          flexShrink: 0,
                        }}
                      >
                        {proto}
                      </span>
                      <span
                        style={{
                          fontSize: 11,
                          fontFamily: "var(--font-mono)",
                          color: "var(--text-secondary)",
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                          whiteSpace: "nowrap",
                          minWidth: 0,
                        }}
                      >
                        {url}
                      </span>
                      <CopyButton value={url} title={copyTitle} />
                    </div>
                  ))}
                  {d.encoder_state && !["idle", "live"].includes(d.encoder_state) && (
                    <span
                      style={{
                        fontSize: 11,
                        color:
                          d.encoder_state === "starting"
                            ? "var(--text-muted)"
                            : "var(--color-warning, #b26a00)",
                      }}
                    >
                      {d.encoder_state === "starting"
                        ? "Encoder starting — the stream appears in a few seconds…"
                        : d.encoder_state === "error"
                          ? "Encoder error — check the System Log."
                          : "Encoder stopped."}
                    </span>
                  )}
                </div>
              )}
            </div>
          ))
        )}
      </div>

      <p style={{ marginTop: "var(--space-md)", fontSize: "var(--font-size-sm)", color: "var(--text-muted)", lineHeight: 1.5 }}>
        A browser display has a link to open, full screen, in a browser on
        the device driving that screen — or, when this server has a video
        output at the display, pick that output in the display&apos;s
        settings and the server opens the window itself. A stream display
        instead shows RTSP and SRT addresses for a hardware decoder to pull.
        Both carry a secret (the link&apos;s key, the address&apos;s stream
        key) — treat them like passwords; the key button issues new ones.
        Source picks what a display shows: Auto follows the active
        presenter; pinning a presenter holds their screen there. If the
        connect address above isn&apos;t reachable from guests&apos; laptops
        (multiple networks, VLANs), set Join Address in the plugin&apos;s
        configuration below.
      </p>

      {editing && (
        <DisplayForm
          display={editing === "new" ? null : editing}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            refresh(true);
            syncProjectStore();
          }}
        />
      )}
      {deleting && (
        <ConfirmDialog
          title="Remove display"
          message={`Remove "${deleting.label}"? Its display link will stop working.`}
          confirmLabel="Remove"
          destructive
          onConfirm={confirmDelete}
          onCancel={() => setDeleting(null)}
        />
      )}
      {rekeying && (
        <ConfirmDialog
          title={rekeying.kind === "stream" ? "Regenerate stream key" : "Regenerate display link"}
          message={
            rekeying.kind === "stream"
              ? `Create new stream addresses for "${rekeying.label}"? The current RTSP and SRT addresses stop working immediately — every decoder pulling them must be given the new address.`
              : `Create a new display link for "${rekeying.label}"? Every copy of the current link stops working immediately, including the device that is using it right now.`
          }
          confirmLabel="Regenerate"
          destructive
          onConfirm={confirmRekey}
          onCancel={() => setRekeying(null)}
        />
      )}
    </div>
  );
}
