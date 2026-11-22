import { useEffect, useState, type ReactNode } from "react";
import { AlertTriangle, CheckCircle2, Download, ExternalLink, RefreshCw } from "lucide-react";
import { Dialog } from "./Dialog";
import { saveCaCertificate } from "../system/caCertificate";
import {
  HANDOFF_ATTEMPTS,
  landingUrl,
  probeInit,
  statusProbeUrl,
  waitOrigins,
} from "./restartPollHelpers";
import { waitForRestart, type RestartWaitView } from "./restartWait";
import { askStartupStatus, type StartupStatus } from "../../api/startupStatus";
import { getTunnelPrefix } from "../../api/base";
import * as api from "../../api/restClient";
import { ApiError, parseApiError } from "../../api/errors";

type Phase =
  | "asking" | "waiting" | "starting" | "failed"
  | "cert-error" | "success" | "timeout" | "error" | "handoff";

/** A failed start the dialog shows: its reason, and where to open the
 *  server's page to press Start again. */
export interface Failure {
  status: StartupStatus;
  page: string;
}

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
  /** More addresses to wait at after `targetUrl`, best first. When the
   *  browser is cut off (`handoff`), the only ones. */
  alsoTry?: string[];
  /** Set when a change of who can connect cuts this browser off. `lines` is
   *  shown while the server restarts; `saved` replaces the 60-second failure
   *  once nothing answers here, because nothing is wrong. */
  handoff?: { lines: ReactNode; saved: ReactNode };
}

const POLL_INTERVAL_MS = 1000;
// Rounds without hearing the new start before the dialog says the server did
// not come back: about a minute.
const MAX_POLL_ATTEMPTS = 60;

/** What a refused restart leaves to wait for: a restart or a stop already
 *  under way (409) ends this server like the one asked for. */
function refusalToShow(e: unknown): string | null {
  return e instanceof ApiError && e.status !== 409 ? parseApiError(e) : null;
}

export function RestartProgressDialog({
  targetUrl,
  isProtocolSwitch,
  expectsNewCert,
  onClose,
  alsoTry = [],
  handoff,
}: RestartProgressDialogProps) {
  // The effect re-runs only when the addresses change, not on every render
  // of a parent that builds a fresh array.
  const alsoTryKey = alsoTry.join(" ");
  const cutOff = !!handoff;
  const [phase, setPhase] = useState<Phase>("asking");
  const [errorDetail, setErrorDetail] = useState<string>("");
  const [startingAgain, setStartingAgain] = useState(false);
  const [failure, setFailure] = useState<Failure | null>(null);

  useEffect(() => {
    // Local to THIS effect run — a superseded run (targetUrl/expectsNewCert
    // changed) keeps its own `cancelled=true` and can't be un-cancelled by the
    // next run, so a stale run can't re-POST restart or navigate to a stale URL.
    let cancelled = false;
    const here = { origin: window.location.origin, prefix: getTunnelPrefix() };
    const targetPath = new URL(targetUrl).pathname; // "/programmer"

    const show = (view: RestartWaitView) => {
      if (view.kind === "failed") {
        setFailure({ status: view.status, page: landingUrl(targetPath, view.origin, here) });
        setPhase("failed");
        return;
      }
      setStartingAgain(view.kind === "starting" && view.again);
      setPhase(view.kind);
    };

    const run = async () => {
      // Step 1: ask for the restart. A refusal says why and ends here. An
      // answer that never arrived is ambiguous (the server may have dropped
      // the connection on its way down), so the wait decides.
      let answerLost = false;
      try {
        await api.restartSystem("graceful");
      } catch (e) {
        if (cancelled) return;
        const refusal = refusalToShow(e);
        if (refusal !== null) {
          setErrorDetail(refusal);
          setPhase("error");
          return;
        }
        answerLost = !(e instanceof ApiError);
      }
      if (cancelled) return;

      // Step 2: wait where the server comes back: the expected public
      // address, and the other addresses a change of who can connect makes
      // reachable. Never the page's own as a fallback: a second listener
      // there might be another server. Cut off, the page waits only at the
      // new choice's own addresses (alsoTry); targetUrl is then just the
      // address the messages name.
      setPhase("waiting");
      const more = alsoTryKey ? alsoTryKey.split(" ").map((u) => new URL(u).origin) : [];
      const origins = waitOrigins(new URL(targetUrl).origin, more, cutOff);
      // A browser cut off by the change tries the new choice's own addresses
      // for a while (a computer on both networks reaches them), then says
      // where to open the Programmer. With none to try, it says so now.
      const end = await waitForRestart({
        origins,
        attempts: cutOff ? (origins.length ? HANDOFF_ATTEMPTS : 0) : MAX_POLL_ATTEMPTS,
        expectsNewCert,
        askedMayStillAnswer: answerLost,
        probe: (origin) => askStartupStatus(statusProbeUrl(origin, here), probeInit()),
        pause: () => new Promise((r) => setTimeout(r, POLL_INTERVAL_MS)),
        cancelled: () => cancelled,
        show,
      });
      if (cancelled || end.kind === "cancelled") return;
      if (end.kind === "back") {
        setPhase("success");
        const landing = landingUrl(targetPath, end.origin, here);
        // Small grace period so the user reads the "Reconnected" state
        // before the browser navigates.
        setTimeout(() => {
          if (!cancelled) window.location.assign(landing);
        }, 400);
        return;
      }
      if (end.kind === "cert-error") setPhase("cert-error");
      else setPhase(cutOff ? "handoff" : "timeout");
    };

    void run();
    return () => {
      cancelled = true;
    };
    // cutOff is fixed for the dialog's life.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [targetUrl, expectsNewCert, alsoTryKey]);

  const downloadCertThenOpen = async () => {
    try {
      // Download CA from the *current* origin (still HTTP, still reachable from
      // the splash page that's still in the user's browser tab).
      await saveCaCertificate();
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
        {isProtocolSwitch && phase === "asking" && (
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

        {handoff && isWaiting(phase) && (
          <div data-testid="restart-handoff-lines" style={{ marginBottom: "var(--space-md)" }}>
            {handoff.lines}
          </div>
        )}

        {isWaiting(phase) && (
          <div style={{ display: "flex", alignItems: "center", gap: "var(--space-md)" }}>
            <RefreshCw size={20} style={{ animation: "spin 1s linear infinite" }} />
            <span data-testid="restart-wait-line">
              {phase === "asking" && "Asking the server to restart..."}
              {phase === "waiting" && (cutOff
                ? "Checking whether this browser can reach the new address..."
                : "Waiting for the server to come back...")}
              {phase === "starting" && (startingAgain ? "Starting again..." : "OpenAVC is starting...")}
            </span>
          </div>
        )}

        {phase === "failed" && failure && (
          <FailedStart failure={failure} onClose={onClose} />
        )}

        {phase === "success" && (
          <div style={{ color: "var(--accent-color, #8AB493)" }}>
            Reconnected. Loading the new page...
          </div>
        )}

        {phase === "handoff" && handoff && (
          <div data-testid="restart-handoff-saved">
            <div style={{ display: "flex", gap: "var(--space-sm)", marginBottom: "var(--space-md)" }}>
              <CheckCircle2 size={18} style={{ color: "var(--accent-color, #8AB493)", flexShrink: 0, marginTop: 2 }} />
              <div>{handoff.saved}</div>
            </div>
            <button onClick={onClose} style={btnSecondary}>
              Close
            </button>
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
                <code>startup-error.json</code> in the <code>status</code>{" "}
                folder of the data directory.
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

function isWaiting(phase: Phase): boolean {
  return phase === "asking" || phase === "waiting" || phase === "starting";
}

/** What a failed start leaves to do, as the server's own page says it. */
function nextStep(status: StartupStatus): string | null {
  if (status.held) return "Fix the problem, then open the server's page and press Start again.";
  if (status.retryIn !== null) {
    return `Trying again in ${status.retryIn} ${status.retryIn === 1 ? "second" : "seconds"}.`;
  }
  return null;
}

/** A start that failed: the server's reason and what happens next. The
 *  wait goes on behind it, so a start that follows still reconnects. The
 *  Updates page's install dialog says a new version's failed start with it
 *  too. */
export function FailedStart({ failure, onClose }: { failure: Failure; onClose: () => void }) {
  const next = nextStep(failure.status);
  return (
    <div data-testid="restart-start-failed">
      <div style={{ display: "flex", gap: "var(--space-sm)", marginBottom: "var(--space-md)" }}>
        <AlertTriangle size={18} style={{ color: "rgb(244, 67, 54)", flexShrink: 0, marginTop: 2 }} />
        <div>
          <div style={{ fontWeight: 600 }}>OpenAVC could not start</div>
          {failure.status.error && (
            <div data-testid="restart-start-error" style={{ marginTop: "var(--space-xs)", whiteSpace: "pre-line" }}>
              {failure.status.error}
            </div>
          )}
          {next && (
            <div style={{ marginTop: "var(--space-sm)", color: "var(--text-secondary)" }}>{next}</div>
          )}
        </div>
      </div>
      <div style={{ display: "flex", gap: "var(--space-sm)", flexWrap: "wrap" }}>
        <button onClick={() => window.location.assign(failure.page)} style={btnSecondary}>
          <ExternalLink size={14} />
          <span>Open the server's page</span>
        </button>
        <button onClick={onClose} style={btnSecondary}>
          Close
        </button>
      </div>
    </div>
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
