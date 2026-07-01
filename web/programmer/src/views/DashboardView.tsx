import { useState, useEffect, useCallback, useMemo } from "react";
import { Cpu, Zap, Cloud, FileCode, AlertTriangle, Clock, ArrowRight, ArrowUpCircle, Monitor, Copy, Check, ExternalLink, QrCode, Printer } from "lucide-react";
import qrcode from "qrcode-generator";
import { ViewContainer } from "../components/layout/ViewContainer";
import { DeviceStatusDot } from "../components/shared/DeviceStatusDot";
import { Dialog } from "../components/shared/Dialog";
import { useProjectStore } from "../store/projectStore";
import { useConnectionStore } from "../store/connectionStore";
import { useLogStore } from "../store/logStore";
import { useNavigationStore } from "../store/navigationStore";
import { StatusCardSlot } from "../components/plugins/PluginExtensions";
import { showError } from "../store/toastStore";
import * as api from "../api/restClient";
import type { CloudStatus } from "../api/restClient";

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  const handleCopy = useCallback(() => {
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }).catch(() => {});
  }, [text]);
  return (
    <button
      type="button"
      onClick={handleCopy}
      title="Copy to clipboard"
      style={{
        background: "none",
        border: "none",
        color: copied ? "var(--color-success)" : "var(--text-muted)",
        cursor: "pointer",
        padding: 4,
        flexShrink: 0,
      }}
    >
      {copied ? <Check size={14} /> : <Copy size={14} />}
    </button>
  );
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// Full-page, print-ready poster: big headline + big QR + subtle OpenAVC branding
// and a faint sage "signal ripple" motif in opposite corners. Rendered into a
// standalone window so none of the IDE's styling bleeds into the printout.
function buildPosterHtml({ qrSvg, url, roomName, logoSrc }: { qrSvg: string; url: string; roomName: string; logoSrc: string }): string {
  const room = roomName.trim() ? escapeHtml(roomName.trim()) : "this room";
  const safeUrl = escapeHtml(url);
  return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Scan to control ${room}</title>
<style>
  :root { --sage: #8AB493; --sage-deep: #4a7d5c; --ink: #23302a; --muted: #6a7b70; }
  * { box-sizing: border-box; -webkit-print-color-adjust: exact; print-color-adjust: exact; }
  html, body { margin: 0; padding: 0; height: 100%; background: #fff; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    color: var(--ink);
    display: flex; flex-direction: column; align-items: center; justify-content: center;
    text-align: center; min-height: 100vh; position: relative; overflow: hidden;
    padding: 18mm 16mm;
  }
  .accent { position: fixed; width: 92mm; height: 92mm; opacity: 0.12; pointer-events: none; z-index: 0; }
  .accent svg { width: 100%; height: 100%; display: block; }
  .accent-tl { top: -30mm; left: -30mm; }
  .accent-br { bottom: -30mm; right: -30mm; }
  .content { position: relative; z-index: 1; display: flex; flex-direction: column; align-items: center; }
  .brand { height: 8mm; width: auto; opacity: 0.75; margin-bottom: 13mm; }
  .headline { font-size: 30pt; font-weight: 700; line-height: 1.22; margin: 0; letter-spacing: -0.01em; }
  .headline .room { color: var(--sage-deep); }
  .qr-wrap { margin: 12mm 0 8mm; padding: 6mm; background: #fff; border: 1px solid #e6ece8; border-radius: 4mm; }
  .qr { width: 92mm; height: 92mm; }
  .qr svg { width: 100%; height: 100%; display: block; }
  .steps { font-size: 13pt; color: var(--muted); line-height: 1.7; margin: 0; }
  .steps b { color: var(--ink); font-weight: 600; }
  .url { margin-top: 6mm; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
         font-size: 12pt; color: var(--sage-deep); word-break: break-all; }
  @page { margin: 12mm; }
</style>
</head>
<body>
  <div class="accent accent-tl" aria-hidden="true">
    <svg viewBox="0 0 200 200"><g fill="none" stroke="#8AB493" stroke-width="7">
      <circle cx="0" cy="0" r="55"/><circle cx="0" cy="0" r="100"/><circle cx="0" cy="0" r="145"/><circle cx="0" cy="0" r="190"/>
    </g></svg>
  </div>
  <div class="accent accent-br" aria-hidden="true">
    <svg viewBox="0 0 200 200"><g fill="none" stroke="#4a7d5c" stroke-width="7">
      <circle cx="200" cy="200" r="55"/><circle cx="200" cy="200" r="100"/><circle cx="200" cy="200" r="145"/><circle cx="200" cy="200" r="190"/>
    </g></svg>
  </div>
  <div class="content">
    <img id="brand-logo" class="brand" src="${logoSrc}" alt="OpenAVC">
    <h1 class="headline">To control <span class="room">${room}</span>,<br>scan this QR code</h1>
    <div class="qr-wrap"><div class="qr">${qrSvg}</div></div>
    <p class="steps"><b>1.</b> Open your phone or tablet camera &nbsp;&nbsp; <b>2.</b> Tap the link that appears</p>
    <div class="url">${safeUrl}</div>
  </div>
  <script>
    (function () {
      function go() { try { window.focus(); window.print(); } catch (e) {} }
      var img = document.getElementById('brand-logo');
      if (!img || img.complete) { setTimeout(go, 120); return; }
      img.addEventListener('load', function () { setTimeout(go, 80); });
      img.addEventListener('error', function () { setTimeout(go, 80); });
    })();
  </script>
</body>
</html>`;
}

function QRCodeDialog({ url, roomName, onClose }: { url: string; roomName: string; onClose: () => void }) {
  const svgMarkup = useMemo(() => {
    const qr = qrcode(0, "M");
    qr.addData(url);
    qr.make();
    return qr.createSvgTag({ cellSize: 8, margin: 2, scalable: true });
  }, [url]);

  const handlePrint = useCallback(async () => {
    // Inline the logo as a data URI so the standalone print window is fully
    // self-contained (no dependence on cross-window asset loading/timing).
    let logoSrc = new URL(`${import.meta.env.BASE_URL}logo-wide.png`, document.baseURI).href;
    try {
      const resp = await fetch(logoSrc);
      if (resp.ok) {
        const blob = await resp.blob();
        logoSrc = await new Promise<string>((resolve, reject) => {
          const reader = new FileReader();
          reader.onload = () => resolve(String(reader.result));
          reader.onerror = () => reject(new Error("logo read failed"));
          reader.readAsDataURL(blob);
        });
      }
    } catch {
      /* fall back to the resolved URL */
    }

    const posterHtml = buildPosterHtml({ qrSvg: svgMarkup, url, roomName, logoSrc });
    const win = window.open("", "_blank");
    if (!win) {
      showError("Couldn't open the print view. Allow pop-ups for this site, then try again.");
      return;
    }
    win.document.open();
    win.document.write(posterHtml);
    win.document.close();
  }, [svgMarkup, url, roomName]);

  return (
    <Dialog title="Scan to connect" onClose={onClose}>
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: "var(--space-md)" }}>
        <div style={{ color: "var(--text-muted)", fontSize: "var(--font-size-sm)", textAlign: "center" }}>
          Scan with a phone or tablet camera to open this OpenAVC system.
        </div>
        <div
          style={{ width: 260, height: 260, background: "#fff", padding: "var(--space-sm)", borderRadius: "var(--border-radius)" }}
          dangerouslySetInnerHTML={{ __html: svgMarkup }}
        />
        <code style={{ fontSize: 12, fontFamily: "var(--font-mono)", color: "var(--text-muted)", wordBreak: "break-all", textAlign: "center" }}>
          {url}
        </code>
        <div style={{
          display: "flex",
          gap: "var(--space-md)",
          fontSize: "var(--font-size-sm)",
          flexWrap: "wrap",
          justifyContent: "center",
        }}>
          <a
            href="https://docs.openavc.com/panel-app-dedicated-android"
            target="_blank"
            rel="noopener noreferrer"
            style={{ color: "var(--accent)", textDecoration: "none" }}
          >
            Android dedicated panel setup
          </a>
          <a
            href="https://docs.openavc.com/panel-app-dedicated-ios"
            target="_blank"
            rel="noopener noreferrer"
            style={{ color: "var(--accent)", textDecoration: "none" }}
          >
            iOS dedicated panel setup
          </a>
        </div>
        <div style={{ display: "flex", gap: "var(--space-sm)", marginTop: "var(--space-sm)" }}>
          <button
            type="button"
            onClick={handlePrint}
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: "var(--space-xs)",
              padding: "var(--space-sm) var(--space-lg)",
              background: "var(--accent-bg)",
              color: "#fff",
              border: "none",
              borderRadius: "var(--border-radius)",
              cursor: "pointer",
              fontSize: "var(--font-size-sm)",
              fontWeight: 500,
            }}
          >
            <Printer size={14} />
            Print sign
          </button>
          <button
            type="button"
            onClick={onClose}
            style={{
              padding: "var(--space-sm) var(--space-lg)",
              background: "var(--bg-hover)",
              color: "var(--text-primary)",
              border: "1px solid var(--border-color)",
              borderRadius: "var(--border-radius)",
              cursor: "pointer",
              fontSize: "var(--font-size-sm)",
              fontWeight: 500,
            }}
          >
            Close
          </button>
        </div>
      </div>
    </Dialog>
  );
}

function PanelAccessCard({ systemStatus, roomName }: { systemStatus: Record<string, unknown> | null; roomName: string }) {
  const [qrOpen, setQrOpen] = useState(false);
  if (!systemStatus) return null;

  const localIp = String(systemStatus.local_ip ?? "");
  const hostname = String(systemStatus.hostname ?? "");
  const port = Number(systemStatus.http_port ?? 8080);
  const bindAddress = String(systemStatus.bind_address ?? "127.0.0.1");

  const isLocalOnly = bindAddress === "127.0.0.1" || bindAddress === "::1";

  const panelUrl = localIp && !isLocalOnly
    ? `http://${localIp}${port === 80 ? "" : ":" + String(port)}/panel`
    : "";
  const hostnameUrl = hostname && !isLocalOnly
    ? `http://${hostname}${port === 80 ? "" : ":" + String(port)}/panel`
    : "";
  const pairUrl = localIp && !isLocalOnly
    ? `http://${localIp}${port === 80 ? "" : ":" + String(port)}/pair`
    : "";

  const cardStyle: React.CSSProperties = {
    background: "var(--bg-surface)",
    border: "1px solid var(--border-color)",
    borderRadius: "var(--border-radius)",
    padding: "var(--space-lg)",
  };
  const sectionTitle: React.CSSProperties = {
    fontSize: "var(--font-size-sm)",
    color: "var(--text-secondary)",
    textTransform: "uppercase",
    letterSpacing: "0.5px",
    fontWeight: 600,
    marginBottom: "var(--space-md)",
  };

  return (
    <div style={{ marginBottom: "var(--space-xl)" }}>
      <h3 style={sectionTitle}>
        <span style={{ display: "flex", alignItems: "center", gap: "var(--space-xs)" }}>
          <Monitor size={14} />
          Panel Access
        </span>
      </h3>
      <div style={cardStyle}>
        {isLocalOnly ? (
          <div style={{ fontSize: "var(--font-size-sm)" }}>
            <div style={{ color: "var(--text-muted)", marginBottom: "var(--space-sm)" }}>
              The server is set to local-only access. To open the panel on a tablet or phone, go to{" "}
              <strong
                onClick={() => useNavigationStore.getState().navigateTo("settings")}
                style={{ color: "var(--accent)", cursor: "pointer" }}
              >
                Settings
              </strong>{" "}
              and change the bind address to <code>0.0.0.0</code>.
            </div>
          </div>
        ) : (
          <div style={{ fontSize: "var(--font-size-sm)" }}>
            <div style={{ color: "var(--text-muted)", marginBottom: "var(--space-sm)" }}>
              Open this URL on a tablet, phone, or any device on the same network:
            </div>
            {panelUrl && (
              <div style={{
                display: "flex",
                alignItems: "center",
                gap: "var(--space-xs)",
                background: "var(--bg-elevated, var(--bg-hover))",
                borderRadius: "var(--border-radius)",
                padding: "var(--space-sm) var(--space-md)",
                marginBottom: hostnameUrl ? "var(--space-xs)" : 0,
              }}>
                <code style={{ flex: 1, fontSize: 13, fontFamily: "var(--font-mono)", wordBreak: "break-all" }}>
                  {panelUrl}
                </code>
                <CopyButton text={panelUrl} />
                <a
                  href={panelUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  title="Open in new tab"
                  style={{ color: "var(--text-muted)", padding: 4, flexShrink: 0 }}
                >
                  <ExternalLink size={14} />
                </a>
              </div>
            )}
            {hostnameUrl && hostname !== localIp && (
              <div style={{
                display: "flex",
                alignItems: "center",
                gap: "var(--space-xs)",
                background: "var(--bg-elevated, var(--bg-hover))",
                borderRadius: "var(--border-radius)",
                padding: "var(--space-sm) var(--space-md)",
              }}>
                <code style={{ flex: 1, fontSize: 13, fontFamily: "var(--font-mono)", wordBreak: "break-all" }}>
                  {hostnameUrl}
                </code>
                <CopyButton text={hostnameUrl} />
              </div>
            )}
            {panelUrl && (
              <button
                type="button"
                onClick={() => setQrOpen(true)}
                style={{
                  marginTop: "var(--space-md)",
                  display: "inline-flex",
                  alignItems: "center",
                  gap: "var(--space-xs)",
                  padding: "var(--space-xs) var(--space-md)",
                  background: "var(--bg-hover)",
                  color: "var(--text-primary)",
                  border: "1px solid var(--border-color)",
                  borderRadius: "var(--border-radius)",
                  cursor: "pointer",
                  fontSize: "var(--font-size-sm)",
                }}
              >
                <QrCode size={14} />
                Show QR code
              </button>
            )}
          </div>
        )}
      </div>
      {qrOpen && pairUrl && <QRCodeDialog url={pairUrl} roomName={roomName} onClose={() => setQrOpen(false)} />}
    </div>
  );
}

export function DashboardView() {
  const project = useProjectStore((s) => s.project);
  const liveState = useConnectionStore((s) => s.liveState);
  const [cloudStatus, setCloudStatus] = useState<CloudStatus | null>(null);
  const [systemStatus, setSystemStatus] = useState<Record<string, unknown> | null>(null);
  const [, setRefreshTick] = useState(0);
  const [fetchError, setFetchError] = useState<string | null>(null);

  useEffect(() => {
    const fetchAll = () => {
      api.getSystemStatus().then(s => { setSystemStatus(s); setFetchError(null); }).catch(e => setFetchError(`Unable to reach server: ${e.message || e}`));
      api.getCloudStatus().then(s => setCloudStatus(s)).catch(() => {});
      setRefreshTick(t => t + 1);
    };
    fetchAll();
    const interval = setInterval(fetchAll, 30000);
    return () => clearInterval(interval);
  }, []);

  if (!project) {
    return <ViewContainer title="Dashboard"><p style={{ color: "var(--text-muted)" }}>Loading...</p></ViewContainer>;
  }

  const devices = project.devices;
  const connectedCount = devices.filter(d => {
    if (d.enabled === false) return false;
    return liveState[`device.${d.id}.connected`] === true;
  }).length;
  const enabledCount = devices.filter(d => d.enabled !== false).length;
  const disconnectedCount = enabledCount - connectedCount;

  const triggerCount = project.macros.reduce((sum, m) => sum + (m.triggers?.filter(t => t.enabled !== false).length ?? 0), 0);
  const scriptCount = project.scripts.filter(s => s.enabled).length;

  const isCloudConnected = cloudStatus?.connected === true;
  const isCloudEnabled = cloudStatus?.enabled === true;

  const uptimeSeconds = typeof (systemStatus as any)?.uptime_seconds === "number"
    ? (systemStatus as any).uptime_seconds as number
    : undefined;
  const formatUptime = (s: number) => {
    if (s < 60) return `${Math.floor(s)}s`;
    if (s < 3600) return `${Math.floor(s / 60)}m`;
    const h = Math.floor(s / 3600);
    const mn = Math.floor((s % 3600) / 60);
    return `${h}h ${mn}m`;
  };

  const iscEnabled = project.isc?.enabled === true;
  const iscPeerCount = (project.isc?.peers as unknown[])?.length ?? 0;
  const iscPatternCount = (project.isc?.shared_state as unknown[])?.length ?? 0;

  const activeTriggers = project.macros.flatMap(m =>
    (m.triggers ?? []).filter(t => t.enabled !== false).map(t => ({
      macroName: String(m.name),
      triggerType: String(t.type),
      detail: String(
        t.type === "schedule" ? t.cron ?? ""
          : t.type === "state_change" ? `${t.state_key ?? ""} ${t.state_operator ?? "any"}`
          : t.type === "event" ? t.event_pattern ?? ""
          : t.type === "startup" ? `delay ${t.delay_seconds ?? 0}s`
          : ""
      ),
    }))
  );

  const trackedVars = project.variables.filter(v => v.dashboard);

  // Snapshot of recent log entries (non-reactive to avoid rapid re-renders)
  const logEntries = useLogStore.getState().logEntries;
  const recentActivity = logEntries
    .filter(e => e.level !== "DEBUG")
    .slice(-15)
    .reverse();

  const cardStyle: React.CSSProperties = {
    background: "var(--bg-surface)",
    border: "1px solid var(--border-color)",
    borderRadius: "var(--border-radius)",
    padding: "var(--space-lg)",
  };

  const sectionTitle: React.CSSProperties = {
    fontSize: "var(--font-size-sm)",
    color: "var(--text-secondary)",
    textTransform: "uppercase",
    letterSpacing: "0.5px",
    fontWeight: 600,
    marginBottom: "var(--space-md)",
  };

  return (
    <ViewContainer title="Dashboard">
      {fetchError && (
        <div style={{ background: "var(--status-error-bg, #3a1a1a)", color: "var(--status-error, #ff6b6b)", padding: "var(--space-sm) var(--space-md)", borderRadius: "var(--radius-md)", marginBottom: "var(--space-md)", fontSize: "var(--font-sm)" }}>
          {fetchError}
        </div>
      )}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 320px", gap: "var(--space-xl)", maxWidth: 1100 }}>
        {/* Main column */}
        <div>
          {/* Top bar */}
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "var(--space-lg)" }}>
            <div>
              <div style={{ fontSize: "var(--font-size-lg)", fontWeight: 600 }}>{String(project.project.name)}</div>
              <div style={{ fontSize: "var(--font-size-sm)", color: "var(--text-muted)" }}>
                {"OpenAVC v" + String(systemStatus?.version ?? "")}
              </div>
            </div>
            {uptimeSeconds != null && (
              <div style={{ display: "flex", alignItems: "center", gap: "var(--space-xs)", color: "var(--text-muted)", fontSize: "var(--font-size-sm)" }}>
                <Clock size={14} />
                <span>{"Uptime: " + formatUptime(uptimeSeconds)}</span>
              </div>
            )}
          </div>

          {/* Summary row */}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: "var(--space-md)", marginBottom: "var(--space-xl)" }}>
            <div style={cardStyle}>
              <div style={{ display: "flex", alignItems: "center", gap: "var(--space-xs)", marginBottom: "var(--space-xs)" }}>
                <Cpu size={14} style={{ color: "var(--accent)" }} />
                <span style={{ fontSize: "var(--font-size-sm)", color: "var(--text-secondary)" }}>Devices</span>
              </div>
              <div style={{ fontSize: "var(--font-size-xl)", fontWeight: 700 }}>{String(connectedCount) + "/" + String(enabledCount)}</div>
            </div>
            <div style={cardStyle}>
              <div style={{ display: "flex", alignItems: "center", gap: "var(--space-xs)", marginBottom: "var(--space-xs)" }}>
                <Zap size={14} style={{ color: "#f59e0b" }} />
                <span style={{ fontSize: "var(--font-size-sm)", color: "var(--text-secondary)" }}>Triggers</span>
              </div>
              <div style={{ fontSize: "var(--font-size-xl)", fontWeight: 700 }}>{String(triggerCount)}</div>
            </div>
            <div style={cardStyle}>
              <div style={{ display: "flex", alignItems: "center", gap: "var(--space-xs)", marginBottom: "var(--space-xs)" }}>
                <FileCode size={14} style={{ color: "#8b5cf6" }} />
                <span style={{ fontSize: "var(--font-size-sm)", color: "var(--text-secondary)" }}>Scripts</span>
              </div>
              <div style={{ fontSize: "var(--font-size-xl)", fontWeight: 700 }}>{String(scriptCount)}</div>
            </div>
            <div style={cardStyle}>
              <div style={{ display: "flex", alignItems: "center", gap: "var(--space-xs)", marginBottom: "var(--space-xs)" }}>
                <Cloud size={14} style={{ color: isCloudConnected ? "var(--color-success)" : "var(--text-muted)" }} />
                <span style={{ fontSize: "var(--font-size-sm)", color: "var(--text-secondary)" }}>Cloud</span>
              </div>
              <div style={{ fontSize: "var(--font-size-xl)", fontWeight: 700, color: !isCloudEnabled ? "var(--text-muted)" : isCloudConnected ? "var(--color-success)" : "var(--color-error)" }}>
                {!isCloudEnabled ? "—" : isCloudConnected ? "Online" : "Offline"}
              </div>
            </div>
          </div>

          {/* Update available card */}
          {!!liveState["system.update_available"] && (
            <div
              onClick={() => useNavigationStore.getState().navigateTo("updates")}
              style={{
                ...cardStyle,
                marginBottom: "var(--space-xl)",
                borderColor: "rgba(138,180,147,0.3)",
                display: "flex",
                alignItems: "center",
                gap: "var(--space-md)",
                cursor: "pointer",
              }}
              onMouseEnter={(e) => (e.currentTarget.style.background = "var(--bg-hover)")}
              onMouseLeave={(e) => (e.currentTarget.style.background = "var(--bg-surface)")}
            >
              <ArrowUpCircle size={20} style={{ color: "var(--accent)", flexShrink: 0 }} />
              <div style={{ flex: 1 }}>
                <div style={{ fontWeight: 500, fontSize: "var(--font-size-sm)" }}>
                  {"OpenAVC v" + String(liveState["system.update_available"]) + " available"}
                </div>
                <div style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 2 }}>
                  View changelog and install
                </div>
              </div>
              <ArrowRight size={14} style={{ color: "var(--text-muted)", flexShrink: 0 }} />
            </div>
          )}

          {/* Getting Started — shown when project is empty */}
          {devices.length === 0 && project.macros.length === 0 && (
            <div style={{ ...cardStyle, marginBottom: "var(--space-xl)", borderColor: "var(--accent-bg)", background: "var(--color-info-bg)" }}>
              <div style={{ fontSize: "var(--font-size-md)", fontWeight: 600, marginBottom: "var(--space-md)" }}>
                Getting Started
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-sm)" }}>
                {[
                  { step: "1", label: "Add a device", desc: "Connect to a projector, display, switcher, or audio processor.", view: "devices" as const },
                  { step: "2", label: "Create a macro", desc: "Build a sequence of commands — power on, switch inputs, set levels.", view: "macros" as const },
                  { step: "3", label: "Build your panel", desc: "Design a touch panel UI with buttons, sliders, and status indicators.", view: "ui-builder" as const },
                ].map((item) => (
                  <div
                    key={item.step}
                    onClick={() => useNavigationStore.getState().navigateTo(item.view)}
                    style={{
                      display: "flex", alignItems: "center", gap: "var(--space-md)",
                      padding: "var(--space-sm) var(--space-md)", borderRadius: "var(--border-radius)",
                      cursor: "pointer", background: "var(--bg-surface)",
                    }}
                    onMouseEnter={(e) => (e.currentTarget.style.background = "var(--bg-hover)")}
                    onMouseLeave={(e) => (e.currentTarget.style.background = "var(--bg-surface)")}
                  >
                    <div style={{ width: 24, height: 24, borderRadius: "50%", background: "var(--accent-bg)", color: "#fff", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 12, fontWeight: 700, flexShrink: 0 }}>
                      {item.step}
                    </div>
                    <div style={{ flex: 1 }}>
                      <div style={{ fontWeight: 500, fontSize: "var(--font-size-sm)" }}>{item.label}</div>
                      <div style={{ fontSize: 11, color: "var(--text-muted)" }}>{item.desc}</div>
                    </div>
                    <ArrowRight size={14} style={{ color: "var(--text-muted)", flexShrink: 0 }} />
                  </div>
                ))}
              </div>
              <div style={{ marginTop: "var(--space-md)", fontSize: 12, color: "var(--text-muted)" }}>
                New to OpenAVC?{" "}
                <a href="https://docs.openavc.com/getting-started" target="_blank" rel="noopener noreferrer" style={{ color: "var(--accent)" }}>
                  Read the Getting Started guide
                </a>
              </div>
            </div>
          )}

          {/* Disconnected warning */}
          {disconnectedCount > 0 && (
            <div style={{
              ...cardStyle,
              borderColor: "rgba(239,68,68,0.3)",
              display: "flex",
              alignItems: "center",
              gap: "var(--space-md)",
              marginBottom: "var(--space-xl)",
            }}>
              <AlertTriangle size={18} style={{ color: "var(--color-error)", flexShrink: 0 }} />
              <div>
                <div style={{ fontWeight: 500, fontSize: "var(--font-size-sm)" }}>
                  {String(disconnectedCount) + " device" + (disconnectedCount !== 1 ? "s" : "") + " disconnected"}
                </div>
                <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>
                  {devices.filter(d => d.enabled !== false && liveState[`device.${d.id}.connected`] !== true).map(d => String(d.name)).join(", ")}
                </div>
              </div>
            </div>
          )}

          {/* Device grid */}
          <div style={{ marginBottom: "var(--space-xl)" }}>
            <h3 style={sectionTitle}>Devices</h3>
            {devices.length === 0 ? (
              <div style={{ ...cardStyle, color: "var(--text-muted)", textAlign: "center", fontSize: "var(--font-size-sm)" }}>
                No devices configured yet.
              </div>
            ) : (
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(180px, 1fr))", gap: "var(--space-sm)" }}>
                {devices.map(dev => {
                  const isEnabled = dev.enabled !== false;
                  const isConnected = liveState[`device.${dev.id}.connected`] === true;
                  return (
                    <div
                      key={dev.id}
                      style={{
                        ...cardStyle,
                        padding: "var(--space-sm) var(--space-md)",
                        opacity: isEnabled ? 1 : 0.5,
                        borderColor: !isEnabled ? "var(--border-color)" : isConnected ? "rgba(76,175,80,0.3)" : "rgba(239,68,68,0.3)",
                      }}
                    >
                      <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)" }}>
                        <DeviceStatusDot connected={isConnected} />
                        <div style={{ minWidth: 0 }}>
                          <div style={{ fontWeight: 500, fontSize: "var(--font-size-sm)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                            {String(dev.name)}
                          </div>
                          <div style={{ fontSize: 11, color: "var(--text-muted)" }}>
                            {String(dev.driver)}{!isEnabled ? " (disabled)" : ""}
                          </div>
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          {/* Active Triggers */}
          {activeTriggers.length > 0 && (
            <div style={{ marginBottom: "var(--space-xl)" }}>
              <h3 style={sectionTitle}>Active Triggers</h3>
              <div style={{ ...cardStyle, padding: 0, overflow: "hidden" }}>
                {activeTriggers.map((t, i) => (
                  <div
                    key={i}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: "var(--space-sm)",
                      padding: "var(--space-sm) var(--space-md)",
                      borderTop: i > 0 ? "1px solid var(--border-color)" : undefined,
                      fontSize: "var(--font-size-sm)",
                    }}
                  >
                    <span style={{
                      fontSize: 10,
                      fontWeight: 600,
                      color: "var(--text-muted)",
                      background: "var(--bg-hover)",
                      padding: "1px 6px",
                      borderRadius: 3,
                      textTransform: "uppercase" as const,
                      letterSpacing: "0.5px",
                      flexShrink: 0,
                    }}>
                      {t.triggerType === "schedule" ? "cron" : t.triggerType === "state_change" ? "state" : t.triggerType}
                    </span>
                    <span style={{ fontWeight: 500 }}>{t.macroName}</span>
                    {t.detail && (
                      <code style={{ fontSize: 11, color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
                        {t.detail}
                      </code>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Right sidebar */}
        <div>
          {/* Panel Access */}
          <PanelAccessCard systemStatus={systemStatus} roomName={String(project.project.name ?? "")} />

          {/* Tracked Variables */}
          {trackedVars.length > 0 && (
            <div style={{ marginBottom: "var(--space-xl)" }}>
              <h3 style={sectionTitle}>Variables</h3>
              <div style={{ ...cardStyle, padding: 0, overflow: "hidden" }}>
                {trackedVars.map((v, i) => {
                  const live = liveState[`var.${v.id}`];
                  return (
                    <div
                      key={v.id}
                      style={{
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "space-between",
                        padding: "var(--space-sm) var(--space-md)",
                        borderTop: i > 0 ? "1px solid var(--border-color)" : undefined,
                      }}
                    >
                      <div>
                        <div style={{ fontSize: "var(--font-size-sm)", fontWeight: 500 }}>
                          {String(v.label || v.id)}
                        </div>
                        <code style={{ fontSize: 11, color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
                          {"var." + String(v.id)}
                        </code>
                      </div>
                      <div style={{
                        fontSize: "var(--font-size-sm)",
                        fontWeight: 600,
                        fontFamily: "var(--font-mono)",
                        color: live !== undefined ? "var(--text-primary)" : "var(--text-muted)",
                        background: "var(--bg-hover)",
                        padding: "2px 8px",
                        borderRadius: "var(--border-radius)",
                      }}>
                        {live !== undefined ? String(live) : "—"}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* ISC Status */}
          {iscEnabled && (
            <div style={{ marginBottom: "var(--space-xl)" }}>
              <h3 style={sectionTitle}>Inter-System</h3>
              <div style={{ ...cardStyle, fontSize: "var(--font-size-sm)" }}>
                <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)" }}>
                  <DeviceStatusDot connected={true} size={8} />
                  <span>ISC Enabled</span>
                </div>
                <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: "var(--space-xs)" }}>
                  {String(iscPeerCount) + " manual peer" + (iscPeerCount !== 1 ? "s" : "") + " · " + String(iscPatternCount) + " shared pattern" + (iscPatternCount !== 1 ? "s" : "")}
                </div>
              </div>
            </div>
          )}

          {/* Recent Activity */}
          <div>
            <h3 style={sectionTitle}>Recent Activity</h3>
            <div style={{ ...cardStyle, padding: 0, overflow: "hidden", maxHeight: 400, overflowY: "auto" }}>
              {recentActivity.length === 0 ? (
                <div style={{ padding: "var(--space-lg)", color: "var(--text-muted)", textAlign: "center", fontSize: "var(--font-size-sm)" }}>
                  No activity yet. Events will appear here as the system runs.
                </div>
              ) : (
                recentActivity.map((e, i) => (
                  <div
                    key={i}
                    style={{
                      padding: "var(--space-xs) var(--space-md)",
                      borderTop: i > 0 ? "1px solid var(--border-color)" : undefined,
                      fontSize: 12,
                    }}
                  >
                    <div style={{ display: "flex", alignItems: "baseline", gap: "var(--space-sm)" }}>
                      <span style={{ color: "var(--text-muted)", fontFamily: "var(--font-mono)", fontSize: 11, flexShrink: 0 }}>
                        {new Date(e.timestamp * 1000).toLocaleTimeString(undefined, { hour12: false })}
                      </span>
                      {e.level === "ERROR" && (
                        <span style={{ color: "var(--color-error)", fontWeight: 600 }}>{"ERROR"}</span>
                      )}
                      {e.level === "WARNING" && (
                        <span style={{ color: "#f59e0b" }}>{"WARN"}</span>
                      )}
                      <span style={{ color: "var(--text-primary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {String(e.message)}
                      </span>
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>
        </div>

        {/* Plugin Status Cards */}
        <StatusCardSlot />
      </div>
    </ViewContainer>
  );
}
