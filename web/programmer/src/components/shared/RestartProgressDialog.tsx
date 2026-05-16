import { useEffect, useRef, useState } from "react";
import { AlertTriangle, Download, ExternalLink, RefreshCw } from "lucide-react";
import { Dialog } from "./Dialog";
import * as api from "../../api/restClient";

type Phase = "starting" | "waiting" | "polling" | "cert-error" | "success" | "timeout" | "error";

interface RestartProgressDialogProps {
  /** Full URL the page should land on once the server is back. */
  targetUrl: string;
  /** True when the current page protocol differs from the post-restart protocol.
   *  Drives the pre-restart info line and the cert-error fallback. */
  isProtocolSwitch: boolean;
  /** True when the post-restart URL is https:// and the current page is http://.
   *  Used to interpret persistent fetch failures as a CA-not-yet-installed problem. */
  expectsNewCert: boolean;
  onClose: () => void;
}

const POLL_INTERVAL_MS = 1000;
const MAX_POLL_ATTEMPTS = 60;
// Once we've seen this many consecutive failures and the new URL is https://
// (page is http://), assume the browser is rejecting the new self-signed cert
// rather than the server still being down.
const CERT_ERROR_THRESHOLD = 5;
// Time the server's graceful-exit delay (2s) plus a small margin so the
// listener has actually released the port before polling begins.
const INITIAL_WAIT_MS = 3000;

export function RestartProgressDialog({
  targetUrl,
  isProtocolSwitch,
  expectsNewCert,
  onClose,
}: RestartProgressDialogProps) {
  const [phase, setPhase] = useState<Phase>("starting");
  const [errorDetail, setErrorDetail] = useState<string>("");
  const cancelled = useRef(false);

  useEffect(() => {
    cancelled.current = false;

    const run = async () => {
      // Step 1: trigger the restart. A throw here is ambiguous — the server
      // may have already started exiting and dropped the connection before
      // sending a response. Either way, polling is the source of truth, so
      // log and fall through rather than aborting on a transient fetch error.
      try {
        await api.restartSystem("graceful");
      } catch (e) {
        if (cancelled.current) return;
        // Keep the detail around in case polling never succeeds — we'll
        // surface it then. Don't switch phase yet.
        setErrorDetail(String(e));
      }
      if (cancelled.current) return;

      // Step 2: wait for the server to actually exit and its replacement to
      // bind. The server delays exit ~2s to flush logs; a 3s wait covers that
      // plus a small margin for port release.
      setPhase("waiting");
      await new Promise((r) => setTimeout(r, INITIAL_WAIT_MS));
      if (cancelled.current) return;

      // Step 3: poll until /api/health responds or we hit the cap.
      setPhase("polling");
      let consecutiveFailures = 0;
      for (let attempt = 0; attempt < MAX_POLL_ATTEMPTS; attempt++) {
        if (cancelled.current) return;
        try {
          // `cache: "no-store"` keeps stale 502/0 responses from the previous
          // boot out of the way. `mode: "cors"` makes the cert error surface
          // as a TypeError instead of an opaque success.
          const res = await fetch(`${targetUrl.replace(/\/$/, "")}/api/health`, {
            cache: "no-store",
            credentials: "omit",
          });
          if (res.ok) {
            setPhase("success");
            // Small grace period so the user reads the "Reconnected" state
            // before the browser navigates.
            setTimeout(() => {
              if (!cancelled.current) window.location.assign(targetUrl);
            }, 400);
            return;
          }
          consecutiveFailures = 0; // server is up but returning non-2xx; not a network error
        } catch {
          consecutiveFailures += 1;
          if (
            expectsNewCert &&
            consecutiveFailures >= CERT_ERROR_THRESHOLD
          ) {
            // Browser likely rejecting the new self-signed cert.
            setPhase("cert-error");
            return;
          }
        }
        await new Promise((r) => setTimeout(r, POLL_INTERVAL_MS));
      }
      if (!cancelled.current) setPhase("timeout");
    };

    void run();
    return () => {
      cancelled.current = true;
    };
  }, [targetUrl, expectsNewCert]);

  const downloadCertThenOpen = async () => {
    try {
      // Download CA from the *current* origin (still HTTP, still reachable from
      // the splash page that's still in the user's browser tab).
      const blob = await api.downloadCertificate();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "openavc-ca.crt";
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    } catch {
      // Best effort — if the CA isn't fetchable from the current page (e.g.,
      // the redirect listener already took over), fall through to opening the
      // target URL so the user can at least click through the warning.
    }
    window.open(targetUrl, "_blank", "noopener,noreferrer");
  };

  return (
    <Dialog title="Restarting OpenAVC" onClose={onClose}>
      <div style={{ fontSize: "var(--font-size-sm)", lineHeight: 1.5 }}>
        {isProtocolSwitch && phase === "starting" && (
          <div style={{ marginBottom: "var(--space-md)", color: "var(--text-secondary)" }}>
            After restart, this page will be at <code>{targetUrl}</code>.
            {expectsNewCert && (
              <>
                {" "}
                Your browser will show a certificate warning until you install
                the CA certificate.
              </>
            )}
          </div>
        )}

        {(phase === "starting" || phase === "waiting" || phase === "polling") && (
          <div style={{ display: "flex", alignItems: "center", gap: "var(--space-md)" }}>
            <RefreshCw size={20} style={{ animation: "spin 1s linear infinite" }} />
            <span>
              {phase === "starting" && "Asking the server to restart..."}
              {phase === "waiting" && "Server is restarting. Reconnecting..."}
              {phase === "polling" && "Waiting for the server to come back..."}
            </span>
          </div>
        )}

        {phase === "success" && (
          <div style={{ color: "var(--accent-color, #8AB493)" }}>
            Reconnected. Loading the new page...
          </div>
        )}

        {phase === "cert-error" && (
          <div>
            <div style={{ display: "flex", gap: "var(--space-sm)", marginBottom: "var(--space-md)" }}>
              <AlertTriangle size={18} style={{ color: "rgb(255, 152, 0)", flexShrink: 0, marginTop: 2 }} />
              <span>
                The server is back, but your browser doesn't trust the new
                HTTPS certificate yet. Download and install the CA certificate,
                then open the new URL.
              </span>
            </div>
            <div style={{ display: "flex", gap: "var(--space-sm)", flexWrap: "wrap" }}>
              <button
                onClick={downloadCertThenOpen}
                style={btnPrimary}
              >
                <Download size={14} />
                <span>Download CA + Open new URL</span>
              </button>
              <button onClick={onClose} style={btnSecondary}>
                Close
              </button>
            </div>
            <div style={{ marginTop: "var(--space-md)", fontSize: 12, color: "var(--text-muted)" }}>
              New URL: <code>{targetUrl}</code>
            </div>
          </div>
        )}

        {phase === "timeout" && (
          <div>
            <div style={{ display: "flex", gap: "var(--space-sm)", marginBottom: "var(--space-md)" }}>
              <AlertTriangle size={18} style={{ color: "rgb(244, 67, 54)", flexShrink: 0, marginTop: 2 }} />
              <span>
                The server didn't come back within 60 seconds. It may have
                refused the new configuration. Check the service status
                (Windows tray, <code>systemctl status openavc</code>, or
                <code>docker logs openavc</code>) and look for{" "}
                <code>startup-error.json</code> in the data directory.
              </span>
            </div>
            <div style={{ display: "flex", gap: "var(--space-sm)" }}>
              <button onClick={() => window.location.reload()} style={btnSecondary}>
                <RefreshCw size={14} />
                <span>Reload this page</span>
              </button>
              <button onClick={() => window.open(targetUrl, "_blank")} style={btnSecondary}>
                <ExternalLink size={14} />
                <span>Try new URL</span>
              </button>
              <button onClick={onClose} style={btnSecondary}>
                Close
              </button>
            </div>
          </div>
        )}

        {phase === "error" && (
          <div>
            <div style={{ display: "flex", gap: "var(--space-sm)", marginBottom: "var(--space-md)" }}>
              <AlertTriangle size={18} style={{ color: "rgb(244, 67, 54)", flexShrink: 0, marginTop: 2 }} />
              <span>
                Failed to ask the server to restart: <code>{errorDetail}</code>
              </span>
            </div>
            <button onClick={onClose} style={btnSecondary}>
              Close
            </button>
          </div>
        )}
      </div>
    </Dialog>
  );
}

const btnPrimary: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: "var(--space-xs)",
  padding: "var(--space-sm) var(--space-md)",
  background: "var(--accent-color, #8AB493)",
  color: "#fff",
  border: "none",
  borderRadius: "var(--border-radius)",
  cursor: "pointer",
  fontSize: "var(--font-size-sm)",
  fontWeight: 500,
};

const btnSecondary: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: "var(--space-xs)",
  padding: "var(--space-sm) var(--space-md)",
  background: "transparent",
  color: "var(--text-primary)",
  border: "1px solid var(--border-color)",
  borderRadius: "var(--border-radius)",
  cursor: "pointer",
  fontSize: "var(--font-size-sm)",
};
